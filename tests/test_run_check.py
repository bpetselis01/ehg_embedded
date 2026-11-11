"""eval/run_check.py:evaluate on built rows, so the pass/fail thresholds are tested without a board or InfluxDB."""

import sqlite3
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import run_check as rc  # noqa: E402

from gateway.packet import TICK_HZ  # noqa: E402

FS = 20.0
SRC = np.random.default_rng(1).integers(-2000, 2000, size=(500, 3))
T0 = 1_800_000_000.0


def frames(lo=0, hi=3000, skip=()):
    """Frames lo..hi-1 as stored, looping the source like the feeder does (row = frame_idx % length)."""
    return {i: SRC[i % len(SRC)].tolist() for i in range(lo, hi) if i not in skip}


def pkts(n=300, ack_delay=0.4, **extra):
    """One packet per 10 frames, received 50 ms after its last frame, stored ack_delay later."""
    return [{"n_frames": 10, "t_inject_dev": int(k * 10 / FS * TICK_HZ), "t_gw_rx": T0 + (k * 10 + 9) / FS + 0.05,
             "t_db_ack": T0 + (k * 10 + 9) / FS + 0.05 + ack_delay, "retrans": False, **extra} for k in range(n)]


def by_name(checks):
    return {c.name: c for c in checks}


def end(p):
    return p[-1]["t_db_ack"] + 1


def test_a_healthy_run_passes_every_check():
    p = pkts()
    checks = rc.evaluate(frames(), SRC, FS, p, end(p), outbox_start=0, outbox_now=0)
    assert [c.name for c in checks] == ["arriving", "delivery", "bit-exact", "p95", "outbox"]
    assert all(c.ok for c in checks), rc.line(checks)
    assert by_name(checks)["delivery"].detail == "100.00 %"


def test_a_gap_over_one_percent_fails_delivery_and_names_the_range():
    p = pkts()
    c = by_name(rc.evaluate(frames(skip=range(100, 131)), SRC, FS, p, end(p)))["delivery"]
    assert not c.ok and "missing 100..130" in c.detail


def test_the_newest_frames_are_not_judged_while_live():
    p = pkts()
    c = by_name(rc.evaluate(frames(skip=range(2850, 3000)), SRC, FS, p, end(p)))["delivery"]
    assert c.ok, "the last 10 s (200 frames) may still be in flight"
    c = by_name(rc.evaluate(frames(skip=range(2850, 2999)), SRC, FS, p, end(p), settle_s=0))["delivery"]
    assert not c.ok, "with no settle margin (end of the hardware test) the same gap counts"


def test_a_window_that_starts_mid_stream_and_spans_a_record_loop_still_lines_up():
    p = pkts()
    checks = by_name(rc.evaluate(frames(lo=777, hi=2777), SRC, FS, p, end(p)))
    assert checks["delivery"].ok and checks["bit-exact"].ok


def test_one_changed_sample_fails_bit_exact():
    f = frames()
    f[1234] = [f[1234][0] + 1, *f[1234][1:]]
    p = pkts()
    c = by_name(rc.evaluate(f, SRC, FS, p, end(p)))["bit-exact"]
    assert not c.ok and c.detail == "1 (first at frame 1234)"


def test_a_stalled_run_fails_arriving():
    p = pkts()
    assert not by_name(rc.evaluate(frames(), SRC, FS, p, end(p) + 30))["arriving"].ok


def test_slow_storage_fails_latency_but_resends_and_replays_do_not_count():
    assert not by_name(rc.evaluate(frames(), SRC, FS, pkts(ack_delay=3.0), end(pkts(ack_delay=3.0))))["p95"].ok
    late = pkts(n=20, ack_delay=30.0, replayed=True) + pkts(n=20, ack_delay=30.0, retrans=True)
    p = pkts() + late
    assert by_name(rc.evaluate(frames(), SRC, FS, p, end(pkts())))["p95"].ok


def test_no_data_fails_every_data_check_without_raising():
    checks = by_name(rc.evaluate(None, None, FS, [], T0))
    assert [checks[n].ok for n in ("arriving", "delivery", "bit-exact", "p95")] == [False] * 4
    assert checks["delivery"].detail == "no data" and checks["p95"].detail == "no packets"


def test_outbox_must_drain_not_grow():
    p = pkts()
    assert not by_name(rc.evaluate(frames(), SRC, FS, p, end(p), outbox_start=0, outbox_now=50))["outbox"].ok
    assert by_name(rc.evaluate(frames(), SRC, FS, p, end(p), outbox_start=None, outbox_now=None))["outbox"].detail == "none"
    assert not by_name(rc.evaluate(frames(), SRC, FS, p, end(p), outbox_unreadable=True))["outbox"].ok


def test_outbox_pending_reads_the_count_read_only(tmp_path):
    db = tmp_path / "outbox.db"
    assert rc.outbox_pending(db) is None
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE outbox(id INTEGER PRIMARY KEY, payload TEXT)")
    con.executemany("INSERT INTO outbox(payload) VALUES (?)", [("a",), ("b",)])
    con.commit()
    con.close()
    assert rc.outbox_pending(db) == 2


def test_a_failing_line_puts_the_failures_first():
    p = pkts()
    text = rc.line(rc.evaluate(frames(skip=range(100, 131)), SRC, FS, p, end(p)))
    assert "FAIL  delivery" in text and "PASS" not in text
