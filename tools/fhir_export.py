"""Export a stored EHG window as an HL7 FHIR R5 Bundle (Device + Observation with SampledData).

Usage: uv run python tools/fhir_export.py --node EHG-0001 --since 10m [--db tpehgdb] [-o out.json]
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

CODE_SYSTEM = "urn:thesisb:ehg"  # local codes: no standard EHG code verified
UCUM = "http://unitsofmeasure.org"


def bundle(frames: dict[int, list[int]], src: dict, node_id: str, record: str, record_id: int,
           start: datetime) -> dict:
    """frames: frame_idx -> raw counts per channel. src: the record's.npz arrays (fs, gain, baseline, channels)."""
    fs = float(src["fs"])
    lo, hi = min(frames), max(frames)
    device_url = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'{CODE_SYSTEM}/device/{node_id}')}"
    components = []
    for c, name in enumerate(src["channels"]):
        gain, base = float(src["gain"][c]), int(src["baseline"][c])
        data = " ".join(str(frames[i][c]) if i in frames else "E" for i in range(lo, hi + 1))
        components.append({
            "code": {"coding": [{"system": CODE_SYSTEM, "code": f"channel-{name}"}], "text": f"EHG channel {name}"},
            "valueSampledData": {
                "origin": {"value": -base / gain, "unit": "mV", "system": UCUM, "code": "mV"},
                "interval": 1000.0 / fs,
                "intervalUnit": "ms",
                "factor": 1.0 / gain,
                "dimensions": 1,
                "data": data,
            },
        })
    end = start + timedelta(seconds=(hi - lo) / fs)
    observation = {
        "resourceType": "Observation",
        "status": "final",
        "code": {"coding": [{"system": CODE_SYSTEM, "code": "ehg-waveform"}], "text": "Electrohysterogram (EHG) waveform"},
        "effectivePeriod": {"start": start.isoformat(), "end": end.isoformat()},
        "device": {"reference": device_url},
        "note": [{"text": f"Replayed source {record} (record_id {record_id}), frames {lo}..{hi}"}],
        "component": components,
    }
    device = {
        "resourceType": "Device",
        "identifier": [{"system": f"{CODE_SYSTEM}/node-id", "value": node_id}],
        "displayName": node_id,
    }
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"fullUrl": device_url, "resource": device},
            {"fullUrl": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'{CODE_SYSTEM}/obs/{node_id}/{start.isoformat()}/{lo}')}",
             "resource": observation},
        ],
    }


def main():
    from backend_client import query_frames  # local import: keeps bundle() testable without Influx
    from tools.convert import source_file

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", required=True)
    ap.add_argument("--since", default="10m")
    ap.add_argument("--db", default="tpehgdb")
    ap.add_argument("-o", "--out")
    ap.add_argument("--backend", choices=["influx", "timescale"], help="which store to read (default: $EHG_BACKEND or influx)")
    a = ap.parse_args()
    record_id, frames, start = query_frames(a.node, a.since, backend=a.backend)
    path = source_file(Path(f"data/converted/{a.db}"), record_id)
    text = json.dumps(bundle(frames, np.load(path), a.node, path.stem, record_id, start), indent=1)
    Path(a.out).write_text(text) if a.out else print(text)


if __name__ == "__main__":
    root = Path(__file__).parent.parent
    sys.path[:0] = [str(root / "eval"), str(root)]
    main()
