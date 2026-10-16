/*
 * ehg_service.h - what other files may call in ehg_service.c, plus the Bluetooth service ID.
 *
 * PURPOSE: Declarations for ehg_service.c + the ID the laptop uses to find the EHG service.
 * HOW: A UUID = long unique number naming a Bluetooth "service" (a group of slots).
 *   Ours ends 0001; data slot 0002 (board -> laptop), command slot 0003 (laptop -> board).
 * ROLE IN THESIS: Gantt 1c/1d.
 * BEFORE -> AFTER: main.c calls these on connect/disconnect and advertises EHG_SVC_UUID_VAL.
 *   gateway/gateway.py has the same IDs as text. Commands: docs/interface-spec.md 3.
  */
#pragma once
#include <zephyr/bluetooth/conn.h>

/* Service 4e480001-a0e1-4c3b-8747-9a1b2c3d4e5f; data slot ...0002; command slot ...0003 */
#define EHG_UUID_BASE(n) BT_UUID_128_ENCODE(0x4e480000 + (n), 0xa0e1, 0x4c3b, 0x8747, 0x9a1b2c3d4e5f)
#define EHG_SVC_UUID_VAL EHG_UUID_BASE(1)

/* Set the current connection (NULL = disconnected, which stops the replay). */
void ehg_service_set_conn(struct bt_conn *conn);

void ehg_service_log_stats(void);
