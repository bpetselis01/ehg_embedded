"""Writes the per-record signal metadata the FHIR sync worker needs, so the worker can run where the converted recordings are not.

Usage: uv run python tools/export_record_meta.py [--converted-dir data/converted/tpehgdb] [-o deploy/fhir/records.json]

PURPOSE: The sync worker (backend/fhir_sync/worker.py) turns raw ADC counts into FHIR SampledData and needs each record's sampling rate, gain, baseline and channel names. Those live in the converted .npz files, which are
  large, gitignored and not on the cloud VM. This copies ONLY the four small header fields per record into one JSON file that is committed (deploy/fhir/records.json).
INPUT: data/converted/<db>/manifest.json and the .npz files it names (README step 0 makes them). e.g. record 0 = tpehg1007: fs 20.0, gain [13107, 13107, 13107], baseline [0, 0, 0], channels ["1", "2", "3"].
OUTPUT: a JSON object {"<record_id>": {"record": "tpehg1007", "fs": 20.0, "gain": [...], "baseline": [...], "channels": [...]}}. No samples and nothing about a person: these are the public PhysioNet record headers.
HOW: reads the arrays with numpy only (no wfdb). --check compares the file with what the data would produce and exits 1 on a difference (tests/test_fhir_sync.py skips that when the data is absent).
ROLE IN THESIS: Lets the live write-back to the reference EHR run on the VM (docs/production-topology.md, hop 6b).
BEFORE -> AFTER: Before: the worker needed data/converted next to it. After: it reads this small file.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parents[1]


def build(converted_dir: Path) -> dict:
    manifest = json.loads((converted_dir / "manifest.json").read_text())
    out = {}
    for e in manifest:
        z = np.load(converted_dir / e["file"])
        out[str(e["record_id"])] = {"record": e["record"], "fs": float(z["fs"]), "gain": [float(g) for g in z["gain"]],
                                    "baseline": [int(b) for b in z["baseline"]], "channels": [str(c) for c in z["channels"]]}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--converted-dir", type=Path, default=ROOT / "data/converted/tpehgdb")
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "deploy/fhir/records.json")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    text = json.dumps(build(a.converted_dir), indent=1, sort_keys=True) + "\n"
    if a.check:
        if not a.out.exists() or a.out.read_text() != text:
            print(f"{a.out} is out of date: run uv run python tools/export_record_meta.py")
            return 1
        print("record metadata is current")
        return 0
    a.out.write_text(text)
    print(f"wrote {a.out} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
