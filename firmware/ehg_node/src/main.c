/*
 * main.c - EHG sensor node for the nRF52840 DK.
 *
 * Advertises as "EHG-XXXX" (XXXX from the chip's device ID, so several DKs get distinct
 * names for H3). On connect: requests 2M PHY and max data length, and logs what the
 * central actually granted (MTU, PHY, DLE, interval) because macOS decides these itself.
 * Streaming starts when the gateway writes START to the control characteristic.
 *
 * Logs on VCOM0 at 115200: link parameters on change, packet stats every 10 s.
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

static char name[9]; /* "EHG-XXXX" */
static struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA(BT_DATA_NAME_COMPLETE, name, 8),
};
static const struct bt_data sd[] = {
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, EHG_SVC_UUID_VAL),
};

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

static void connected(struct bt_conn *conn, uint8_t err)
{
	if (err) {
		LOG_WRN("connection failed (0x%02x)", err);
		return;
	}
	LOG_INF("connected");
	ehg_service_set_conn(conn);
	/* Requests only - the central decides. Results arrive in the callbacks below. */
	bt_conn_le_phy_update(conn, BT_CONN_LE_PHY_PARAM_2M);
	bt_conn_le_data_len_update(conn, BT_LE_DATA_LEN_PARAM_MAX);
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	LOG_INF("disconnected (0x%02x)", reason);
	ehg_service_set_conn(NULL);
	ehg_service_log_stats();
}

static void recycled(void)
{
	k_work_submit(&adv_work);
}

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

BT_CONN_CB_DEFINE(conn_cb) = {
	.connected = connected,
	.disconnected = disconnected,
	.recycled = recycled,
	.le_param_updated = param_updated,
	.le_phy_updated = phy_updated,
	.le_data_len_updated = data_len_updated,
};

static void mtu_updated(struct bt_conn *conn, uint16_t tx, uint16_t rx)
{
	LOG_INF("ATT MTU: tx %u rx %u", tx, rx);
}

static struct bt_gatt_cb gatt_cb = {.att_mtu_updated = mtu_updated};

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
