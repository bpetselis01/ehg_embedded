"""Guards the third copy of the wire format (Swift, ios/EHGWire): the golden vectors are exactly what gateway/packet.py produces today, every vector parses the way the file says, the Swift constants and field offsets match the Python ones,
and (opt-in, EHG_INTEGRATION=1, needs the Swift toolchain) `swift test` itself passes. Nothing here touches Xcode, a simulator or a phone."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gateway import packet
from gateway.tracking import NEW_STREAM_BACKSTEP, ClockMap, GapTracker
from tools.gen_packet_vectors import OUT, build

ROOT = Path(__file__).parents[1]
SWIFT = (ROOT / "ios/EHGWire/Sources/EHGWire/Packet.swift").read_text()
VECTORS = json.loads(OUT.read_text())


def test_the_vector_file_is_exactly_what_the_python_reference_produces_now():
    assert json.loads(json.dumps(build())) == VECTORS, "run: uv run python tools/gen_packet_vectors.py"


def test_every_valid_vector_parses_to_the_recorded_values_and_repacks_to_the_same_bytes():
    assert len(VECTORS["valid"]) >= 8
    for v in VECTORS["valid"]:
        raw = bytes.fromhex(v["hex"])
        assert packet.parse(raw).__dict__ == v["packet"], v["name"]
        assert packet.pack(packet.Packet(**v["packet"])) == raw, v["name"]


def test_every_invalid_vector_is_refused_with_the_recorded_reason():
    kinds = {"too short": "too_short", "unknown version": "unknown_version", "expected": "wrong_length", "crc mismatch": "crc_mismatch"}
    for v in VECTORS["invalid"]:
        with pytest.raises(ValueError) as e:
            packet.parse(bytes.fromhex(v["hex"]))
        assert next(k for m, k in kinds.items() if m in str(e.value)) == v["error"], v["name"]
    assert {v["error"] for v in VECTORS["invalid"]} == set(kinds.values()), "every refusal reason has a vector"


def test_the_vectors_cover_the_cases_that_matter():
    names = {v["name"] for v in VECTORS["valid"]}
    assert {"24bit_samples", "retransmission_flag", "no_frames", "max_field_values", "one_channel"} <= names
    extremes = {s for v in VECTORS["valid"] for f in v["packet"]["samples"] for s in f}
    assert {-32768, 32767, -8388608, 8388607} <= extremes


def test_swift_constants_match_the_python_reference():
    def swift(name):
        return re.search(rf"static let {name}(?::\s*\w+)?\s*=\s*(0x[0-9A-Fa-f]+|\d+)", SWIFT).group(1)
    assert int(swift("version"), 0) == packet.VERSION
    assert int(swift("headerSize")) == packet.HEADER.size == 20
    assert int(swift("crcSize")) == packet.CRC.size
    assert int(swift("flagRetransmission"), 0) == packet.FLAG_RETRANS and int(swift("flag24Bit"), 0) == packet.FLAG_24BIT
    assert int(swift("tickHz")) == packet.TICK_HZ
    assert int(swift("crcInitial"), 0) == 0xFFFF and int(swift("crcPolynomial"), 0) == 0x1021


def test_swift_reads_every_header_field_at_the_python_offset():
    offsets, at = {}, 0
    for name, size in zip(("version", "n_ch", "fs_hz", "seq", "frame_idx", "t_inject", "n_frames", "flags", "record_id"), (1, 1, 2, 4, 4, 4, 1, 1, 2)):
        offsets[name], at = (at, size), at + size
    assert at == packet.HEADER.size
    expected = {"fsHz": "le(b, 2, 2)", "seq": "le(b, 4, 4)", "frameIdx": "le(b, 8, 4)", "tInject": "le(b, 12, 4)", "recordId": "le(b, 18, 2)"}
    for _, call in expected.items():
        assert call in SWIFT, call
    for key, (off, size) in {"fs_hz": offsets["fs_hz"], "seq": offsets["seq"], "frame_idx": offsets["frame_idx"], "t_inject": offsets["t_inject"], "record_id": offsets["record_id"]}.items():
        assert f"le(b, {off}, {size})" in SWIFT, key
    assert "b[16]" in SWIFT and "b[17]" in SWIFT and offsets["n_frames"][0] == 16 and offsets["flags"][0] == 17


def test_swift_checks_in_the_same_order_as_python():
    order = [SWIFT.index(x) for x in ("throw PacketError.tooShort", "throw PacketError.unknownVersion", "throw PacketError.wrongLength", "throw PacketError.crcMismatch")]
    assert order == sorted(order)


def test_swift_test_suite_reads_the_shared_vectors():
    t = (ROOT / "ios/EHGWire/Tests/EHGWireTests/PacketTests.swift").read_text()
    assert "tests/fixtures/packet_vectors.json" in t and "testEveryInvalidVectorIsRefusedForTheSameReason" in t


@pytest.mark.skipif(os.environ.get("EHG_INTEGRATION") != "1" or shutil.which("swift") is None, reason="needs EHG_INTEGRATION=1 and the Swift toolchain")
def test_swift_test_passes():
    r = subprocess.run(["swift", "test"], cwd=ROOT / "ios/EHGWire", capture_output=True, text=True, timeout=600)
    m = re.search(r"Executed (\d+) tests, with 0 failures", r.stdout + r.stderr)
    assert r.returncode == 0 and m and int(m.group(1)) >= 9, (r.stdout + r.stderr)[-2000:]


TRACKING = VECTORS["tracking"]
TRACKING_SWIFT = (ROOT / "ios/EHGWire/Sources/EHGWire/Tracking.swift").read_text()


def test_tracking_vectors_replay_in_python_exactly():
    assert len(TRACKING["gap"]) >= 9 and len(TRACKING["clock"]) >= 4
    for c in TRACKING["gap"]:
        t = GapTracker()
        assert [t.observe(q) for q in c["seqs"]] == c["returns"], c["name"]
        assert {"last": t.last, "missing": sorted(t.missing), "received": t.received, "duplicates": t.duplicates, "streams": t.streams} == c["final"], c["name"]
    for c in TRACKING["clock"]:
        m = ClockMap()
        for ticks, host in c["updates"]:
            m.update(ticks, host)
        assert m.offset == c["offset"] and all(m.to_host(q["ticks"]) == q["host"] for q in c["queries"]), c["name"]


def test_tracking_vectors_cover_the_rules_that_matter():
    names = {c["name"] for c in TRACKING["gap"]} | {c["name"] for c in TRACKING["clock"]}
    assert {"gap_then_late_fill", "duplicate_ignored", "backstep_over_1000_is_a_new_stream", "near_uint32_limit", "tick_counter_wraps", "keeps_the_smallest_offset"} <= names
    assert max(len(r) for c in TRACKING["gap"] for r in c["returns"]) <= 100, "keep the fixture small: no case should need a huge gap list"


def test_swift_tracking_constants_match_python():
    assert re.search(r"newStreamBackstep = (\d+)", TRACKING_SWIFT).group(1) == str(NEW_STREAM_BACKSTEP)
    assert "1 << 32" in TRACKING_SWIFT and "ClockMap.wrap / 2" in TRACKING_SWIFT
    assert "seq >= previous - GapTracker.newStreamBackstep" in TRACKING_SWIFT, "the new-stream rule is the one in gateway/tracking.py"

