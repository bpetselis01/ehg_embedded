"""Convert PhysioNet WFDB records into the replay format used by every layer.

Output per record: data/converted/<db>/<record>.npz with
  samples  int16 [n_frames, n_ch]   raw ADC units, exactly as stored (bit-exact source for H2/H4)
  fs       float                    sampling rate, Hz
  channels str[n_ch]                WFDB signal names
  gain     float[n_ch]              ADC units per physical unit (mV = (adu - baseline) / gain)
  baseline int[n_ch]
  units    str[n_ch]
plus data/converted/<db>/manifest.json listing records in record_id order.

Usage: uv run python tools/convert.py tpehgdb|tpehgt|ehgdb|icehg
"""

import json
import sys
from pathlib import Path

import numpy as np
import wfdb

RAW = Path("data/raw")
OUT = Path("data/converted")

# Which signals each database transports. None = all signals.
# TPEHG: "1","2","3" are the unfiltered bipolar channels (the filtered copies are
# derived data; filtering is a downstream concern - see spec assumption A4).
DATABASES = {
    "tpehgdb": (RAW / "tpehgdb/tpehgdb", ["1", "2", "3"]),
    "tpehgt": (RAW / "tpehgt", ["EHG1", "EHG2", "EHG3", "TOCO"]),
    "ehgdb": (RAW / "ehgdb", [f"EHG{i}" for i in range(1, 17)]),  # numeric order = 4x4 grid order
    "icehg": (RAW / "icehg-ds", ["S1", "S2", "S3"]),  # records live in per-outcome subfolders
}


def convert_record(path: str, channels: list[str] | None) -> dict:
    rec = wfdb.rdrecord(str(path), physical=False)
    idx = list(range(rec.n_sig)) if channels is None else [rec.sig_name.index(c) for c in channels]
    return {
        "samples": rec.d_signal[:, idx].astype(np.int16),
        "fs": float(rec.fs),
        "channels": np.array([rec.sig_name[i] for i in idx]),
        "gain": np.array([rec.adc_gain[i] for i in idx], dtype=float),
        "baseline": np.array([rec.baseline[i] for i in idx], dtype=int),
        "units": np.array([rec.units[i] for i in idx]),
    }


def convert_db(db: str) -> None:
    src, channels = DATABASES[db]
    out_dir = OUT / db
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for record_id, hea in enumerate(sorted(src.rglob("*.hea"))):
        name = hea.stem
        rec = convert_record(hea.with_suffix(""), channels)
        np.savez(out_dir / f"{name}.npz", **rec)
        n_frames, n_ch = rec["samples"].shape
        manifest.append({"record_id": record_id, "record": name, "n_frames": n_frames,
                         "n_ch": n_ch, "fs": rec["fs"], "file": f"{name}.npz"})
        print(f"{record_id:4d} {name} {n_frames}x{n_ch} @ {rec['fs']} Hz")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    convert_db(sys.argv[1])
