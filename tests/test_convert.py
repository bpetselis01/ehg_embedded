"""Checks tools/convert.py keeps the original numbers exactly (the bit-exact
starting point for H2/H4). Skipped if the TPEHG dataset is not downloaded."""

import numpy as np
import pytest
import wfdb

from tools.convert import convert_record

REC = "data/raw/tpehgdb/tpehgdb/tpehg1007"


@pytest.mark.skipif(not __import__("os").path.exists(REC + ".hea"), reason="TPEHG not downloaded")
def test_convert_is_bit_exact_raw_channels():
    out = convert_record(REC, ["1", "2", "3"])
    ref = wfdb.rdrecord(REC, physical=False).d_signal[:, [0, 4, 8]]
    assert out["samples"].dtype == np.int16
    assert out["samples"].shape == (35180, 3)
    assert np.array_equal(out["samples"], ref)
    assert out["fs"] == 20.0
