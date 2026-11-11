"""Pass/fail health of a run, live beside a demo or once at the end of the hardware test.

Usage: uv run python eval/run_check.py --watch 30            (one line every 30 s until Ctrl-C)
       uv run python eval/run_check.py --once --window 5m    (exit 0 if every check passes, 1 if not)

PURPOSE: Answer "is this run healthy right now?" with the thesis targets, instead of watching seven windows.
INPUT: the node's latest run in InfluxDB (eval/backend_client.py, latest_run so two runs never mix), the source recording named
  by its record_id (data/converted/<db>/manifest.json), and the gateway outbox (gateway/outbox.db, opened read-only).
OUTPUT: one line per evaluation, e.g.
    12:04:30  PASS  arriving 0.4 s | delivery 100.00 % | bit-exact 0 | p95 471 ms | outbox 0
    12:05:00  FAIL  delivery 97.20 % (missing 1203..1240) | ...
HOW: evaluate() is pure: frames, source, packets, outbox counts and the time in, a list of Check out. The checks reuse
  eval/integrity.py:reconcile (H2 delivery, H4 bit-exact) and eval/latency.py:per_sample_latencies (H1, BLE-envelope offset,
  which reads low by up to one connection interval: fine for a 2 s threshold, eval/latency.py gives the exact number).
  Live, the newest 10 s of frames are not judged yet: they may still be in flight or waiting for a resend.
ROLE IN THESIS: The live monitor, and the assertion of tests/test_hardware_default_run.py. Writes nothing.
BEFORE -> AFTER: Before: a run feeding InfluxDB. After: a PASS/FAIL line per check. Tested by tests/test_run_check.py.
"""

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from integrity import reconcile  # noqa: E402
from latency import per_sample_latencies, without_replayed  # noqa: E402

DELIVERY_MIN_PCT = 99.0   # H2
LATENCY_P95_MAX_S = 2.0   # H1
ARRIVING_MAX_AGE_S = 10.0
OUTBOX_MAX_GROWTH = 20    # pending packets; the outbox should drain, not grow
SETTLE_S = 10.0           # live only: newest frames not judged yet


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _delivery_checks(frames: dict[int, list[int]] | None, src: np.ndarray | None, fs: float, settle_s: float) -> list[Check]:
    if not frames or src is None:
        return [Check("delivery", False, "no data"), Check("bit-exact", False, "no data")]
    lo, hi = min(frames), max(frames) - int(settle_s * fs)
    if hi < lo:
        return [Check("delivery", True, "too early to judge"), Check("bit-exact", True, "too early to judge")]
    shifted = {i - lo: v for i, v in frames.items() if i <= hi}
    r = reconcile(shifted, np.roll(src, -(lo % len(src)), axis=0), hi - lo + 1)  # same alignment as integrity.main
    gaps = ", ".join(f"{a + lo}..{b + lo}" for a, b in r["missing_ranges"][:3])
    pct = r["delivery_pct"]
    return [Check("delivery", pct > DELIVERY_MIN_PCT, f"{pct:.2f} %" + (f" (missing {gaps})" if gaps else "")),
            Check("bit-exact", not r["mismatches"], f"{len(r['mismatches'])}"
                  + (f" (first at frame {r['mismatches'][0] + lo})" if r["mismatches"] else ""))]


def _latency_check(pkts: list[dict], fs: float) -> Check:
    live = without_replayed([p for p in pkts if not p.get("retrans")])  # resends and outbox replays are not H1 latency
    if not live:
        return Check("p95", False, "no packets")
    p95 = float(np.percentile(per_sample_latencies([dict(p) for p in live], fs), 95))
    return Check("p95", p95 < LATENCY_P95_MAX_S, f"{p95 * 1000:.0f} ms")


def _outbox_check(start: int | None, now: int | None, unreadable: bool) -> Check:
    if unreadable:
        return Check("outbox", False, "unreadable")
    if now is None:
        return Check("outbox", True, "none")  # gateway run with --no-outbox, or never started
    grew = now - (start or 0)
    return Check("outbox", grew <= OUTBOX_MAX_GROWTH, f"{now}" + (f" (+{grew})" if grew > 0 else ""))


def evaluate(frames, src, fs, pkts, now, outbox_start=None, outbox_now=None, outbox_unreadable=False, settle_s=SETTLE_S) -> list[Check]:
    """Every check for one window. frames: frame_idx -> samples (or None); src: the source recording rows; pkts: ehg_pkt rows."""
    acks = [p["t_db_ack"] for p in pkts]
    age = now - max(acks) if acks else None
    arriving = Check("arriving", age is not None and age < ARRIVING_MAX_AGE_S, "no packets" if age is None else f"{age:.1f} s")
    return [arriving, *_delivery_checks(frames, src, fs, settle_s), _latency_check(pkts, fs), _outbox_check(outbox_start, outbox_now, outbox_unreadable)]


def outbox_pending(path: Path) -> int | None:
    """Packets waiting in the outbox; None if there is no outbox file. Read-only: the gateway may be writing (WAL)."""
    if not path.exists():
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    try:
        return con.execute("SELECT count(*) FROM outbox").fetchone()[0]
    finally:
        con.close()


def gather(node: str, window: str, db: str, outbox: Path, outbox_start: int | None, settle_s: float) -> list[Check]:
    """Query the latest run in the window and evaluate it."""
    from backend_client import query_frames, query_pkts
    from tools.convert import source_file

    try:
        record_id, frames, first = query_frames(node, window, latest_run=True)
        fs, src = _source(Path(f"data/converted/{db}"), record_id, source_file)
        pkts = [p for p in query_pkts(node, window) if p["_time"] >= first]  # this run's packets only (both are stamped at injection)
    except SystemExit:  # backend_client's "no data in window"
        frames, src, fs, pkts = None, None, 20.0, []
    try:
        now_pending, unreadable = outbox_pending(outbox), False
    except sqlite3.Error:
        now_pending, unreadable = None, True
    return evaluate(frames, src, fs, pkts, time.time(), outbox_start, now_pending, unreadable, settle_s)


def _source(db_dir: Path, record_id: int, source_file) -> tuple[float, np.ndarray]:
    d = np.load(source_file(db_dir, record_id))
    return float(d["fs"]), d["samples"]


def line(checks: list[Check]) -> str:
    ok = all(c.ok for c in checks)
    shown = checks if ok else [c for c in checks if not c.ok] + [c for c in checks if c.ok]  # failures first
    return f"{time.strftime('%H:%M:%S')}  {'PASS' if ok else 'FAIL'}  " + " | ".join(f"{c.name} {c.detail}" for c in shown)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", default="EHG-93C0")
    ap.add_argument("--window", default="5m", help="how far back to look, e.g. 90s, 5m (the latest run inside it)")
    ap.add_argument("--db", default="tpehgdb")
    ap.add_argument("--outbox", type=Path, default=Path("gateway/outbox.db"))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="evaluate once; exit 1 if any check fails")
    mode.add_argument("--watch", type=float, default=30, metavar="SECONDS", help="evaluate every SECONDS (default)")
    a = ap.parse_args()
    if a.once:
        checks = gather(a.node, a.window, a.db, a.outbox, None, SETTLE_S)
        print(line(checks))
        raise SystemExit(0 if all(c.ok for c in checks) else 1)
    try:
        start = outbox_pending(a.outbox)
    except sqlite3.Error:
        start = None
    print(f"checking {a.node} every {a.watch:.0f} s over the last {a.window} (Ctrl-C to stop)", flush=True)
    try:
        while True:
            print(line(gather(a.node, a.window, a.db, a.outbox, start, SETTLE_S)), flush=True)
            time.sleep(a.watch)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
