"""Convert PhysioNet WFDB recordings -> 1 .npz file per recording.

Usage: uv run python tools/convert.py tpehgdb|tpehgt|ehgdb|icehg [--bits 16|24]

PURPOSE: Step 0. Public EHG recordings -> files every later step reads. Numbers kept exactly as stored.
INPUT (per recording, e.g. tpehg1007):
  data/raw/tpehgdb/tpehgdb/tpehg1007.hea  text labels (rate, gain, patient notes)
  data/raw/tpehgdb/tpehgdb/tpehg1007.dat  binary: 35180 rows x 12 signals, 16-bit whole numbers
  contents explained: docs/data/tpehgdb.md (same for other DBs)
OUTPUT:
  data/converted/tpehgdb/tpehg1007.npz    3 raw signals + labels to read them
  data/converted/tpehgdb/manifest.json    all records + their record_id (tracked in git)
  .npz arrays:
    samples   int16 [n_frames, n_ch]  untouched numbers (row 0: 1093 171 -748). --bits 24: int32
    fs        float                   samples/s (20.0)
    channels  str[n_ch]               "1" "2" "3"
    gain      float[n_ch]             units per mV (13107)
    baseline  int[n_ch]               stored value = 0 mV (0)
    units     str[n_ch]               "mV"
    mV = (samples - baseline) / gain
    why: samples are ADC counts, gain/baseline differ per DB (13107 here, 131.068 in ehgdb), so
    counts alone aren't comparable; classifier + plots need mV. Kept as counts here so the
    bit-exact check (H2/H4) works; mV only downstream. More: docs/data/tpehgdb.md
HOW: wfdb reads .hea/.dat with physical=False (no mV conversion), keeps chosen channels
  (filtered copies dropped), np.savez. Values that don't fit the chosen width -> error, never wrap.
RECORD_ID: tied to the record *name*, not file order. An existing manifest.json is reused: known
  names keep their id, new names get the next free id, ids of removed records are never reused.
  Needed because record_id is baked into firmware builds and stored DB rows.
WHY .npz: numbers + labels in 1 file, 1-line NumPy load, later steps all Python. (Inferred.)
ROLE IN THESIS: "Ground truth" the transmitted data is compared against. Raw numbers kept
  -> bit-exact checks (H2/H4) possible.
BEFORE -> AFTER: Before: data/raw/ (README step 0). After: data/converted/, read by
  tools/make_replay.py, gateway.py --simulate, eval/integrity.py (answer key).
  Tested by tests/test_convert.py.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import wfdb

RAW = Path("data/raw")
OUT = Path("data/converted")

# db -> (folder, signals to keep (None = all), sub-folders to skip).
# TPEHG: "1","2","3" are the unfiltered bipolar channels (the filtered copies are
# derived data; filtering is a downstream concern - see spec assumption A4).
DATABASES = {
    "tpehgdb": (RAW / "tpehgdb/tpehgdb", ["1", "2", "3"], ()),
    "tpehgt": (RAW / "tpehgt", ["EHG1", "EHG2", "EHG3", "TOCO"], ()),
    "ehgdb": (RAW / "ehgdb", [f"EHG{i}" for i in range(1, 17)], ()),  # numeric order = 4x4 grid order
    # records live in per-outcome subfolders; icehgdsmat/ = same 126 records again as .mat copies
    "icehg": (RAW / "icehg-ds", ["S1", "S2", "S3"], ("icehgdsmat",)),
}

DTYPES = {16: np.int16, 24: np.int32}  # 24-bit values are carried in int32


def convert_record(path: str, channels: list[str] | None, bits: int = 16) -> dict:
    """Read one WFDB record and return the arrays that go into its .npz file."""
    rec = wfdb.rdrecord(str(path), physical=False)
    idx = list(range(rec.n_sig)) if channels is None else [rec.sig_name.index(c) for c in channels]
    d = rec.d_signal[:, idx]
    lim = 2 ** (bits - 1)
    if d.size and (d.min() < -lim or d.max() >= lim):
        raise ValueError(f"{path}: values {d.min()}..{d.max()} don't fit {bits}-bit; use --bits 24")
    return {
        "samples": d.astype(DTYPES[bits]),
        "fs": float(rec.fs),
        "channels": np.array([rec.sig_name[i] for i in idx]),
        "gain": np.array([rec.adc_gain[i] for i in idx], dtype=float),
        "baseline": np.array([rec.baseline[i] for i in idx], dtype=int),
        "units": np.array([rec.units[i] for i in idx]),
    }


def assign_ids(names: list[str], old: list[dict]) -> dict[str, int]:
    """Name -> record_id. Known names keep their id; new names get ids after the highest ever used."""
    ids = {e["record"]: e["record_id"] for e in old}
    nxt = max(ids.values(), default=-1) + 1
    for name in sorted(names):
        if name not in ids:
            ids[name], nxt = nxt, nxt + 1
    if nxt > 65536:
        raise ValueError("record_id must fit the packet's u16 field")
    return ids


def convert_db(src: Path, channels: list[str] | None, out_dir: Path, skip=(), bits: int = 16) -> list[dict]:
    """Convert every record under src and write out_dir/manifest.json (sorted by record_id)."""
    heas = {h.stem: h for h in src.rglob("*.hea") if not set(skip) & set(h.relative_to(src).parts)}
    out_dir.mkdir(parents=True, exist_ok=True)
    mf = out_dir / "manifest.json"
    old = json.loads(mf.read_text()) if mf.exists() else []
    ids = assign_ids(list(heas), old)
    entries = {e["record"]: e for e in old}  # removed records stay listed, so their ids stay taken
    for name, hea in sorted(heas.items(), key=lambda kv: ids[kv[0]]):
        rec = convert_record(hea.with_suffix(""), channels, bits)
        np.savez(out_dir / f"{name}.npz", **rec)
        n_frames, n_ch = rec["samples"].shape
        entries[name] = {"record_id": ids[name], "record": name, "n_frames": n_frames,
                         "n_ch": n_ch, "fs": rec["fs"], "file": f"{name}.npz"}
        print(f"{ids[name]:4d} {name} {n_frames}x{n_ch} @ {rec['fs']} Hz")
    manifest = sorted(entries.values(), key=lambda e: e["record_id"])
    mf.write_text(json.dumps(manifest, indent=1))
    return manifest


def source_file(db_dir: Path, record_id: int) -> Path:
    """The converted .npz for a record_id (looked up by id, not by list position)."""
    manifest = json.loads((db_dir / "manifest.json").read_text())
    return db_dir / next(e["file"] for e in manifest if e["record_id"] == record_id)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("db", choices=DATABASES)
    ap.add_argument("--bits", type=int, choices=DTYPES, default=16)
    a = ap.parse_args()
    src, channels, skip = DATABASES[a.db]
    convert_db(src, channels, OUT / a.db, skip, a.bits)
