"""EHG wire format v1 - mirror of firmware/ehg_node/src/ehg_packet.h. Change both together.

Little-endian. 20-byte header then n_frames * n_ch int16 samples, frame-major.
See docs/interface-spec.md for field meanings.

PURPOSE: Turn the raw bytes of one radio packet into named Python values, and back.
HOW: `struct` reads/writes fixed-size numbers at fixed byte positions. The format string
  "<BBHIIIBBH" lists the header fields in order (B = 1 byte, H = 2 bytes, I = 4 bytes,
  < = little-endian). The samples after the header are 2-byte signed numbers ("h").
ROLE IN THESIS: The laptop's copy of the shared packet contract. It checks length and version,
  so a damaged packet is rejected rather than silently stored (H2/H4 integrity).
BEFORE -> AFTER: Bytes come from the board (firmware/ehg_node/src/ehg_packet.h layout) or from
  the simulator in gateway.py. parse() is used by gateway.py:Node.on_bytes; pack() by the
  simulator and tests/test_packet.py.
"""

import struct
from dataclasses import dataclass

VERSION = 1
HEADER = struct.Struct("<BBHIIIBBH")  # version n_ch fs_hz seq frame_idx t_inject n_frames flags record_id
FLAG_RETRANS = 0x01
TICK_HZ = 32768


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


def parse(b: bytes) -> Packet:
    """Bytes -> Packet. Raises ValueError if too short, wrong version or wrong length."""
    if len(b) < HEADER.size:
        raise ValueError(f"packet too short: {len(b)} bytes")
    h = HEADER.unpack_from(b)
    if h[0] != VERSION:
        raise ValueError(f"unknown version {h[0]}")
    n_ch, n_frames = h[1], h[6]
    n = n_ch * n_frames
    if len(b) != HEADER.size + 2 * n:
        raise ValueError(f"expected {HEADER.size + 2 * n} bytes, got {len(b)}")
    flat = struct.unpack_from(f"<{n}h", b, HEADER.size)
    return Packet(*h, samples=[list(flat[i:i + n_ch]) for i in range(0, n, n_ch)])


def pack(p: Packet) -> bytes:
    """Packet -> bytes, the exact inverse of parse() (same layout the firmware sends)."""
    flat = [v for frame in p.samples for v in frame]
    return HEADER.pack(p.version, p.n_ch, p.fs_hz, p.seq, p.frame_idx, p.t_inject,
                       p.n_frames, p.flags, p.record_id) + struct.pack(f"<{len(flat)}h", *flat)
