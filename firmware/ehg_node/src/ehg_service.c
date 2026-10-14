/*
 * ehg_service.c - GATT service, packetiser, history + resend.
 *
 * Data path: replay_ring (frames) -> tx_thread batches CONFIG_EHG_FRAMES_PER_PACKET frames
 * into one packet (header per ehg_packet.h) -> history[seq % N] -> bt_gatt_notify.
 *
 * Notifying from this dedicated thread blocks when BLE TX buffers are exhausted
 * (zephyr/subsys/bluetooth/host/att.c), so a slow link backs up into replay_ring and is
 * counted there as overflow rather than dropped silently inside the stack.
 *
 * PURPOSE: Turns the stream of frames into numbered radio packets, sends them to the laptop,
 *   and re-sends any the laptop reports missing.
 * HOW: (1) Declares a Bluetooth service with two slots: "data" (board pushes packets out =
 *   notify) and "control" (laptop writes START / STOP / RESEND commands). (2) A thread
 *   (tx_thread) collects N frames, adds the 20-byte header, gives it the next packet number
 *   (seq), keeps a copy in a history list, and sends it. (3) On RESEND, copies are looked up in
 *   history and sent again with the "retransmission" flag set.
 * ROLE IN THESIS: Gantt 1c (GATT service) and 1d (sequence numbers, timestamps). Batching
 *   trades latency for efficiency (H1); numbering + history/resend is the loss-recovery
 *   mechanism (H2). The counters printed by ehg_service_log_stats() show where any loss happened.
 * BEFORE -> AFTER: Frames come from replay.c via replay_ring. Packet layout from ehg_packet.h.
 *   Connection handed in by main.c. Packets go over the air to gateway/gateway.py, which
 *   decodes them with gateway/packet.py and sends RESEND commands back.
 */
#include "ehg_service.h"

#include <string.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/byteorder.h>

#include "ehg_packet.h"
#include "replay.h"
#include "replay_data.h" /* constants only (REPLAY_DEFINE_SAMPLES not set) */

LOG_MODULE_REGISTER(ehg_svc, LOG_LEVEL_INF);

#define FRAMES CONFIG_EHG_FRAMES_PER_PACKET
#define PKT_LEN (sizeof(struct ehg_pkt_hdr) + FRAMES * REPLAY_N_CH * sizeof(int16_t))
BUILD_ASSERT(PKT_LEN <= 244, "packet exceeds MTU 247 payload - lower CONFIG_EHG_FRAMES_PER_PACKET");
BUILD_ASSERT(FRAMES <= UINT8_MAX);

static struct bt_uuid_128 svc_uuid = BT_UUID_INIT_128(EHG_UUID_BASE(1));
static struct bt_uuid_128 data_uuid = BT_UUID_INIT_128(EHG_UUID_BASE(2));
static struct bt_uuid_128 ctrl_uuid = BT_UUID_INIT_128(EHG_UUID_BASE(3));

/* A pending resend request from the gateway: "send packets seq .. seq+count-1 again". */
struct resend_req {
	uint32_t seq;
	uint16_t count;
};
K_MSGQ_DEFINE(resend_q, sizeof(struct resend_req), 16, 4);

/* History: copies of the last CONFIG_EHG_HISTORY_PACKETS sent packets, slot = seq mod size.
 * Older packets are overwritten, so very late resend requests can no longer be served. */
static struct hist_entry {
	uint32_t seq;
	bool valid;
	uint8_t buf[PKT_LEN];
} history[CONFIG_EHG_HISTORY_PACKETS];

static struct bt_conn *conn;
static K_MUTEX_DEFINE(conn_lock);
static uint32_t n_sent, n_resent, n_resend_miss, n_notify_err;

/* Runs when the gateway writes to the control slot. Decodes the 1-byte command; RESEND is
 * only queued here (sending happens in tx_thread so this Bluetooth callback stays quick). */
static ssize_t ctrl_write(struct bt_conn *c, const struct bt_gatt_attr *attr, const void *buf,
			  uint16_t len, uint16_t offset, uint8_t flags)
{
	const uint8_t *b = buf;

	if (offset != 0 || len < 1) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}
	switch (b[0]) {
	case EHG_OP_START:
		replay_start();
		break;
	case EHG_OP_STOP:
		replay_stop();
		break;
	case EHG_OP_RESEND: {
		if (len != 7) {
			return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
		}
		struct resend_req r = {.seq = sys_get_le32(&b[1]), .count = sys_get_le16(&b[5])};

		if (k_msgq_put(&resend_q, &r, K_NO_WAIT) != 0) {
			n_resend_miss += r.count;
		}
		break;
	}
	default:
		return BT_GATT_ERR(BT_ATT_ERR_NOT_SUPPORTED);
	}
	return len;
}

/* The service table: data slot (notify) + its on/off switch (CCC) + control slot (write). */
BT_GATT_SERVICE_DEFINE(ehg_svc,
	BT_GATT_PRIMARY_SERVICE(&svc_uuid.uuid),
	BT_GATT_CHARACTERISTIC(&data_uuid.uuid, BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_NONE, NULL, NULL, NULL),
	BT_GATT_CCC(NULL, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	BT_GATT_CHARACTERISTIC(&ctrl_uuid.uuid, BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP,
			       BT_GATT_PERM_WRITE, NULL, ctrl_write, NULL),
);

#define DATA_ATTR (&ehg_svc.attrs[2])

/* Remember (or forget, when c is NULL) the current connection. Losing it stops replay. */
void ehg_service_set_conn(struct bt_conn *c)
{
	k_mutex_lock(&conn_lock, K_FOREVER);
	if (conn) {
		bt_conn_unref(conn);
	}
	conn = c ? bt_conn_ref(c) : NULL;
	k_mutex_unlock(&conn_lock);
	if (!c) {
		replay_stop();
	}
}

/* Push one packet to the laptop, if connected and subscribed. Returns 0 on success. */
static int send(const uint8_t *buf, uint16_t len)
{
	int err = -ENOTCONN;

	k_mutex_lock(&conn_lock, K_FOREVER);
	struct bt_conn *c = conn ? bt_conn_ref(conn) : NULL;

	k_mutex_unlock(&conn_lock);
	if (!c) {
		return err;
	}
	/* The central may not have finished the MTU exchange yet: wait rather than drop. */
	for (int i = 0; i < 100 && bt_gatt_get_mtu(c) - 3 < len; i++) {
		k_sleep(K_MSEC(10));
	}
	if (bt_gatt_is_subscribed(c, DATA_ATTR, BT_GATT_CCC_NOTIFY)) {
		err = bt_gatt_notify(c, DATA_ATTR, buf, len); /* copies buf; blocks for TX buffers */
	}
	bt_conn_unref(c);
	if (err) {
		n_notify_err++;
	}
	return err;
}

/* Handle all queued RESEND requests: re-send each stored copy with the retransmission flag. */
static void serve_resends(void)
{
	struct resend_req r;
	uint8_t tmp[PKT_LEN];

	while (k_msgq_get(&resend_q, &r, K_NO_WAIT) == 0) {
		for (uint32_t s = r.seq; s != r.seq + r.count; s++) {
			const struct hist_entry *h = &history[s % CONFIG_EHG_HISTORY_PACKETS];

			if (!h->valid || h->seq != s) {
				n_resend_miss++; /* aged out of history: gateway logs it as lost */
				continue;
			}
			memcpy(tmp, h->buf, PKT_LEN);
			((struct ehg_pkt_hdr *)tmp)->flags |= EHG_FLAG_RETRANS;
			if (send(tmp, PKT_LEN) == 0) {
				n_resent++;
			}
		}
	}
}

/* The packetiser. Loops forever: serve resends, take one frame, append it to the packet
 * being built; when the packet holds FRAMES frames, save it to history and send it. */
static void tx_thread(void *a, void *b, void *c)
{
	uint8_t pkt[PKT_LEN];
	struct ehg_pkt_hdr *hdr = (struct ehg_pkt_hdr *)pkt;
	int16_t *samples = (int16_t *)(pkt + sizeof(*hdr));
	struct ehg_frame f;
	uint32_t seq = 0;
	uint8_t nf = 0;

	for (;;) {
		serve_resends();
		if (k_msgq_get(&replay_ring, &f, K_MSEC(10)) != 0) {
			continue;
		}
		if (f.frame_idx == 0) { /* frame 0 marks a new stream (START) */
			seq = 0;
			nf = 0;
			memset(history, 0, sizeof(history));
		}
		if (nf == 0) { /* first frame of a new packet: header takes its index and timestamp */
			*hdr = (struct ehg_pkt_hdr){
				.version = EHG_PKT_VERSION,
				.n_ch = REPLAY_N_CH,
				.fs_hz = REPLAY_FS_HZ,
				.seq = seq,
				.frame_idx = f.frame_idx,
				.t_inject = f.tick,
				.n_frames = FRAMES,
				.flags = 0,
				.record_id = REPLAY_RECORD_ID,
			};
		}
		memcpy(&samples[nf * REPLAY_N_CH], f.s, REPLAY_N_CH * sizeof(int16_t));
		if (++nf < FRAMES) {
			continue;
		}
		struct hist_entry *h = &history[seq % CONFIG_EHG_HISTORY_PACKETS];

		h->seq = seq;
		h->valid = true;
		memcpy(h->buf, pkt, PKT_LEN);
		if (send(pkt, PKT_LEN) == 0) {
			n_sent++;
		}
		seq++;
		nf = 0;
	}
}

K_THREAD_DEFINE(tx_tid, 2048, tx_thread, NULL, NULL, NULL, 5, 0, 0);

/* Print health counters to the serial log: sent, resent, resends that came too late,
 * send errors, frames dropped at the ring buffer, and worst injection lateness. */
void ehg_service_log_stats(void)
{
	LOG_INF("sent=%u resent=%u resend_miss=%u notify_err=%u ring_overflow=%u max_inject_late=%u us",
		n_sent, n_resent, n_resend_miss, n_notify_err, replay_overflows(),
		(uint32_t)k_ticks_to_us_ceil32(replay_max_late_ticks()));
}
