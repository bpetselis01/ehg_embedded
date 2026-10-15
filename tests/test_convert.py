"""Checks tools/convert.py: numbers kept exactly (the bit-exact starting point for H2/H4),
record_id tied to record names (stable when files are added/removed), skipped duplicate
folders, and no silent wrap-around when values don't fit 16 bits.
Uses tiny synthetic WFDB records, so it runs without the datasets; one extra check uses
real TPEHG data when it is downloaded."""

import json
import os

import numpy as np
import pytest
import wfdb

from tools.convert import convert_db, convert_record, source_file

REC = "data/raw/tpehgdb/tpehgdb/tpehg1007"


def write_rec(d, name, sig, fmt="16"):
    """Write a 2-signal WFDB record (names "a", "b") with the given raw counts."""
    d.mkdir(parents=True, exist_ok=True)
    wfdb.wrsamp(name, fs=20, units=["mV", "mV"], sig_name=["a", "b"], d_signal=np.asarray(sig),
                fmt=[fmt, fmt], adc_gain=[13107.0, 13107.0], baseline=[0, 5], write_dir=str(d))


def ids(manifest):
    return {e["record"]: e["record_id"] for e in manifest}


def test_record_is_bit_exact_and_keeps_labels(tmp_path):
    sig = [[-32768, 32767], [1, -2], [300, 400]]
    write_rec(tmp_path, "r1", sig)
    out = convert_record(tmp_path / "r1", ["b", "a"])  # order follows the request
    assert out["samples"].dtype == np.int16
    assert out["samples"].tolist() == [[32767, -32768], [-2, 1], [400, 300]]
    assert out["channels"].tolist() == ["b", "a"]
    assert out["gain"].tolist() == [13107.0, 13107.0] and out["baseline"].tolist() == [5, 0]
    assert out["fs"] == 20.0


def test_too_wide_for_16_bit_raises_instead_of_wrapping(tmp_path):
    write_rec(tmp_path, "w", [[40000, 0], [-70000, 1]], fmt="24")
    with pytest.raises(ValueError, match="24"):
        convert_record(tmp_path / "w", None)
    out = convert_record(tmp_path / "w", None, bits=24)
    assert out["samples"].dtype == np.int32 and out["samples"][:, 0].tolist() == [40000, -70000]


def test_record_id_follows_names_not_file_order(tmp_path):
    src, out = tmp_path / "raw", tmp_path / "out"
    for n in ("b", "c"):
        write_rec(src, n, [[1, 2]])
    first = ids(convert_db(src, None, out))
    assert first == {"b": 0, "c": 1}

    write_rec(src, "a", [[3, 4]])  # new record sorts first by name, but must not shift others
    (src / "b.hea").unlink()      # removed record keeps its id reserved
    (src / "b.dat").unlink()
    second = ids(convert_db(src, None, out))
    assert second == {"b": 0, "c": 1, "a": 2}

    write_rec(src, "d", [[5, 6]])
    assert ids(convert_db(src, None, out))["d"] == 3  # ids are never reused
    assert source_file(out, 1).name == "c.npz"        # lookup by id, not list position


def test_skipped_subfolders_are_not_converted(tmp_path):
    src, out = tmp_path / "raw", tmp_path / "out"
    write_rec(src / "early", "x", [[1, 2]])
    write_rec(src / "mat_copies", "xm", [[1, 2]])
    manifest = convert_db(src, None, out, skip=("mat_copies",))
    assert [e["record"] for e in manifest] == ["x"]
    assert json.loads((out / "manifest.json").read_text()) == manifest


@pytest.mark.skipif(not os.path.exists(REC + ".hea"), reason="TPEHG not downloaded")
def test_real_tpehg_record_is_bit_exact():
    out = convert_record(REC, ["1", "2", "3"])
    ref = wfdb.rdrecord(REC, physical=False).d_signal[:, [0, 4, 8]]
    assert out["samples"].dtype == np.int16
    assert out["samples"].shape == (35180, 3)
    assert np.array_equal(out["samples"], ref)
    assert out["fs"] == 20.0
