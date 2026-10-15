/*
 * replay.c - digital sample injection (report 8.2, Sensor-in-the-Loop pattern [60]).
 *
 * PURPOSE: The fake "sensor". Releases 1 frame (1 sample per channel) at the recording's original
 *   rate, e.g. 20 per second for TPEHG.
 * INPUT: src/replay_data.h = REPLAY_N_CH / N_FRAMES / FS_HZ / RECORD_ID + replay_samples[] (raw int16).
 *   Made by tools/make_replay.py from data/converted/<db>/<record>.npz.
 * OUTPUT: `struct ehg_frame` {tick, frame_idx, s[ch]} into the queue replay_ring (src/ehg_packet.h).
 *   tick = board clock (32768/s) when the frame was released = t_inject.
 * HOW: Thread waits for START, then loops: work out when next frame is due, sleep until then, copy
 *   that row of the recording, stamp the clock tick, push to replay_ring. Queue full -> counted as
 *   overflow, never silently lost. Record loops: a 30 min TPEHG record feeds a 6 h soak (H2).
 *   Frame n is due at t0 + n * TICKS_PER_SEC / fs on the absolute tick timeline. A k_timer period
 *   would round to whole ticks (50 ms = 1638.4 ticks), giving 20.005 Hz instead of 20 Hz.
 *   Remaining per-frame lateness is measured (replay_max_late_ticks) for H1.
 * ROLE IN THESIS: Gantt 1a/1b. Start of every latency measurement (H1: the tick stamped here is
 *   t_inject) and the known source for loss + bit-exact checks (H2/H4).
 * BEFORE -> AFTER: Before: src/replay_data.h (tools/make_replay.py). After: replay_ring -> read by
 *   tx_thread in src/ehg_service.c.
 */
/* Must come before any include: replay.h -> ehg_packet.h already pulls in replay_data.h,
 * and only this file may get the sample array. */
#define REPLAY_DEFINE_SAMPLES
#include "replay.h"

#include <string.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(replay, LOG_LEVEL_INF);

BUILD_ASSERT(REPLAY_N_CH <= EHG_MAX_CH, "replay source has more channels than EHG_MAX_CH");

/* The ring buffer: a fixed-size queue of frames between this thread and the packetiser. */
K_MSGQ_DEFINE(replay_ring, sizeof(struct ehg_frame), CONFIG_EHG_RING_FRAMES, 4);

/* State shared between replay_start/stop (called from Bluetooth) and the replay thread.
 * The spinlock stops both sides from reading/writing it at the same moment. */
static K_SEM_DEFINE(start_sem, 0, 1);
static struct k_spinlock lock;
static bool running;
static int64_t t0;
static uint32_t n;
static uint32_t overflows;
static uint32_t max_late;

/* Reset counters, note the start time t0, empty the queue, and wake the replay thread. */
void replay_start(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);

	t0 = k_uptime_ticks();
	n = 0;
	overflows = 0;
	max_late = 0;
	running = true;
	k_spin_unlock(&lock, key);
	k_msgq_purge(&replay_ring);
	k_sem_give(&start_sem);
	LOG_INF("replay start: record %d, %d ch @ %d Hz, %d frames", REPLAY_RECORD_ID, REPLAY_N_CH,
		REPLAY_FS_HZ, REPLAY_N_FRAMES);
}

/* Ask the thread to stop after its current frame. */
void replay_stop(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);

	running = false;
	k_spin_unlock(&lock, key);
}

/* The sensor loop. Outer loop: wait for START. Inner loop: one iteration per frame. */
static void replay_thread(void *a, void *b, void *c)
{
	struct ehg_frame f;

	for (;;) {
		k_sem_take(&start_sem, K_FOREVER);
		for (;;) {
			k_spinlock_key_t key = k_spin_lock(&lock);
			bool run = running;
			uint32_t idx = n;
			int64_t due = t0 + ((int64_t)idx * CONFIG_SYS_CLOCK_TICKS_PER_SEC) / REPLAY_FS_HZ;

			k_spin_unlock(&lock, key);
			if (!run) {
				break;
			}
			/* frame idx is due at t0 + idx/fs seconds (in ticks); sleep until that exact moment */
			k_sleep(K_TIMEOUT_ABS_TICKS(due));

			int64_t now = k_uptime_ticks();

			key = k_spin_lock(&lock);
			if (!running || n != idx) { /* stopped or restarted while sleeping */
				k_spin_unlock(&lock, key);
				continue;
			}
			n++;
			max_late = MAX(max_late, (uint32_t)(now - due));
			k_spin_unlock(&lock, key);

			/* build the frame: timestamp, counter, and row (idx mod record length) so it loops */
			f.tick = (uint32_t)now;
			f.frame_idx = idx;
			memcpy(f.s, &replay_samples[(idx % REPLAY_N_FRAMES) * REPLAY_N_CH],
			       REPLAY_N_CH * sizeof(ehg_sample_t));
			if (k_msgq_put(&replay_ring, &f, K_NO_WAIT) != 0) {
				overflows++;
			}
		}
	}
}

/* Higher priority (lower number) than the packetiser so injection timing is not disturbed. */
K_THREAD_DEFINE(replay_tid, 1024, replay_thread, NULL, NULL, NULL, 2, 0, 0);

/* Simple getters used by ehg_service.c (header fields and the stats log). */
uint32_t replay_overflows(void) { return overflows; }
uint32_t replay_max_late_ticks(void) { return max_late; }
uint8_t replay_n_ch(void) { return REPLAY_N_CH; }
uint16_t replay_fs_hz(void) { return REPLAY_FS_HZ; }
uint16_t replay_record_id(void) { return REPLAY_RECORD_ID; }
