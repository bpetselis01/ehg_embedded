"""Checks tools/feed.py: messages survive the 0x00-delimited COBS framing, carry a CRC, frames keep
their numbering across the record loop, and only "@" lines are taken as board messages."""

import struct

import numpy as np
import pytest

from gateway.packet import crc16
from tools.feed import (MSG_CONFIG, MSG_DATA, MSG_SYNC, Feeder, cobs_decode, cobs_encode, config_msg, data_msg,
                        fault_msg, parse_at, parse_faults, sync_msg)


def unwrap(msg: bytes) -> bytes:
    """Wire message -> type + payload, checking the end marker and the CRC."""
    assert msg.endswith(b"\x00") and b"\x00" not in msg[:-1]
    body = cobs_decode(msg[:-1])
    assert struct.unpack("<H", body[-2:])[0] == crc16(body[:-2])
    return body[:-2]


@pytest.mark.parametrize("data", [b"", b"\x00", b"\x00\x00", bytes(range(256)) * 3, b"\x01" * 600])
def test_cobs_round_trip(data):
    enc = cobs_encode(data)
    assert b"\x00" not in enc
    assert cobs_decode(enc) == data


def test_cobs_rejects_truncated_block():
    with pytest.raises(ValueError):
        cobs_decode(bytes([5, 1, 2]))


def test_config_layout():
    assert unwrap(config_msg(3, 20, 0, 16, 0x9C2A)) == bytes([MSG_CONFIG, 3, 20, 0, 0, 0, 16, 0x2A, 0x9C])


def test_sync_layout():
    assert unwrap(sync_msg(7)) == bytes([MSG_SYNC, 7, 0, 0, 0])


def test_data_layout_16_and_24_bit():
    frames = np.array([[1093, 171, -748], [1145, 180, -790]], dtype=np.int16)
    body = unwrap(data_msg(7, frames, 16))
    assert body[:6] == bytes([MSG_DATA, 7, 0, 0, 0, 2])
    assert struct.unpack("<6h", body[6:]) == (1093, 171, -748, 1145, 180, -790)
    body24 = unwrap(data_msg(7, frames.astype(np.int32) * 100, 24))
    assert int.from_bytes(body24[6:9], "little", signed=True) == 109300
    assert len(body24) == 6 + 6 * 3


def test_numbering_continues_across_record_loop():
    src = np.arange(12, dtype=np.int16).reshape(4, 3)  # 4-frame record
    f = Feeder(src, fs=20, lead=2)
    f.take(3)
    first, frames = f.take(3)
    assert first == 3
    assert frames.tolist() == [src[3].tolist(), src[0].tolist(), src[1].tolist()]


def test_refill_to_target_once_per_level_report():
    f = Feeder(np.zeros((100, 3), np.int16), fs=20, lead=2)  # target 40 frames
    assert f.due() == 40
    f.take(10); f.take(10); f.take(10); f.take(10)
    assert f.due() == 0           # nothing more until the board reports
    f.on_level(22)                # board used 18 frames since
    assert f.due() == 18


def at(text: str) -> str:
    return f"@{text} #{crc16(text.encode()):04x}"


@pytest.mark.parametrize("line, want", [
    ("[00:00:01.000,000] <inf> replay: " + at("LVL 40"), ("LVL", ["40"])),
    (at("HELLO EHG-93C0 3 20 0"), ("HELLO", ["EHG-93C0", "3", "20", "0"])),
    (at("SYNC 7 81920"), ("SYNC", ["7", "81920"])),
    ("[00:00:10.000,000] <inf> ehg_svc: sent=11 resent=0", None),
    (at("WHAT 1"), None),          # unknown @ word: printed as an ordinary log line
    ("@LVL 40", None),             # no CRC
    (at("LVL 40").replace("40", "4"), None),  # bytes lost on the way: CRC no longer matches
])
def test_at_lines(line, want):
    assert parse_at(line) == want


def test_faults_map_to_gilbert_probabilities():
    f = parse_faults("drop=0.05,burst=3,seed=9")
    p, r = f["p_gb"] / 65536, f["p_bg"] / 65536
    assert abs(r - 1 / 3) < 1e-4
    assert abs(p / (p + r) - 0.05) < 1e-3  # long-run share of packets lost
    assert f["seed"] == 9 and f["corrupt"] == 0 and f["disc_s"] == 0


def test_no_faults_is_all_zero_and_bad_keys_rejected():
    assert parse_faults("") == {"seed": 1, "p_gb": 0, "p_bg": 65535, "corrupt": 0, "disc_s": 0}
    with pytest.raises(SystemExit):
        parse_faults("dorp=0.1")
    with pytest.raises(SystemExit):
        parse_faults("drop=1")


def test_fault_layout():
    body = unwrap(fault_msg(parse_faults("drop=0.05,burst=3,corrupt=0.01,disc=60,seed=1")))
    assert body[0] == 0x13 and len(body) == 13
    assert struct.unpack("<IHHHH", body[1:]) == (1, 1150, 21845, 655, 60)
