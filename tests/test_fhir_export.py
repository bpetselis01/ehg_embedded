"""Checks tools/fhir_export.py: the FHIR R5 SampledData keeps the raw counts unchanged
(bit-exact, H4), marks missing frames as "E" (H2), and its origin/factor give the same mV
as convert.py's formula."""

from datetime import datetime, timezone

import numpy as np

from tools.fhir_export import bundle

SRC = {"fs": np.float64(20.0), "gain": np.array([13107.0, 100.0]), "baseline": np.array([0, 5]),
       "channels": np.array(["1", "2"])}
T0 = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)


def obs(frames):
    b = bundle(frames, SRC, "EHG-0001", "tpehg1007", 0, T0)
    assert b["resourceType"] == "Bundle" and b["type"] == "collection"
    dev, ob = (e["resource"] for e in b["entry"])
    assert dev["resourceType"] == "Device" and dev["identifier"][0]["value"] == "EHG-0001"
    assert ob["device"]["reference"] == b["entry"][0]["fullUrl"]
    assert all(e["fullUrl"].startswith("urn:uuid:") for e in b["entry"])
    return ob


def test_raw_counts_round_trip_exactly():
    frames = {10: [1093, 171], 11: [-32768, 32767], 12: [-748, 0]}
    ob = obs(frames)
    for c, comp in enumerate(ob["component"]):
        data = [int(v) for v in comp["valueSampledData"]["data"].split()]
        assert data == [frames[i][c] for i in (10, 11, 12)]


def test_missing_frames_marked_E_and_period_spans_window():
    ob = obs({0: [1, 2], 3: [7, 8]})
    assert ob["component"][0]["valueSampledData"]["data"] == "1 E E 7"
    assert ob["effectivePeriod"]["end"] == "2026-10-16T09:00:00.150000+00:00"  # 3 frames x 50 ms


def test_origin_and_factor_reproduce_millivolts():
    ob = obs({0: [1093, 105]})
    for c, comp in enumerate(ob["component"]):
        sd = comp["valueSampledData"]
        count = int(sd["data"])
        mv = sd["origin"]["value"] + sd["factor"] * count
        expected = (count - SRC["baseline"][c]) / SRC["gain"][c]
        assert abs(mv - expected) < 1e-12
        assert sd["origin"]["code"] == "mV" and sd["intervalUnit"] == "ms" and sd["interval"] == 50.0
