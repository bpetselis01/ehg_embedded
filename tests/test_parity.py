"""Checks eval/parity.py: it passes for identical stores, and fails (non-zero exit) for each kind of difference it exists to catch: a missing frame, a changed sample bit,
a different missing range, a missing packet, a changed timing field, and a latency shift beyond the tolerance."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "eval"))
from parity import compare, missing_ranges  # noqa: E402


def stores(n=20, lat=0.26):
    frames = {i: [i, -i, 1000 + i] for i in range(n) if i not in (7, 8)}
    pkts = [{"seq": s, "n_frames": 10, "retrans": False, "replayed": False, "t_inject_dev": s * 16384, "t_inject_host": 1000.0 + s * 0.5, "t_gw_rx": 1000.4 + s * 0.5,
             "t_mqtt_pub": 1000.41 + s * 0.5, "t_ingest_rx": 1000.42 + s * 0.5, "t_db_ack": 1000.0 + s * 0.5 + lat + (s % 5) * 0.01} for s in range(10)]
    return frames, pkts


def failed(checks):
    return [name for name, ok, _ in checks if not ok]


def test_missing_ranges():
    assert missing_ranges({0: [0], 1: [0], 5: [0]}) == [(2, 4)] and missing_ranges({}) == [] and missing_ranges({3: [0], 4: [0]}) == []
    assert missing_ranges({0: [0], 3: [0], 7: [0]}) == [(1, 2), (4, 6)]


def test_identical_stores_pass_every_check():
    fa, pa = stores()
    fb, pb = copy.deepcopy(fa), copy.deepcopy(pa)
    for p in pb:
        p["t_db_ack"] += 0.003  # a different writer stamps a few ms differently: allowed
        p["t_ingest_rx"] += 0.002
    assert failed(compare(fa, fb, pa, pb, tolerance_ms=50)) == []


def test_a_missing_frame_is_caught():
    fa, pa = stores()
    fb, pb = copy.deepcopy(fa), copy.deepcopy(pa)
    del fb[3]
    assert "same frame numbers" in failed(compare(fa, fb, pa, pb)) and "same missing ranges" in failed(compare(fa, fb, pa, pb))


def test_a_single_flipped_sample_bit_is_caught():
    fa, pa = stores()
    fb, pb = copy.deepcopy(fa), copy.deepcopy(pa)
    fb[5][2] ^= 1
    assert failed(compare(fa, fb, pa, pb)) == ["samples bit-identical"]


def test_a_missing_packet_and_a_changed_timing_field_are_caught():
    fa, pa = stores()
    fb, pb = copy.deepcopy(fa), copy.deepcopy(pa)
    pb.pop(4)
    assert "same packet seq set" in failed(compare(fa, fb, pa, pb))
    fb, pb = copy.deepcopy(fa), copy.deepcopy(pa)
    pb[2]["t_gw_rx"] += 0.01
    assert failed(compare(fa, fb, pa, pb)) == ["static timing fields equal"]
    pb = copy.deepcopy(pa)
    pb[2]["retrans"] = True
    assert "static timing fields equal" in failed(compare(fa, fb, pa, pb))


def test_a_latency_shift_beyond_the_tolerance_is_caught_and_within_it_is_not():
    fa, pa = stores(lat=0.26)
    _, pb = stores(lat=0.34)
    assert any(name.startswith("H1 median") for name in failed(compare(fa, fa, pa, pb, tolerance_ms=50)))
    assert failed(compare(fa, fa, pa, pb, tolerance_ms=200)) == []


def test_no_live_packets_is_a_failure_not_a_pass():
    fa, pa = stores()
    empty = [dict(p, replayed=True) for p in pa]
    assert "H1 latency comparable" in failed(compare(fa, fa, pa, empty))
