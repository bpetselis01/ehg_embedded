/*
 * replay.h - digital sample injection (Gantt 1a) into the ring buffer (1b).
 *
 * PURPOSE: The public "menu" of replay.c: what other firmware files may call to start/stop
 *   the fake sensor and read its health counters.
 * HOW: Declarations only; the code is in replay.c.
 * ROLE IN THESIS: Digital injection = playing a stored real EHG recording as if a sensor
 *   were measuring it live, so results can be checked against a known answer.
 * BEFORE -> AFTER: Used by ehg_service.c (start/stop on gateway command, read frames from
 *   replay_ring, read counters for the stats log).
 */
#pragma once
#include <zephyr/kernel.h>

#include "ehg_packet.h"

/* Frames flow replay thread -> packetiser through this queue. */
extern struct k_msgq replay_ring;

void replay_start(void); /* resets frame counter; frame 0 is injected immediately */
void replay_stop(void);

/* Frames dropped because the ring was full (packetiser/BLE could not keep up). Never silent. */
uint32_t replay_overflows(void);

/* Worst lateness of injection vs the ideal schedule, in ticks - the injection-jitter figure (1d). */
uint32_t replay_max_late_ticks(void);

uint8_t replay_n_ch(void);
uint16_t replay_fs_hz(void);
uint16_t replay_record_id(void);
