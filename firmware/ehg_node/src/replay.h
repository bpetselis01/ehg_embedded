/*
 * replay.h - digital sample injection (Gantt 1a) into the ring buffer (1b).
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
