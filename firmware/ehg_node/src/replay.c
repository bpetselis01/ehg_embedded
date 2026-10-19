/*
 * replay.c - the sensor stand-in: takes frames the laptop streams in over the serial port and
 * releases them at the recording's real speed (report 8.2, digital injection, Sensor-in-the-Loop [60]).
 *
 * PURPOSE: Releases 1 frame (1 sample per channel) at the recording's own rate, e.g. 20 per second
 *   for TPEHG, as if a real sensor were measuring. The laptop only fills a queue ahead of time;
 *   the board's own timer decides when each frame is "sampled".
 * INPUT: messages from tools/feed.py on uart0 (the J-Link VCOM, shared with the log), each COBS-encoded,
 *   ended by 0x00, decoded = type | payload | CRC-16 (docs/interface-spec.md 3b):
 *   e.g. CONFIG 10 | 03 14 00 00 00 10 2a 9c    = 3 ch, 20 Hz, record_id 0, 16-bit, feeder session 0x9c2a
 *        DATA   11 | 00 00 00 00 02 | 45 04 ...  = frames from frame_idx 0, 2 frames, samples 1093 171 -748 ...
 *        SYNC   12 | 07 00 00 00                 = clock ping 7 (answered at once with @SYNC 7 <tick>)
 *        FAULT  13 | seed u32, 4 x u16           = fault injection settings (ehg_service_set_faults)
 *   Plus START / STOP, which ehg_service.c (BLE) and main.c (button) pass on as replay_start() / replay_stop().
 * OUTPUT: frames {tick, frame_idx, samples} into the queue replay_ring (struct in ehg_packet.h).
 *   tick = board clock (32768 ticks/s) when the frame was released = t_inject (start of H1 timing).
 *   e.g. START at tick 98304 (3 s after boot), then 1 frame every 1638.4 ticks (50 ms):
 *     {tick=98304, frame_idx=0, s={1093, 171, -748}, start=1}
 *     {tick=99942, frame_idx=1, s={1145, 180, -790}}
 *   Lines for the laptop (through the log, so they never split a log line):
 *   e.g. [00:00:01.200,000] <inf> replay: @HELLO EHG-93C0 3 20 0 #xxxx  (CONFIG accepted)
 *        [00:00:02.000,000] <inf> replay: @LVL 40 #xxxx                  (frames queued, once a second)
 *        [00:00:02.500,000] <inf> replay: @SYNC 7 81920 #xxxx            (board tick when ping 7 arrived)
 *   "#xxxx" = CRC-16 of the text between "@" and " #", so a damaged line is ignored (at_line).
 *        [00:00:03.000,000] <inf> replay: replay start: record 0, 3 ch @ 20 Hz, from frame 0
 * HOW: A parser thread turns serial bytes into messages and puts DATA frames in feed_q.
 *   The replay thread waits for START, then loops: work out when frame n is due -> sleep until then
 *   -> stamp the clock -> take the frame numbered start_idx + n from feed_q -> put it in replay_ring.
 *   Frame not there yet (laptop fell behind) -> counted as starved, that frame number is skipped.
 *   Frame older than due -> thrown away, counted as late. replay_ring full -> counted as overflow.
 *   Due time = start + n / fs, worked out fresh for every frame. A repeating 50 ms timer would
 *   round to whole ticks (1638.4 -> 1638) and drift to 20.005 Hz; this way the rate stays exact.
 * ROLE IN THESIS: Gantt 1a/1b. Start of every latency measurement (H1) and the known source for
 *   loss + bit-exact checks (H2/H4).
 * BEFORE -> AFTER: Before: tools/feed.py (from data/converted/). After: replay_ring -> ehg_service.c.
 */
#include "replay.h"

#include "ehg_service.h"

#include <stdarg.h>
#include <string.h>
#include <zephyr/sys/printk.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/crc.h>
#include <zephyr/sys/ring_buffer.h>

LOG_MODULE_REGISTER(replay, LOG_LEVEL_INF);

#define MSG_CONFIG 0x10
#define MSG_DATA 0x11
#define MSG_SYNC 0x12
#define MSG_FAULT 0x13
#define MSG_MAX 600 /* biggest decoded message: DATA of 10 frames x 16 ch x 3 bytes + 9 */

/* The queue between this thread and ehg_service.c. Fixed size: CONFIG_EHG_RING_FRAMES frames. */
K_MSGQ_DEFINE(replay_ring, sizeof(struct ehg_frame), CONFIG_EHG_RING_FRAMES, 4);

/* Frames the laptop sent ahead, waiting for their due time (about 2 s worth, tools/feed.py --lead). */
struct src_frame {
	uint32_t frame_idx;
	int32_t s[EHG_MAX_CH];
};
K_MSGQ_DEFINE(feed_q, sizeof(struct src_frame), CONFIG_EHG_FEED_BUFFER_FRAMES, 4);

/* Record settings from CONFIG. Fixed while a session runs. */
static struct {
	bool valid;
	uint8_t n_ch, bits;
	uint16_t fs, record_id, session;
} cfg;

/* State shared by replay_start/stop (laptop or button) and the replay thread.
 * The lock stops both from changing it at the same moment. */
static K_SEM_DEFINE(start_sem, 0, 1);
static struct k_spinlock lock;
static bool running;
static int64_t t0;
static uint32_t n, start_idx;
static bool first_pending; /* next frame released is the session's first (ehg_service.c restarts seq) */
static uint32_t overflows, starved, late, max_late, rx_bad, feed_full, rx_full, rx_err;

/* Serial receive: DMA fills 2 buffers in turn, the callback copies bytes into rx_ring. */
static const struct device *uart = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
static uint8_t dma_buf[2][256];
static uint8_t dma_next;
RING_BUF_DECLARE(rx_ring, 4096);
static K_SEM_DEFINE(rx_sem, 0, 1);

static void uart_cb(const struct device *dev, struct uart_event *e, void *user)
{
	switch (e->type) {
	case UART_RX_RDY:
		if (ring_buf_put(&rx_ring, e->data.rx.buf + e->data.rx.offset, e->data.rx.len) <
		    e->data.rx.len) {
			rx_full++; /* parser fell behind: the broken message fails its CRC and is counted */
		}
		k_sem_give(&rx_sem);
		break;
	case UART_RX_BUF_REQUEST:
		uart_rx_buf_rsp(dev, dma_buf[dma_next], sizeof(dma_buf[0]));
		dma_next ^= 1;
		break;
	case UART_RX_STOPPED: /* line error (overrun, framing...): bytes lost, the message fails its CRC */
		rx_err++;
		break;
	case UART_RX_DISABLED: /* after a line error: start again */
		dma_next = 1;
		uart_rx_enable(dev, dma_buf[0], sizeof(dma_buf[0]), 1000);
		break;
	default:
		break;
	}
}

/* Print a line for tools/feed.py: "@<text> #<crc>", CRC-16 of <text> in hex. The VCOM loses a
 * few board->laptop bytes per 10,000 lines while the radio is busy (Unit 0 check), so the feeder
 * ignores any "@" line whose CRC does not match instead of acting on a damaged number. */
static void at_line(const char *fmt, ...)
{
	char text[64];
	va_list ap;

	va_start(ap, fmt);
	vsnprintk(text, sizeof(text), fmt, ap);
	va_end(ap);
	LOG_INF("@%s #%04x", text, crc16_itu_t(0xFFFF, text, strlen(text)));
}

/* COBS decode in place. Returns the decoded length, or -1 if malformed. */
static int cobs_decode(uint8_t *buf, int len)
{
	int in = 0, out = 0;

	while (in < len) {
		uint8_t code = buf[in++];

		if (code == 0 || in + code - 1 > len) {
			return -1;
		}
		for (int i = 1; i < code; i++) {
			buf[out++] = buf[in++];
		}
		if (code < 255 && in < len) {
			buf[out++] = 0;
		}
	}
	return out;
}

static void on_config(const uint8_t *p, int len)
{
	if (len != 8 || p[0] == 0 || p[0] > EHG_MAX_CH || (p[5] != 16 && p[5] != 24) ||
	    sys_get_le16(&p[1]) == 0) {
		rx_bad++;
		return;
	}
	/* The feeder repeats CONFIG until it hears @HELLO. A repeat (same session number) must not
	 * empty the queue it has started filling; a new feeder (new number) starts clean. */
	bool repeat = cfg.valid && sys_get_le16(&p[6]) == cfg.session;
	k_spinlock_key_t key = k_spin_lock(&lock);
	bool busy = running && !repeat;

	if (!busy && !repeat) {
		cfg.n_ch = p[0];
		cfg.fs = sys_get_le16(&p[1]);
		cfg.record_id = sys_get_le16(&p[3]);
		cfg.bits = p[5];
		cfg.session = sys_get_le16(&p[6]);
		cfg.valid = true;
	}
	k_spin_unlock(&lock, key);
	if (busy) {
		at_line("ERR CONFIG ignored while running (STOP first)");
		return;
	}
	if (!repeat) {
		k_msgq_purge(&feed_q);
	}
	at_line("HELLO %s %u %u %u", bt_get_name(), cfg.n_ch, cfg.fs, cfg.record_id);
}

static void on_data(const uint8_t *p, int len)
{
	int w = cfg.bits / 8;
	int nf = len >= 5 ? p[4] : 0;

	if (!cfg.valid || len != 5 + nf * cfg.n_ch * w) {
		rx_bad++;
		LOG_WRN("DATA dropped: %s (%d bytes)", cfg.valid ? "wrong length" : "no CONFIG yet", len);
		return;
	}
	struct src_frame f;
	const uint8_t *s = p + 5;

	for (int k = 0; k < nf; k++) {
		f.frame_idx = sys_get_le32(p) + k;
		for (int c = 0; c < cfg.n_ch; c++, s += w) {
			f.s[c] = w == 2 ? (int16_t)sys_get_le16(s) : sign_extend(sys_get_le24(s), 23);
		}
		if (k_msgq_put(&feed_q, &f, K_NO_WAIT) != 0) {
			feed_full++; /* laptop sent more than fits: it should never, it follows @LVL */
		}
	}
}

/* One complete message (between 0x00 markers): decode, check CRC, act on it. */
static void on_message(uint8_t *buf, int len)
{
	int raw = len;

	len = cobs_decode(buf, len);
	if (len < 3 || crc16_itu_t(0xFFFF, buf, len - 2) != sys_get_le16(&buf[len - 2])) {
		rx_bad++;
		LOG_WRN("serial message dropped: bad COBS/CRC (%d bytes in, type 0x%02x)", raw,
			len > 0 ? buf[0] : 0);
		return;
	}
	len -= 2;
	switch (buf[0]) {
	case MSG_CONFIG:
		on_config(buf + 1, len - 1);
		break;
	case MSG_DATA:
		on_data(buf + 1, len - 1);
		break;
	case MSG_SYNC: /* clock ping: answer with the board tick right now (eval/latency.py offset) */
		if (len == 5) {
			at_line("SYNC %u %u", sys_get_le32(&buf[1]), (uint32_t)k_uptime_ticks());
		} else {
			rx_bad++;
		}
		break;
	case MSG_FAULT: /* u32 seed, u16 good->bad, u16 bad->good, u16 corrupt, u16 disconnect s */
		if (len == 13) {
			ehg_service_set_faults(sys_get_le32(&buf[1]), sys_get_le16(&buf[5]), sys_get_le16(&buf[7]),
					       sys_get_le16(&buf[9]), sys_get_le16(&buf[11]));
		} else {
			rx_bad++;
		}
		break;
	default:
		rx_bad++;
	}
}

/* The serial parser: collect bytes up to each 0x00, handle the message. Reports the queue level
 * once a second so tools/feed.py can keep it topped up. */
static void parser_thread(void *a, void *b, void *c)
{
	static uint8_t msg[MSG_MAX + MSG_MAX / 254 + 2];
	int len = 0;
	bool overlong = false;
	int64_t next_lvl = k_uptime_get();

	uart_callback_set(uart, uart_cb, NULL);
	dma_next = 1;
	uart_rx_enable(uart, dma_buf[0], sizeof(dma_buf[0]), 1000);

	for (;;) {
		k_sem_take(&rx_sem, K_MSEC(100));
		uint8_t byte;

		while (ring_buf_get(&rx_ring, &byte, 1) == 1) {
			if (byte == 0) {
				if (overlong) {
					rx_bad++;
				} else if (len > 0) {
					on_message(msg, len);
				}
				len = 0;
				overlong = false;
			} else if (len < sizeof(msg)) {
				msg[len++] = byte;
			} else {
				overlong = true;
			}
		}
		if (k_uptime_get() >= next_lvl) {
			static uint32_t full_seen, err_seen;

			if (rx_full != full_seen || rx_err != err_seen) {
				full_seen = rx_full;
				err_seen = rx_err;
				LOG_WRN("serial receive trouble: ring full %u, line errors %u", rx_full, rx_err);
			}
			next_lvl += 1000;
			if (cfg.valid) { /* silence before CONFIG tells tools/feed.py the board (re)started */
				at_line("LVL %u", k_msgq_num_used_get(&feed_q));
			}
		}
	}
}

K_THREAD_DEFINE(parser_tid, 1536, parser_thread, NULL, NULL, NULL, 4, 0, 0);

/* Start a session: reset counters, note t0, number frames from the oldest one queued. Ignored
 * while already running (so a reconnecting gateway or a 2nd START does not restart the record)
 * and before a CONFIG (nothing to play). */
void replay_start(void)
{
	struct src_frame head;
	uint32_t first = k_msgq_peek(&feed_q, &head) == 0 ? head.frame_idx : 0;
	k_spinlock_key_t key = k_spin_lock(&lock);
	bool was = running, ok = cfg.valid;

	if (!was && ok) {
		start_idx = first;
		first_pending = true;
		t0 = k_uptime_ticks();
		n = 0;
		overflows = starved = late = max_late = 0;
		running = true;
	}
	k_spin_unlock(&lock, key);
	if (!ok) {
		at_line("ERR START before CONFIG: run tools/feed.py first");
		return;
	}
	if (was) {
		return;
	}
	k_msgq_purge(&replay_ring);
	k_sem_give(&start_sem);
	LOG_INF("replay start: record %u, %u ch @ %u Hz, from frame %u", cfg.record_id, cfg.n_ch, cfg.fs,
		start_idx);
}

/* Ask the thread to stop after the current frame. */
void replay_stop(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);

	running = false;
	k_spin_unlock(&lock, key);
}

/* Take the frame numbered want from feed_q: drop older ones, leave newer ones. 0 if found. */
static int take_frame(uint32_t want, struct src_frame *f)
{
	while (k_msgq_peek(&feed_q, f) == 0) {
		if ((int32_t)(f->frame_idx - want) > 0) {
			return -1; /* the wanted one never came; this one is for later */
		}
		k_msgq_get(&feed_q, f, K_NO_WAIT);
		if (f->frame_idx == want) {
			return 0;
		}
		late++;
	}
	return -1;
}

/* The sensor loop. Outer loop: wait for START. Inner loop: 1 pass per frame. */
static void replay_thread(void *a, void *b, void *c)
{
	struct ehg_frame f;
	struct src_frame src;

	for (;;) {
		k_sem_take(&start_sem, K_FOREVER);
		for (;;) {
			k_spinlock_key_t key = k_spin_lock(&lock);
			bool run = running;
			uint32_t idx = n;
			int64_t due = t0 + ((int64_t)idx * CONFIG_SYS_CLOCK_TICKS_PER_SEC) / cfg.fs;

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

			if (take_frame(start_idx + idx, &src) != 0) {
				starved++; /* the laptop fell behind: this frame is skipped, never invented */
				continue;
			}
			f.tick = (uint32_t)now;
			f.frame_idx = src.frame_idx;
			f.start = first_pending;
			first_pending = false;
			memcpy(f.s, src.s, cfg.n_ch * sizeof(int32_t));
			if (k_msgq_put(&replay_ring, &f, K_NO_WAIT) != 0) {
				overflows++;
			}
		}
	}
}

/* Priority 2 beats ehg_service.c's 5 (lower = more urgent), so sending never delays the timing. */
K_THREAD_DEFINE(replay_tid, 1024, replay_thread, NULL, NULL, NULL, 2, 0, 0);

/* Getters for ehg_service.c (packet header fields + stats log). */
bool replay_running(void) { return running; }
uint32_t replay_overflows(void) { return overflows; }
uint32_t replay_starved(void) { return starved; }
uint32_t replay_late(void) { return late; }
uint32_t replay_rx_bad(void) { return rx_bad + feed_full; }
uint32_t replay_max_late_ticks(void) { return max_late; }
uint8_t replay_n_ch(void) { return cfg.n_ch; }
uint8_t replay_bits(void) { return cfg.bits; }
uint16_t replay_fs_hz(void) { return cfg.fs; }
uint16_t replay_record_id(void) { return cfg.record_id; }
