"""Checks gateway/packet.py: pack -> parse returns the same packet, and short or
wrong-version packets are rejected (they would otherwise corrupt stored data)."""

import pytest

from gateway.packet import FLAG_24BIT, VERSION, Packet, crc16, pack, parse


def make(samples, **kw):
    base = dict(version=VERSION, n_ch=3, fs_hz=20, seq=7, frame_idx=70, t_inject=123456,
                n_frames=len(samples), flags=0, record_id=5, samples=samples)
    base.update(kw)
    return Packet(**base)


def test_roundtrip_with_negative_and_extreme_values():
    p = make([[-32768, 0, 32767], [-1, 2, -3]])
    b = pack(p)
    assert len(b) == 20 + 2 * 3 * 2 + 2
    assert parse(b) == p


def test_truncated_payload_rejected():
    b = pack(make([[1, 2, 3], [4, 5, 6]]))
    with pytest.raises(ValueError):
        parse(b[:-1])


def test_unknown_version_rejected():
    b = bytearray(pack(make([[1, 2, 3]])))
    b[0] = 1  # v1 had no CRC: must not be read as v2
    with pytest.raises(ValueError):
        parse(bytes(b))


def test_24bit_roundtrip_with_extremes():
    p = make([[-(2**23), 0, 2**23 - 1], [-1, 70000, -70000]], flags=FLAG_24BIT)
    b = pack(p)
    assert len(b) == 20 + 2 * 3 * 3 + 2
    assert parse(b) == p


def test_24bit_flag_survives_retransmission_flag():
    p = make([[123456, -123456, 7]], flags=FLAG_24BIT | 0x01)
    assert parse(pack(p)) == p


def test_16bit_default_layout_unchanged():
    # 2 bytes per sample, little-endian: what the firmware has always sent
    b = pack(make([[1, -2, 258]]))
    assert b[20:-2] == bytes([1, 0, 0xFE, 0xFF, 2, 1])


def test_length_checked_against_sample_width():
    b = pack(make([[1, 2, 3]]))  # 16-bit body
    hdr = bytearray(b)
    hdr[17] |= FLAG_24BIT       # claims 24-bit: body length no longer matches
    with pytest.raises(ValueError):
        parse(bytes(hdr))


def test_crc_matches_firmware_vector():
    # CRC-16/CCITT-FALSE check value; firmware crc16_itu_t(0xFFFF, "123456789", 9) gives the same
    assert crc16(b"123456789") == 0x29B1


def test_any_flipped_bit_rejected():
    b = pack(make([[1093, 171, -748], [1145, 180, -790]]))
    for i in range(len(b) * 8):
        bad = bytearray(b)
        bad[i // 8] ^= 1 << (i % 8)
        with pytest.raises(ValueError):
            parse(bytes(bad))
