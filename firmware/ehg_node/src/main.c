/*
 * main.c - start-up + Bluetooth connection. The program starts here.
 *
 * PURPOSE: Names the board, makes it findable over Bluetooth, accepts the laptop's connection,
 *   prints link info + counters to the serial log.
 * INPUT: chip ID (for the name); the connection from the laptop (gateway/gateway.py).
 *   e.g. chip ID 8 bytes, last 2 = 93 C0 (our DK) -> name "EHG-93C0".
 *   e.g. connection: the laptop connects and asks for its link settings; main.c only accepts.
 * OUTPUT: Bluetooth adverts as "EHG-XXXX" (XXXX from the chip ID, so every board has its own name, H3).
 *   Serial log (VCOM0, 115200 baud): link settings when they change, packet counters every 10 s.
 *   e.g. advert: flags 06 + name "EHG-93C0"; scan response: service ID 4e480001-a0e1-...-9a1b2c3d4e5f
 *   e.g. log of 1 connection (timestamps vary; values from the 2026-10-06 run):
 *     [00:00:00.010,000] <inf> main: advertising as EHG-93C0
 *     [00:00:04.120,000] <inf> main: connected
 *     [00:00:04.300,000] <inf> main: PHY: tx 2 rx 2 (1=1M 2=2M)
 *     [00:00:04.350,000] <inf> main: data length: tx 251 B rx 251 B
 *     [00:00:04.400,000] <inf> main: ATT MTU: tx 247 rx 247
 *     [00:00:04.900,000] <inf> main: conn params: interval 30.00 ms, latency 0, timeout 6000 ms
 *     [00:00:10.000,000] <inf> ehg_svc: sent=11 resent=0 resend_miss=0 notify_err=0 ring_overflow=0 max_inject_late=31 us
 *     [00:05:00.000,000] <inf> main: disconnected (0x13)   (0x13 = laptop closed it; 0x08 = link timed out)
 * HOW: name from chip ID -> Bluetooth on -> advertise ("I'm here, connect to me").
 *   On connect: hand the connection to ehg_service.c, ask for the fastest radio settings.
 *   The laptop decides what it actually grants; the callbacks below only log it.
 *   Streaming starts later, when the laptop sends START.
 * ROLE IN THESIS: Board side of the Bluetooth link (report Fig. 10). Logged settings explain latency (H1).
 * SEE: ehg_service.c (data + commands), docs/firmware.md (overview + glossary).
  */
#include <stdio.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/drivers/hwinfo.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "ehg_service.h"

LOG_MODULE_REGISTER(main, LOG_LEVEL_INF);

/* What the board broadcasts so the laptop can find it.
 * ad = flags + name. sd ("scan response", sent when asked) = the EHG service ID the gateway looks for. */
static char name[9]; /* "EHG-XXXX" */
static struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA(BT_DATA_NAME_COMPLETE, name, 8),
};
static const struct bt_data sd[] = {
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, EHG_SVC_UUID_VAL),
};

/* Start advertising. Queued as a job so it can be restarted after a disconnect. */
static void adv_work_handler(struct k_work *work)
{
	int err = bt_le_adv_start(BT_LE_ADV_CONN_FAST_2, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));

	if (err) {
		LOG_ERR("advertising failed (%d)", err);
	} else {
		LOG_INF("advertising as %s", name);
	}
}
static K_WORK_DEFINE(adv_work, adv_work_handler);

/* Laptop connected: hand the connection to ehg_service.c, ask for faster radio settings. */
static void connected(struct bt_conn *conn, uint8_t err)
{
	if (err) {
		LOG_WRN("connection failed (0x%02x)", err);
		return;
	}
	LOG_INF("connected");
	ehg_service_set_conn(conn);
	/* Only requests: the laptop decides. 2M PHY = 2 Mbit/s radio mode; data length = bigger radio packets. */
	bt_conn_le_phy_update(conn, BT_CONN_LE_PHY_PARAM_2M);
	bt_conn_le_data_len_update(conn, BT_LE_DATA_LEN_PARAM_MAX);
}

/* Connection lost: forget it (this also stops the replay) and print the final counters. */
static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	LOG_INF("disconnected (0x%02x)", reason);
	ehg_service_set_conn(NULL);
	ehg_service_log_stats();
}

/* Connection fully cleaned up: advertise again so the gateway can reconnect. */
static void recycled(void)
{
	k_work_submit(&adv_work);
}

/* These 3 only log what the laptop granted. Units: interval x 1.25 ms, timeout x 10 ms. */
static void param_updated(struct bt_conn *conn, uint16_t interval, uint16_t latency, uint16_t timeout)
{
	LOG_INF("conn params: interval %u.%02u ms, latency %u, timeout %u ms", interval * 125 / 100,
		(interval * 125) % 100, latency, timeout * 10);
}

static void phy_updated(struct bt_conn *conn, struct bt_conn_le_phy_info *p)
{
	LOG_INF("PHY: tx %u rx %u (1=1M 2=2M)", p->tx_phy, p->rx_phy);
}

static void data_len_updated(struct bt_conn *conn, struct bt_conn_le_data_len_info *i)
{
	LOG_INF("data length: tx %u B rx %u B", i->tx_max_len, i->rx_max_len);
}

/* Tell the Bluetooth stack to call the functions above on these events. */
BT_CONN_CB_DEFINE(conn_cb) = {
	.connected = connected,
	.disconnected = disconnected,
	.recycled = recycled,
	.le_param_updated = param_updated,
	.le_phy_updated = phy_updated,
	.le_data_len_updated = data_len_updated,
};

/* MTU = biggest message the link allows. Ours need up to 247 bytes (see prj.conf). */
static void mtu_updated(struct bt_conn *conn, uint16_t tx, uint16_t rx)
{
	LOG_INF("ATT MTU: tx %u rx %u", tx, rx);
}

static struct bt_gatt_cb gatt_cb = {.att_mtu_updated = mtu_updated};

/* Start here: name from the last 2 bytes of the chip ID, Bluetooth on, advertise, then print
 * counters every 10 s forever. The real work runs in other threads (replay.c, ehg_service.c). */
int main(void)
{
	uint8_t id[8] = {0};
	ssize_t n = hwinfo_get_device_id(id, sizeof(id));

	snprintf(name, sizeof(name), "EHG-%02X%02X", n >= 2 ? id[n - 2] : 0, n >= 1 ? id[n - 1] : 0);

	int err = bt_enable(NULL);

	if (err) {
		LOG_ERR("bt_enable failed (%d)", err);
		return 0;
	}
	bt_set_name(name);
	bt_gatt_cb_register(&gatt_cb);
	k_work_submit(&adv_work);

	for (;;) {
		k_sleep(K_SECONDS(10));
		ehg_service_log_stats();
	}
	return 0;
}
