/*
 * ehg_packet.h - EHG wire format v1. Mirror of gateway/packet.py; change both together.
 * Field meanings, offsets: docs/interface-spec.md 3. Little-endian (native on Cortex-M4).
 *
 * PURPOSE: Exact byte layout of 1 radio packet, so board and laptop agree on every byte.
 * LAYOUT (one packet): 20-byte header + n_frames x n_ch samples, frame-major.
 *   Samples int16 by default; 3-byte signed when flags bit1 (EHG_FLAG_24BIT) is set.
 *   e.g. 3 ch x 10 frames = 20 + 60 = 80 bytes (24-bit: 110)
 *   header = version, n_ch, fs_hz, seq, frame_idx, t_inject, n_frames, flags, record_id
 *   (seq = packet number, t_inject = board clock tick when frame 0 was sampled)
 * HOW: Fixed 20-byte header (__packed, no padding), then samples as 16-bit whole numbers.
 *   "Little-endian" = low byte of each number sent first.
 * ROLE IN THESIS: Shared contract of the whole pipeline. seq makes losses countable (H2); t_inject
 *   makes latency measurable (H1); samples stay raw so they compare bit-for-bit with the source (H2/H4).
 * BEFORE -> AFTER: src/replay.c fills `struct ehg_frame` (1 instant of samples) -> src/ehg_service.c
 *   packs frames into `struct ehg_pkt_hdr` + samples, sends over Bluetooth -> gateway/packet.py
 *   decodes the same layout.
 */
#pragma once
#include <stdint.h>
#include <zephyr/sys/util.h>
#include <zephyr/toolchain.h>

#include "replay_data.h" /* REPLAY_SAMPLE_BITS: sample width is a property of the baked-in record */

#define EHG_PKT_VERSION 1
#define EHG_FLAG_RETRANS BIT(0)
#define EHG_FLAG_24BIT BIT(1) /* samples are 3 bytes each (opt-in via make_replay.py --bits 24) */

#if REPLAY_SAMPLE_BITS == 24
typedef int32_t ehg_sample_t;
#define EHG_SAMPLE_BYTES 3
#define EHG_SAMPLE_FLAGS EHG_FLAG_24BIT
#elif REPLAY_SAMPLE_BITS == 16
typedef int16_t ehg_sample_t;
#define EHG_SAMPLE_BYTES 2
#define EHG_SAMPLE_FLAGS 0
#else
#error "REPLAY_SAMPLE_BITS must be 16 or 24 - regenerate replay_data.h with tools/make_replay.py"
#endif

/* Max channels any replay source uses (Icelandic EHGDB). Sizes the ring buffer items. */
#define EHG_MAX_CH 16

/* The 20-byte label at the front of every packet. __packed = no hidden padding bytes. */
struct ehg_pkt_hdr {
	uint8_t version;
	uint8_t n_ch;
	uint16_t fs_hz;
	uint32_t seq;       /* packet counter from stream start */
	uint32_t frame_idx; /* absolute frame counter from stream start; source row = frame_idx % record_len */
	uint32_t t_inject;  /* k_uptime_ticks() (32768 Hz) when frame 0 was injected, truncated to u32 */
	uint8_t n_frames;
	uint8_t flags;
	uint16_t record_id; /* record_id in data/converted/<db>/manifest.json */
} __packed;

BUILD_ASSERT(sizeof(struct ehg_pkt_hdr) == 20, "wire header must be 20 bytes");

/* One injected frame travelling from replay.c to the packetiser. */
struct ehg_frame {
	uint32_t tick;
	uint32_t frame_idx;
	ehg_sample_t s[EHG_MAX_CH];
};

/* Control characteristic opcodes (gateway -> node): the 1-byte commands the laptop can send
 * back to the board. Handled in ehg_service.c:ctrl_write(); sent by gateway/gateway.py. */
enum ehg_ctrl_op {
	EHG_OP_START = 0x01,  /* no args: reset seq/frame counters and start replay */
	EHG_OP_STOP = 0x02,   /* no args */
	EHG_OP_RESEND = 0x03, /* u32 seq, u16 count */
};
