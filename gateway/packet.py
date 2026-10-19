"""EHG wire format v2 (v1 + CRC-16 trailer). Mirror of firmware/ehg_node/src/ehg_packet.h. Change both together.

PURPOSE: Radio packet bytes <-> named Python values.
INPUT: parse(bytes): one BLE packet, little-endian. 20-byte header, then n_frames x n_ch samples:
    int16 (default), or 3-byte signed when flags bit1 (FLAG_24BIT) is set, then a 2-byte CRC-16.
    e.g. 3 ch x 10 frames = 20 + 60 + 2 = 82 bytes (24-bit: 20 + 90 + 2 = 112).
    e.g. 02 03 14 00 00 00 00 00 00 00 00 00 00 80 01 00 0a 00 00 00 45 04 ab 00 14 fd 79 04 ... 78 bb
OUTPUT: Packet(version, n_ch, fs_hz, seq, frame_idx, t_inject, n_frames, flags, record_id,
    samples=[[ch1, ch2, ch3], ...]). pack() does the reverse.
    e.g. Packet(version=2, n_ch=3, fs_hz=20, seq=0, frame_idx=0, t_inject=98304, n_frames=10,
                flags=0, record_id=0, samples=[[1093, 171, -748], [1145, 180, -790], ...])
    e.g. bad input -> ValueError("packet too short: 10 bytes") / ("expected 82 bytes, got 81") / ("crc mismatch")
  Field meanings, byte offsets: docs/interface-spec.md 3.
HOW: `struct` reads fixed-size numbers at fixed byte positions. Format "<BBHIIIBBH" = header fields
  in order (B 1 byte, H 2, I 4, < little-endian). Samples after it are 2-byte signed ("h"), or 3-byte.
ROLE IN THESIS: Laptop copy of the shared packet contract. Checks length + version + CRC, so a damaged
  packet is rejected, not silently stored (H2/H4).
BEFORE -> AFTER: Bytes from the board (layout: firmware/ehg_node/src/ehg_packet.h) or the simulator
  in gateway/gateway.py. parse() used by gateway.py:Node.on_bytes; pack() by the simulator and
  tests/test_packet.py.
"""

import binascii
import struct
from dataclasses import dataclass

VERSION = 2  # v2 = v1 + CRC-16 trailer
HEADER = struct.Struct("<BBHIIIBBH")  # version n_ch fs_hz seq frame_idx t_inject n_frames flags record_id
FLAG_RETRANS = 0x01
FLAG_24BIT = 0x02  # samples are 3 bytes each instead of 2 (opt-in; 16-bit is the default)
TICK_HZ = 32768
CRC = struct.Struct("<H")  # trailer: CRC-16/CCITT-FALSE of everything before it


def crc16(b: bytes) -> int:
    """CRC-16/CCITT-FALSE, same as Zephyr crc16_itu_t(0xFFFF, ...) in the firmware."""
    return binascii.crc_hqx(b, 0xFFFF)


@dataclass
class Packet:
    """One decoded packet: header fields plus samples as a list of frames (each a list of channels)."""

    version: int
    n_ch: int
    fs_hz: int
    seq: int
    frame_idx: int
    t_inject: int  # device ticks (TICK_HZ) when frame 0 was injected
    n_frames: int
    flags: int
    record_id: int
    samples: list[list[int]]  # [n_frames][n_ch]


def sample_bytes(flags: int) -> int:
    """2 bytes per sample, or 3 when the 24-bit flag is set."""
    return 3 if flags & FLAG_24BIT else 2


def parse(b: bytes) -> Packet:
    """Bytes -> Packet. Raises ValueError if too short, wrong version, wrong length or bad CRC."""
    if len(b) < HEADER.size:
        raise ValueError(f"packet too short: {len(b)} bytes")
    h = HEADER.unpack_from(b)
    if h[0] != VERSION:
        raise ValueError(f"unknown version {h[0]}")
    n_ch, n_frames, w = h[1], h[6], sample_bytes(h[7])
    n = n_ch * n_frames
    if len(b) != HEADER.size + w * n + CRC.size:
        raise ValueError(f"expected {HEADER.size + w * n + CRC.size} bytes, got {len(b)}")
    if CRC.unpack_from(b, len(b) - CRC.size)[0] != crc16(b[:-CRC.size]):
        raise ValueError("crc mismatch")
    if w == 2:
        flat = struct.unpack_from(f"<{n}h", b, HEADER.size)
    else:
        body = b[HEADER.size:-CRC.size]
        flat = [int.from_bytes(body[i:i + 3], "little", signed=True) for i in range(0, 3 * n, 3)]
    return Packet(*h, samples=[list(flat[i:i + n_ch]) for i in range(0, n, n_ch)])


def pack(p: Packet) -> bytes:
    """Packet -> bytes, the exact inverse of parse() (same layout the firmware sends)."""
    flat = [v for frame in p.samples for v in frame]
    head = HEADER.pack(p.version, p.n_ch, p.fs_hz, p.seq, p.frame_idx, p.t_inject,
                       p.n_frames, p.flags, p.record_id)
    if sample_bytes(p.flags) == 2:
        body = head + struct.pack(f"<{len(flat)}h", *flat)
    else:
        body = head + b"".join(int(v).to_bytes(3, "little", signed=True) for v in flat)
    return body + CRC.pack(crc16(body))
