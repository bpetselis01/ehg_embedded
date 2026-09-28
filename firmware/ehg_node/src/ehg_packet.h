/*
 * ehg_packet.h - EHG wire format v1. Mirror of gateway/packet.py; change both together.
 * Field meanings: docs/interface-spec.md. Little-endian (native on Cortex-M4).
 */
#pragma once
#include <stdint.h>
#include <zephyr/sys/util.h>
#include <zephyr/toolchain.h>

#define EHG_PKT_VERSION 1
#define EHG_FLAG_RETRANS BIT(0)

/* Max channels any replay source uses (Icelandic EHGDB). Sizes the ring buffer items. */
#define EHG_MAX_CH 16

struct ehg_pkt_hdr {
	uint8_t version;
	uint8_t n_ch;
	uint16_t fs_hz;
	uint32_t seq;       /* packet counter from stream start */
	uint32_t frame_idx; /* absolute frame counter from stream start; source row = frame_idx % record_len */
	uint32_t t_inject;  /* k_uptime_ticks() (32768 Hz) when frame 0 was injected, truncated to u32 */
	uint8_t n_frames;
	uint8_t flags;
	uint16_t record_id; /* index in data/converted/<db>/manifest.json */
} __packed;

BUILD_ASSERT(sizeof(struct ehg_pkt_hdr) == 20, "wire header must be 20 bytes");

/* One injected frame travelling from replay.c to the packetiser. */
struct ehg_frame {
	uint32_t tick;
	uint32_t frame_idx;
	int16_t s[EHG_MAX_CH];
};

/* Control characteristic opcodes (gateway -> node) */
enum ehg_ctrl_op {
	EHG_OP_START = 0x01,  /* no args: reset seq/frame counters and start replay */
	EHG_OP_STOP = 0x02,   /* no args */
	EHG_OP_RESEND = 0x03, /* u32 seq, u16 count */
};
