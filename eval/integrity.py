"""H2 (delivery) and H4 (bit-exact fidelity) - reconcile what reached InfluxDB against the source file.

Usage: uv run python eval/integrity.py --node EHG-0001 --since 2h [--db tpehgdb]

PURPOSE: Answers H2 (did every sample arrive? target > 99 %) and the bit-exact half of H4
  (is every stored number identical to the original recording?).
HOW: Pulls the stored samples for a node, opens the original converted file named by the
  record_id they carry, lines the two up by frame_idx, then counts: frames delivered, frames
  whose numbers differ, and runs of missing frames.
ROLE IN THESIS: The H2/H4 result, end to end (dataset -> board -> radio -> gateway -> DB).
BEFORE -> AFTER: Before: ehg_raw rows (ingest.py) via eval/backend_client.py, and
  data/converted/ from tools/convert.py. After: printed results. Tested by tests/test_integrity.py.
"""

import argparse
import json
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

    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--since", default="1h")
    ap.add_argument("--db", default="tpehgdb")
    a = ap.parse_args()
    record_id, frames = query_frames(a.node, a.since)
    manifest = json.loads(Path(f"data/converted/{a.db}/manifest.json").read_text())
    src = np.load(f"data/converted/{a.db}/{manifest[record_id]['file']}")["samples"]
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
    sys.path.insert(0, str(Path(__file__).parent))
    main()
