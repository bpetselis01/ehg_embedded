"""H2 (delivery) and H4 (bit-exact fidelity): compare what reached InfluxDB with the source file.

Usage: uv run python eval/integrity.py --node EHG-0001 --since 2h [--db tpehgdb]

PURPOSE: H2: did every sample arrive (target > 99 %)? H4 bit-exact half: is every stored number identical to the original?
INPUT: stored samples + record_id from eval/backend_client.py:query_frames;
  data/converted/<db>/manifest.json and the .npz it names (tools/convert.py; docs/data/tpehgdb.md).
  e.g. stored {0: [1093, 171, -748], 1: [1145, 180, -790], ...}, record_id 0
       -> manifest says tpehg1007.npz, whose row 0 is [1093, 171, -748] (match), row 1 [1145, 180, -790] ...
OUTPUT (printed), e.g.:
    node=EHG-0001 record_id=0 frames 0..35999
    delivery 35990/36000 = 99.9722 %
    bit-exact mismatches: 0
    missing ranges (first 20): [(120, 129)]
HOW: Look up the source file by record_id. Line stored and source frames up by frame_idx
  (source loops: row = frame_idx % length). Count delivered, differing, and runs of missing frames.
ROLE IN THESIS: H2/H4 result, end to end (dataset -> board -> radio -> gateway -> DB).
BEFORE -> AFTER: Before: ehg_raw rows (backend/ingest/ingest.py) and data/converted/ (tools/convert.py).
  After: printed results. Tested by tests/test_integrity.py.
"""

import argparse
from pathlib import Path

import numpy as np


def reconcile(received: dict[int, list[int]], source: np.ndarray, expected_frames: int) -> dict:
    """received: frame_idx -> samples; source: [record_len, n_ch]; frames 0..expected_frames-1 were injected."""
    n = len(source)
    delivered = [i for i in range(expected_frames) if i in received]
    mismatches = [i for i in delivered if list(received[i]) != source[i % n].tolist()]
    missing, ranges, start = [i for i in range(expected_frames) if i not in received], [], None
    for a, b in zip(missing, missing[1:] + [None]):
        start = a if start is None else start
        if b != a + 1:
            ranges.append((start, a))
            start = None
    return {
        "expected": expected_frames,
        "delivered": len(delivered),
        "delivery_pct": 100.0 * len(delivered) / expected_frames if expected_frames else 0.0,
        "mismatches": mismatches,
        "missing_ranges": ranges,
    }


def main():
    from backend_client import query_frames  # local import: keeps reconcile() testable without Influx
    from tools.convert import source_file

    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--since", default="1h")
    ap.add_argument("--db", default="tpehgdb")
    a = ap.parse_args()
    record_id, frames, _ = query_frames(a.node, a.since)
    src = np.load(source_file(Path(f"data/converted/{a.db}"), record_id))["samples"]
    # Frames are counted from the first one seen in the window: a window can start mid-stream.
    # Re-number from 0 and rotate the source so row 0 lines up with that first frame.
    lo, hi = min(frames), max(frames)
    shifted = {i - lo: v for i, v in frames.items()}
    src = np.roll(src, -(lo % len(src)), axis=0)
    r = reconcile(shifted, src, hi - lo + 1)
    print(f"node={a.node} record_id={record_id} frames {lo}..{hi}")
    print(f"delivery {r['delivered']}/{r['expected']} = {r['delivery_pct']:.4f} %")
    print(f"bit-exact mismatches: {len(r['mismatches'])}")
    print(f"missing ranges (first 20): {[(x + lo, y + lo) for x, y in r['missing_ranges'][:20]]}")


if __name__ == "__main__":
    import sys
    sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent.parent)]  # eval/ + repo root
    main()
