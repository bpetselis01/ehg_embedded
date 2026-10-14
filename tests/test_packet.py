"""Checks gateway/packet.py: pack -> parse returns the same packet, and short or
wrong-version packets are rejected (they would otherwise corrupt stored data)."""

import pytest

from gateway.packet import Packet, pack, parse


def make(samples, **kw):
    base = dict(version=1, n_ch=3, fs_hz=20, seq=7, frame_idx=70, t_inject=123456,
                n_frames=len(samples), flags=0, record_id=5, samples=samples)
    base.update(kw)
    return Packet(**base)


def test_roundtrip_with_negative_and_extreme_values():
    p = make([[-32768, 0, 32767], [-1, 2, -3]])
    b = pack(p)
    assert len(b) == 20 + 2 * 3 * 2
    assert parse(b) == p


def test_truncated_payload_rejected():
    b = pack(make([[1, 2, 3], [4, 5, 6]]))
    with pytest.raises(ValueError):
        parse(b[:-1])


def test_unknown_version_rejected():
    b = bytearray(pack(make([[1, 2, 3]])))
    b[0] = 2
    with pytest.raises(ValueError):
        parse(bytes(b))
