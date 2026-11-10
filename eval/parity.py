"""Parity proof: do InfluxDB (the reference) and PostgreSQL/TimescaleDB hold the SAME data for the same run?

  uv run python eval/parity.py --node SIM-0000 --since 10m --tolerance-ms 50

PURPOSE: The condition for ever using the enterprise store as the cloud's primary (decision 0007): identical packets, bit-identical samples, identical missing ranges, and the same
  H1 latency within a stated tolerance. Any difference means "do not switch": the exit status says so.
INPUT: a run stored in BOTH stores (start the influx writer and the ts-writer on the same stream, then e.g. `gateway --simulate 1 --sim-loss 0.01` for a few minutes);
  --node, --since; EHG_TS_DSN for PostgreSQL and backend/.env for InfluxDB; --tolerance-ms for the latency comparison (default 50).
OUTPUT: a table of checks, each PASS/FAIL with the numbers, then "PARITY: PASS" or "PARITY: FAIL (n checks differ)"; exit status 0 only on PASS.
  e.g. frames: influx 1200, timescale 1200, same frame numbers PASS / samples bit-identical PASS / missing ranges influx [(40, 49)] timescale [(40, 49)] PASS /
       packets: same seq set PASS / static timing fields equal PASS / H1 median influx 0.259 s vs timescale 0.262 s (diff 3 ms <= 50 ms) PASS
HOW: Both stores answer through eval/backend_client.py with the same return shapes. compare() is pure and tested with synthetic data. The ingest-stamped times (t_ingest_rx, t_db_ack)
  legitimately differ between the two writers, so H1 is compared by median and p95 within the tolerance, not field by field.
ROLE IN THESIS: Evidence that moving to the enterprise store did not change what was stored (H2/H4 hold in both) and did not change the measurement (H1).
BEFORE -> AFTER: Before: only InfluxDB existed. After: this decides whether TimescaleDB may become primary (Checkpoint 4).
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

STATIC = ("n_frames", "retrans", "replayed", "t_inject_dev", "t_inject_host", "t_gw_rx", "t_mqtt_pub")


def missing_ranges(frames: dict[int, list[int]]) -> list[tuple[int, int]]:
    """Runs of frame numbers absent between the first and last frame seen: {0,1,5} -> [(2, 4)]."""
    if not frames:
        return []
    have, out, start = set(frames), [], None
    for i in range(min(frames), max(frames) + 1):
        if i not in have:
            start = i if start is None else start
        elif start is not None:
            out.append((start, i - 1))
            start = None
    return out


def h1_latencies(pkts: list[dict]) -> np.ndarray:
    """Per-packet inject-to-stored time in seconds (t_db_ack - t_inject_host), live packets only."""
    return np.array([p["t_db_ack"] - p["t_inject_host"] for p in pkts if not p.get("retrans") and not p.get("replayed")])


def compare(frames_a: dict, frames_b: dict, pkts_a: list[dict], pkts_b: list[dict], tolerance_ms: float = 50.0) -> list[tuple[str, bool, str]]:
    """List of (check name, passed, detail). a = reference store, b = store under test."""
    checks = []
    same_frames = set(frames_a) == set(frames_b)
    checks.append(("same frame numbers", same_frames, f"reference {len(frames_a)}, other {len(frames_b)}; only in reference {len(set(frames_a) - set(frames_b))}, only in other {len(set(frames_b) - set(frames_a))}"))
    differing = [i for i in set(frames_a) & set(frames_b) if list(frames_a[i]) != list(frames_b[i])]
    checks.append(("samples bit-identical", not differing, f"{len(set(frames_a) & set(frames_b)) - len(differing)} of {len(set(frames_a) & set(frames_b))} common frames identical" + (f"; first differing frame {min(differing)}" if differing else "")))
    ra, rb = missing_ranges(frames_a), missing_ranges(frames_b)
    checks.append(("same missing ranges", ra == rb, f"reference {ra[:5]}, other {rb[:5]}"))
    by_a, by_b = {p["seq"]: p for p in pkts_a}, {p["seq"]: p for p in pkts_b}
    checks.append(("same packet seq set", set(by_a) == set(by_b), f"reference {len(by_a)}, other {len(by_b)}"))
    bad = [s for s in set(by_a) & set(by_b) for k in STATIC if (abs(float(by_a[s][k]) - float(by_b[s][k])) > 1e-6 if k.startswith("t_") and k != "t_inject_dev" else by_a[s][k] != by_b[s][k])]
    checks.append(("static timing fields equal", not bad, f"{len(bad)} mismatching fields" + (f"; first at seq {min(bad)}" if bad else "")))
    la, lb = h1_latencies(pkts_a), h1_latencies(pkts_b)
    if len(la) and len(lb):
        for name, fa, fb in (("median", np.median(la), np.median(lb)), ("p95", np.percentile(la, 95), np.percentile(lb, 95))):
            d = abs(fa - fb) * 1000
            checks.append((f"H1 {name} within {tolerance_ms:g} ms", d <= tolerance_ms, f"reference {fa:.3f} s, other {fb:.3f} s (diff {d:.1f} ms)"))
    else:
        checks.append(("H1 latency comparable", False, "no live packets in one of the stores"))
    return checks


def main() -> int:
    """Query both stores for the same run, print the checks, return 0 only if all pass."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", required=True)
    ap.add_argument("--since", default="1h")
    ap.add_argument("--tolerance-ms", type=float, default=50.0)
    a = ap.parse_args()
    import backend_client as bc
    _, frames_a, _ = bc.query_frames(a.node, a.since, backend="influx")
    _, frames_b, _ = bc.query_frames(a.node, a.since, backend="timescale")
    checks = compare(frames_a, frames_b, bc.query_pkts(a.node, a.since, backend="influx"), bc.query_pkts(a.node, a.since, backend="timescale"), a.tolerance_ms)
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    failed = sum(not ok for _, ok, _ in checks)
    print("PARITY: PASS" if not failed else f"PARITY: FAIL ({failed} checks differ): do NOT switch the cloud profile to TimescaleDB")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
