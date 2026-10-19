/*
 * replay.c - the fake sensor: plays a stored recording at its real speed
 * (report 8.2, Sensor-in-the-Loop pattern [60]).
 *
 * PURPOSE: Releases 1 frame (1 sample per channel) at the recording's own rate, e.g. 20 per second
 *   for TPEHG, as if a real sensor were measuring.
 * INPUT: replay_data.h = the recording baked into the firmware (N_CH, N_FRAMES, FS_HZ, RECORD_ID,
 *   replay_samples[]). Made by tools/make_replay.py from data/converted/<db>/<record>.npz.
 *   e.g. (tpehg1007):  #define REPLAY_N_CH 3 / REPLAY_N_FRAMES 35180 / REPLAY_FS_HZ 20
 *        static const int16_t replay_samples[] = { 1093, 171, -748,   1145, 180, -790, ... };
 *   Plus START / STOP, which ehg_service.c passes on as replay_start() / replay_stop().
 * OUTPUT: frames {tick, frame_idx, samples} into the queue replay_ring (struct in ehg_packet.h).
 *   tick = board clock (32768 ticks/s) when the frame was released = t_inject (start of H1 timing).
 *   e.g. START at tick 98304 (3 s after boot), then 1 frame every 1638.4 ticks (50 ms):
 *     {tick=98304, frame_idx=0, s={1093, 171, -748}}
 *     {tick=99942, frame_idx=1, s={1145, 180, -790}}
 *     ... frame_idx 35180 restarts at row 0 (loop) but keeps counting up.
 *   e.g. log: [00:00:03.000,000] <inf> replay: replay start: record 0, 3 ch @ 20 Hz, 35180 frames
 * HOW: A thread waits for START, then loops: work out when the next frame is due -> sleep until
 *   then -> copy that row of the recording -> stamp the clock -> put it in the queue.
 *   Queue full -> counted as overflow, never silently lost.
 *   The recording loops, so a 30 min record can feed a 6 h test (H2).
 *   Due time = start + n / fs, worked out fresh for every frame. A repeating 50 ms timer would
 *   round to whole ticks (1638.4 -> 1638) and drift to 20.005 Hz; this way the rate stays exact.
 * ROLE IN THESIS: Gantt 1a/1b. Start of every latency measurement (H1) and the known source for
 *   loss + bit-exact checks (H2/H4).
 * BEFORE -> AFTER: Before: replay_data.h (tools/make_replay.py). After: replay_ring -> ehg_service.c.
  */
/* Must come before the includes: only this file gets the big sample array from replay_data.h
 * (replay.h -> ehg_packet.h already includes replay_data.h). */
#define REPLAY_DEFINE_SAMPLES
#include "replay.h"

#include <string.h>
#include <zephyr/logging/log.h>

LOG_MODULE_REGISTER(replay, LOG_LEVEL_INF);

BUILD_ASSERT(REPLAY_N_CH <= EHG_MAX_CH, "replay source has more channels than EHG_MAX_CH");

/* The queue between this thread and ehg_service.c. Fixed size: CONFIG_EHG_RING_FRAMES frames. */
K_MSGQ_DEFINE(replay_ring, sizeof(struct ehg_frame), CONFIG_EHG_RING_FRAMES, 4);

/* State shared by replay_start/stop (called on laptop commands) and the replay thread.
 * The lock stops both from changing it at the same moment. */
static K_SEM_DEFINE(start_sem, 0, 1);
static struct k_spinlock lock;
static bool running;
static int64_t t0;
static uint32_t n;
static uint32_t overflows;
static uint32_t max_late;

/* Reset counters, note the start time t0, empty the queue, wake the replay thread. */
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

/* Ask the thread to stop after the current frame. */
void replay_stop(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);

	running = false;
	k_spin_unlock(&lock, key);
}

/* The sensor loop. Outer loop: wait for START. Inner loop: 1 pass per frame. */
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
			/* frame idx is due at t0 + idx/fs seconds (in ticks): sleep until exactly then */
			k_sleep(K_TIMEOUT_ABS_TICKS(due));

			int64_t now = k_uptime_ticks();

			key = k_spin_lock(&lock);
			if (!running || n != idx) { /* stopped or restarted while asleep */
				k_spin_unlock(&lock, key);
				continue;
			}
			n++;
			max_late = MAX(max_late, (uint32_t)(now - due));
			k_spin_unlock(&lock, key);

			/* build the frame: clock tick, frame number, recording row (wraps round = loops) */
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

/* Priority 2 beats ehg_service.c's 5 (lower = more urgent), so sending never delays the timing. */
K_THREAD_DEFINE(replay_tid, 1024, replay_thread, NULL, NULL, NULL, 2, 0, 0);

/* Getters for ehg_service.c (packet header fields + stats log). */
uint32_t replay_overflows(void) { return overflows; }
uint32_t replay_max_late_ticks(void) { return max_late; }
uint8_t replay_n_ch(void) { return REPLAY_N_CH; }
uint16_t replay_fs_hz(void) { return REPLAY_FS_HZ; }
uint16_t replay_record_id(void) { return REPLAY_RECORD_ID; }
