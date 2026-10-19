/*
 * replay.h - what other files may call in replay.c (the fake sensor).
 *
 * PURPOSE: The list of replay.c functions other files can use: start/stop, read counters.
 * HOW: Declarations only. The code is in replay.c.
 * ROLE IN THESIS: "Digital injection" = playing a stored real EHG recording as if a sensor were
 *   measuring it live, so every result can be checked against a known answer.
 * BEFORE -> AFTER: Used by ehg_service.c (start/stop on laptop command, read frames from
 *   replay_ring, read counters for the log).
  */
#pragma once
#include <zephyr/kernel.h>

#include "ehg_packet.h"

/* Queue ("ring buffer") carrying frames from replay.c to ehg_service.c. */
extern struct k_msgq replay_ring;

void replay_start(void); /* start a session (no-op if running or before the laptop's CONFIG) */
void replay_stop(void);
bool replay_running(void); /* a session is sampling (LED2 in main.c) */

/* Frames dropped because the queue was full (sending fell behind). Counted, never silent. */
uint32_t replay_overflows(void);

/* Frames skipped because the laptop had not sent them by their due time (test-rig loss, not radio loss). */
uint32_t replay_starved(void);

/* Frames thrown away because they arrived after their due time had passed. */
uint32_t replay_late(void);

/* Serial messages rejected (bad COBS/CRC/length) or that did not fit the queue. */
uint32_t replay_rx_bad(void);

/* Worst lateness of any frame vs its exact due time, in clock ticks (timing jitter, Gantt 1d). */
uint32_t replay_max_late_ticks(void);

/* Record settings from the laptop's CONFIG (0 before it). */
uint8_t replay_n_ch(void);
uint8_t replay_bits(void);
uint16_t replay_fs_hz(void);
uint16_t replay_record_id(void);
