/*
 * ehg_service.h - EHG GATT service (Gantt 1c) + packetiser with seq/timestamps (1d)
 * and a history buffer serving gateway re-requests (H2).
 *
 * PURPOSE: The public "menu" of ehg_service.c, plus the Bluetooth service ID.
 * HOW: Declarations and the ID macro. A UUID is a long unique number naming a Bluetooth
 *   "service" (a group of data slots); ours ends 0001, its data slot 0002, its command slot 0003.
 * ROLE IN THESIS: Gantt 1c/1d.
 * BEFORE -> AFTER: main.c calls these on connect/disconnect and advertises EHG_SVC_UUID_VAL.
 *   gateway/gateway.py has the same IDs as strings.
 */
#pragma once
#include <zephyr/bluetooth/conn.h>

/* Service 4e480001-a0e1-4c3b-8747-9a1b2c3d4e5f; data 0002 (notify); ctrl 0003 (write) */
#define EHG_UUID_BASE(n) BT_UUID_128_ENCODE(0x4e480000 + (n), 0xa0e1, 0x4c3b, 0x8747, 0x9a1b2c3d4e5f)
#define EHG_SVC_UUID_VAL EHG_UUID_BASE(1)

/* Current connection or NULL. Replay stops when the link drops. */
void ehg_service_set_conn(struct bt_conn *conn);

void ehg_service_log_stats(void);
