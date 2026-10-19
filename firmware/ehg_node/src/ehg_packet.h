/*
 * ehg_packet.h - byte layout of 1 radio packet. Copy of gateway/packet.py: change both together.
 *
 * PURPOSE: So board and laptop agree on what every byte means.
 * LAYOUT: 20-byte header, then the samples, frame by frame.
 *   header = version, n_ch, fs_hz, seq, frame_idx, t_inject, n_frames, flags, record_id
 *     seq = packet number, t_inject = board clock tick when the packet's 1st frame was sampled
 *   samples = 2 bytes each (default), or 3 bytes when flags bit1 is set (24-bit option)
 *   then a 2-byte CRC-16 of everything before it (v2)
 *   e.g. 3 channels x 10 frames = 20 + 60 + 2 = 82 bytes (24-bit: 112)
 *   Numbers go low byte first ("little-endian"). Full table: docs/interface-spec.md 3.
 * ROLE IN THESIS: The contract the whole pipeline shares. seq -> losses countable (H2);
 *   t_inject -> latency measurable (H1); raw samples -> bit-for-bit comparison (H2/H4).
 * BEFORE -> AFTER: replay.c fills struct ehg_frame (1 instant of samples) -> ehg_service.c builds
 *   struct ehg_pkt_hdr + samples, sends over Bluetooth -> gateway/packet.py decodes it.
  */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <zephyr/sys/util.h>
#include <zephyr/toolchain.h>

#define EHG_PKT_VERSION 2 /* v2 = v1 + CRC-16 trailer */
#define EHG_CRC_BYTES 2   /* CRC-16/CCITT-FALSE (crc16_itu_t, seed 0xFFFF) of all bytes before it, low byte first */
#define EHG_FLAG_RETRANS BIT(0)
#define EHG_FLAG_24BIT BIT(1) /* samples are 3 bytes each (the record is 24-bit, tools/feed.py CONFIG) */
#define EHG_MAX_PKT 244       /* MTU 247 - 3 bytes of ATT header */

/* Most channels any recording has (Icelandic ehgdb: 16). Sets the size of each queued frame. */
#define EHG_MAX_CH 16

/* The 20-byte header at the front of every packet. __packed = no hidden gap bytes. */
struct ehg_pkt_hdr {
	uint8_t version;
	uint8_t n_ch;
	uint16_t fs_hz;
	uint32_t seq;       /* packet number, from 0 at START */
	uint32_t frame_idx; /* frame number of the 1st frame, as numbered by tools/feed.py; recording row = frame_idx % length */
	uint32_t t_inject;  /* board clock (32768 ticks/s) when the 1st frame was released */
	uint8_t n_frames;
	uint8_t flags;
	uint16_t record_id; /* record_id in data/converted/<db>/manifest.json */
} __packed;

BUILD_ASSERT(sizeof(struct ehg_pkt_hdr) == 20, "wire header must be 20 bytes");

/* 1 frame (1 instant, all channels) on its way from replay.c to ehg_service.c. */
struct ehg_frame {
	uint32_t tick;
	uint32_t frame_idx;
	bool start; /* first frame of a session: ehg_service.c restarts seq and clears history */
	int32_t s[EHG_MAX_CH];
};

/* The 1-byte commands the laptop can send to the board. Sent by gateway/gateway.py,
 * handled in ehg_service.c:ctrl_write(). */
enum ehg_ctrl_op {
	EHG_OP_START = 0x01,  /* restart numbering, start the replay */
	EHG_OP_STOP = 0x02,   /* no args */
	EHG_OP_RESEND = 0x03, /* + 4-byte seq, 2-byte count: send these packets again */
};
