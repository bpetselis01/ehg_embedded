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

void replay_start(void); /* restart from frame 0, sent immediately */
void replay_stop(void);

/* Frames dropped because the queue was full (sending fell behind). Counted, never silent. */
uint32_t replay_overflows(void);

/* Worst lateness of any frame vs its exact due time, in clock ticks (timing jitter, Gantt 1d). */
uint32_t replay_max_late_ticks(void);

uint8_t replay_n_ch(void);
uint16_t replay_fs_hz(void);
uint16_t replay_record_id(void);
