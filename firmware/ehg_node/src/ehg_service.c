/*
 * ehg_service.c - packs frames into numbered packets, sends them over Bluetooth, re-sends lost ones.
 *
 * PURPOSE: Frames -> numbered radio packets -> laptop. Re-sends any packet the laptop says it missed.
 * INPUT: frames from the queue replay_ring (replay.c).
 *   Commands the laptop (gateway/gateway.py) writes: 01 START, 02 STOP, 03 RESEND <seq> <count>.
 *   e.g. frame from the queue: {tick=98304, frame_idx=0, s={1093, 171, -748}}
 *   e.g. command bytes (hex):  01                    START
 *                              02                    STOP
 *                              03 05 00 00 00 02 00  RESEND seq 5 (4 bytes), count 2 (2 bytes) = 5 and 6
 *   Anything else -> error "not supported"; RESEND not exactly 7 bytes -> error "invalid length".
 * OUTPUT: 1 packet per Bluetooth message (layout: ehg_packet.h). Re-sent copies have flags bit0 = 1.
 *   e.g. 1st packet after START, 82 bytes (real tpehg1007 frames 0-9; checked with gateway/packet.py):
 *     header  02 | 03 | 14 00 | 00 00 00 00 | 00 00 00 00 | 00 80 01 00 | 0a | 00 | 00 00
 *             ver  n_ch fs=20  seq=0         frame_idx=0   t_inject=98304 10fr flags record_id=0
 *     samples 45 04 ab 00 14 fd | 79 04 b4 00 ea fc | ... (10 frames x 3 ch x 2 bytes)
 *             1093  171   -748  | 1145  180   -790  | ...
 *     crc     78 bb  (CRC-16 of the 80 bytes before it; recomputed when a resend sets the flag)
 *   e.g. the same header re-sent for seq 5: 02 03 14 00 05 00 00 00 32 00 00 00 00 c0 02 00 0a 01 00 00 ... de ff
 *     (frame_idx 50, t_inject 180224, flags 01 = re-sent)
 * HOW:
 *   1. Sets up a Bluetooth "service" with 2 slots: data (board -> laptop, pushed = "notify")
 *      and control (laptop -> board commands).
 *   2. tx_thread collects 10 frames (CONFIG_EHG_FRAMES_PER_PACKET), adds the 20-byte header, the CRC and the
 *      next packet number (seq), keeps a copy in history, sends.
 *   3. RESEND: finds the copy in history, sends it again with the "re-sent" flag.
 *   If the radio is busy, sending waits instead of dropping. The wait backs up into replay_ring,
 *   where any overflow is counted. So nothing is lost silently.
 * ROLE IN THESIS: Gantt 1c/1d. Batching = latency vs efficiency trade-off (H1).
 *   Numbering + history + resend = loss recovery (H2). Counters in the log show where loss happened.
 * BEFORE -> AFTER: Frames from replay.c. Connection from main.c. Packets -> gateway/gateway.py
 *   (decoded by gateway/packet.py), which sends RESEND back.
 * SEE: docs/firmware.md (overview + glossary), docs/interface-spec.md 3 (byte layout, commands).
  */
#include "ehg_service.h"

#include <string.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/crc.h>

#include "ehg_packet.h"
#include "replay.h"

LOG_MODULE_REGISTER(ehg_svc, LOG_LEVEL_INF);

#define HDR_LEN sizeof(struct ehg_pkt_hdr)
BUILD_ASSERT(CONFIG_EHG_FRAMES_PER_PACKET <= UINT8_MAX);

static struct bt_uuid_128 svc_uuid = BT_UUID_INIT_128(EHG_UUID_BASE(1));
static struct bt_uuid_128 data_uuid = BT_UUID_INIT_128(EHG_UUID_BASE(2));
static struct bt_uuid_128 ctrl_uuid = BT_UUID_INIT_128(EHG_UUID_BASE(3));

/* A resend request from the laptop: "send packets seq .. seq+count-1 again". */
struct resend_req {
	uint32_t seq;
	uint16_t count;
};
K_MSGQ_DEFINE(resend_q, sizeof(struct resend_req), 16, 4);

/* History: copies of the last 256 sent packets (CONFIG_EHG_HISTORY_PACKETS), slot = seq mod 256.
 * Older ones get overwritten, so a very late resend request can't be served (counted as resend_miss). */
static struct hist_entry {
	uint32_t seq;
	bool valid;
	uint8_t len;
	uint8_t buf[EHG_MAX_PKT];
} history[CONFIG_EHG_HISTORY_PACKETS];

static struct bt_conn *conn;
static K_MUTEX_DEFINE(conn_lock);
static uint32_t n_sent, n_resent, n_resend_miss, n_notify_err, n_offline;

/* Fault injection settings (ehg_service_set_faults) and what they did. Off at boot. */
static struct {
	uint32_t rng; /* xorshift32 state: same seed = same faults, so a run can be repeated */
	uint16_t p_gb, p_bg, corrupt, disc_s;
	bool bad; /* Gilbert state: in a loss burst */
} flt;
static uint32_t n_inj_drop, n_inj_corrupt, n_inj_disc;

static uint32_t rnd(void)
{
	flt.rng ^= flt.rng << 13;
	flt.rng ^= flt.rng >> 17;
	flt.rng ^= flt.rng << 5;
	return flt.rng;
}

/* Forced disconnect, every disc_s seconds while enabled: tests reconnect + resend from history. */
static void disc_work_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(disc_work, disc_work_handler);

static void disc_work_handler(struct k_work *work)
{
	if (flt.disc_s == 0) {
		return;
	}
	k_mutex_lock(&conn_lock, K_FOREVER);
	if (conn && bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN) == 0) {
		n_inj_disc++;
	}
	k_mutex_unlock(&conn_lock);
	k_work_reschedule(&disc_work, K_SECONDS(flt.disc_s));
}

void ehg_service_set_faults(uint32_t seed, uint16_t p_gb, uint16_t p_bg, uint16_t corrupt, uint16_t disc_s)
{
	flt.rng = seed ? seed : 1; /* xorshift must not start at 0 */
	flt.p_gb = p_gb;
	flt.p_bg = p_bg;
	flt.corrupt = corrupt;
	flt.disc_s = disc_s;
	flt.bad = false;
	n_inj_drop = n_inj_corrupt = n_inj_disc = 0;
	if (disc_s) {
		k_work_reschedule(&disc_work, K_SECONDS(disc_s));
	} else {
		k_work_cancel_delayable(&disc_work);
	}
	LOG_INF("faults: seed %u, good->bad %u/65536, bad->good %u/65536, corrupt %u/65536, disconnect every %u s",
		seed, p_gb, p_bg, corrupt, disc_s);
}

/* Runs when the laptop writes a command. Reads the 1-byte command code. RESEND is only queued
 * here; tx_thread does the sending, so this function returns quickly. */
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

/* The service: data slot (board pushes) + its on/off switch the laptop sets (CCC) + command slot. */
BT_GATT_SERVICE_DEFINE(ehg_svc,
	BT_GATT_PRIMARY_SERVICE(&svc_uuid.uuid),
	BT_GATT_CHARACTERISTIC(&data_uuid.uuid, BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_NONE, NULL, NULL, NULL),
	BT_GATT_CCC(NULL, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	BT_GATT_CHARACTERISTIC(&ctrl_uuid.uuid, BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP,
			       BT_GATT_PERM_WRITE, NULL, ctrl_write, NULL),
);

#define DATA_ATTR (&ehg_svc.attrs[2])

/* Remember the connection (or forget it when c is NULL). Losing it does NOT stop sampling: a real
 * sensor keeps measuring while the radio is down, so seq keeps counting and the gap is recovered
 * from history after the reconnect (up to CONFIG_EHG_HISTORY_PACKETS packets back). */
void ehg_service_set_conn(struct bt_conn *c)
{
	k_mutex_lock(&conn_lock, K_FOREVER);
	if (conn) {
		bt_conn_unref(conn);
	}
	conn = c ? bt_conn_ref(c) : NULL;
	k_mutex_unlock(&conn_lock);
}

bool ehg_service_connected(void)
{
	return conn != NULL;
}

/* Send 1 packet to the laptop, if connected and listening. Returns 0 on success. */
static int send(const uint8_t *buf, uint16_t len)
{
	int err = -ENOTCONN;

	k_mutex_lock(&conn_lock, K_FOREVER);
	struct bt_conn *c = conn ? bt_conn_ref(conn) : NULL;

	k_mutex_unlock(&conn_lock);
	if (!c) {
		return err;
	}
	/* Right after connecting, the max message size (MTU) may not be agreed yet: wait, don't drop. */
	for (int i = 0; i < 100 && bt_gatt_get_mtu(c) - 3 < len; i++) {
		k_sleep(K_MSEC(10));
	}
	if (bt_gatt_is_subscribed(c, DATA_ATTR, BT_GATT_CCC_NOTIFY)) {
		/* Fault stage (off unless tools/feed.py --faults): the Gilbert model decides whether this
		 * packet is "lost on the air" (not sent, but counted as sent, as a real loss looks to the
		 * board), else maybe one bit of the sent copy is flipped so the gateway's CRC must catch it. */
		if (flt.p_gb || flt.bad) {
			flt.bad = flt.bad ? (rnd() & 0xFFFF) >= flt.p_bg : (rnd() & 0xFFFF) < flt.p_gb;
		}
		if (flt.bad) {
			n_inj_drop++;
			err = 0;
		} else if (flt.corrupt && (rnd() & 0xFFFF) < flt.corrupt) {
			uint8_t tmp[EHG_MAX_PKT];
			uint32_t bit = rnd() % (len * 8);

			memcpy(tmp, buf, len);
			tmp[bit / 8] ^= BIT(bit % 8);
			n_inj_corrupt++;
			err = bt_gatt_notify(c, DATA_ATTR, tmp, len);
		} else {
			err = bt_gatt_notify(c, DATA_ATTR, buf, len); /* copies buf; waits if the radio is busy */
		}
	}
	bt_conn_unref(c);
	if (err) {
		n_notify_err++;
	}
	return err;
}

/* Write the CRC-16 trailer over everything before it. Called after any header change. */
static void put_crc(uint8_t *pkt, int len)
{
	sys_put_le16(crc16_itu_t(0xFFFF, pkt, len - EHG_CRC_BYTES), &pkt[len - EHG_CRC_BYTES]);
}

/* Handle queued RESEND requests: send each stored copy again with the "re-sent" flag. */
static void serve_resends(void)
{
	struct resend_req r;
	uint8_t tmp[EHG_MAX_PKT];

	while (k_msgq_get(&resend_q, &r, K_NO_WAIT) == 0) {
		for (uint32_t s = r.seq; s != r.seq + r.count; s++) {
			const struct hist_entry *h = &history[s % CONFIG_EHG_HISTORY_PACKETS];

			if (!h->valid || h->seq != s) {
				n_resend_miss++; /* already overwritten: the gateway logs it as lost */
				continue;
			}
			memcpy(tmp, h->buf, h->len);
			((struct ehg_pkt_hdr *)tmp)->flags |= EHG_FLAG_RETRANS;
			put_crc(tmp, h->len);
			if (send(tmp, h->len) == 0) {
				n_resent++;
			}
		}
	}
}

/* Close the packet being built: header frame count, CRC, copy into history, send, next seq. */
static void finish(uint8_t *pkt, uint8_t nf, int frame_bytes, uint32_t *seq)
{
	int len = HDR_LEN + nf * frame_bytes + EHG_CRC_BYTES;
	struct hist_entry *h = &history[*seq % CONFIG_EHG_HISTORY_PACKETS];

	((struct ehg_pkt_hdr *)pkt)->n_frames = nf;
	put_crc(pkt, len);
	h->seq = *seq;
	h->valid = true;
	h->len = len;
	memcpy(h->buf, pkt, len);
	int err = send(pkt, len);

	if (err == 0) {
		n_sent++;
	} else if (err == -ENOTCONN) {
		n_offline++; /* made while disconnected: kept in history for the resend after reconnect */
	}
	(*seq)++;
}

/* The packet builder. Loops forever: handle resends -> take 1 frame -> add it to the packet.
 * Full packet (up to CONFIG_EHG_FRAMES_PER_PACKET frames, fewer if many channels): finish it.
 * Only consecutive frames share a packet: a skipped frame (replay.c: starved) closes it early. */
static void tx_thread(void *a, void *b, void *c)
{
	uint8_t pkt[EHG_MAX_PKT];
	struct ehg_pkt_hdr *hdr = (struct ehg_pkt_hdr *)pkt;
	struct ehg_frame f;
	uint32_t seq = 0, next_idx = 0;
	uint8_t nf = 0, fpp = 1;
	int w = 2, frame_bytes = 0;

	for (;;) {
		serve_resends();
		if (k_msgq_get(&replay_ring, &f, K_MSEC(10)) != 0) {
			continue;
		}
		if (f.start) { /* new session: restart numbering, sizes from the record's CONFIG */
			seq = 0;
			nf = 0;
			memset(history, 0, sizeof(history));
			w = replay_bits() / 8;
			frame_bytes = replay_n_ch() * w;
			fpp = MIN(CONFIG_EHG_FRAMES_PER_PACKET,
				  (EHG_MAX_PKT - HDR_LEN - EHG_CRC_BYTES) / frame_bytes);
		} else if (nf > 0 && f.frame_idx != next_idx) {
			finish(pkt, nf, frame_bytes, &seq);
			nf = 0;
		}
		if (nf == 0) { /* 1st frame of a new packet: header takes its frame number + clock tick */
			*hdr = (struct ehg_pkt_hdr){
				.version = EHG_PKT_VERSION,
				.n_ch = replay_n_ch(),
				.fs_hz = replay_fs_hz(),
				.seq = seq,
				.frame_idx = f.frame_idx,
				.t_inject = f.tick,
				.flags = w == 3 ? EHG_FLAG_24BIT : 0,
				.record_id = replay_record_id(),
			};
		}
		uint8_t *dst = &pkt[HDR_LEN + nf * frame_bytes];

		for (int ch = 0; ch < replay_n_ch(); ch++, dst += w) {
			if (w == 3) {
				sys_put_le24((uint32_t)f.s[ch], dst);
			} else {
				sys_put_le16((uint16_t)f.s[ch], dst);
			}
		}
		next_idx = f.frame_idx + 1;
		if (++nf == fpp) {
			finish(pkt, nf, frame_bytes, &seq);
			nf = 0;
		}
	}
}

K_THREAD_DEFINE(tx_tid, 2048, tx_thread, NULL, NULL, NULL, 5, 0, 0);

/* Print counters to the serial log: sent, re-sent, resends too late, send errors,
 * packets made while disconnected (offline), frames dropped at the queue, frames the laptop did not deliver in time (starved) or delivered
 * too late (late), bad serial messages, worst frame lateness. */
void ehg_service_log_stats(void)
{
	LOG_INF("sent=%u offline=%u resent=%u resend_miss=%u notify_err=%u ring_overflow=%u starved=%u "
		"late=%u rx_bad=%u max_inject_late=%u us",
		n_sent, n_offline, n_resent, n_resend_miss, n_notify_err, replay_overflows(), replay_starved(),
		replay_late(), replay_rx_bad(), (uint32_t)k_ticks_to_us_ceil32(replay_max_late_ticks()));
	if (flt.p_gb || flt.corrupt || flt.disc_s) {
		LOG_INF("injected: drop=%u corrupt=%u disc=%u", n_inj_drop, n_inj_corrupt, n_inj_disc);
	}
}
