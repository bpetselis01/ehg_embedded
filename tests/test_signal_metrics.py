"""Checks backend/signal_metrics/worker.py against signals whose answers are known, and the Grafana panels that read its output."""

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from backend.signal_metrics import worker

ROOT = Path(__file__).parents[1]
FS = 20.0
N = 1200
T0 = datetime(2026, 11, 11, tzinfo=UTC)


def sine(freq_hz, amplitude, n=N):
    return amplitude * np.sin(2 * np.pi * freq_hz * np.arange(n) / FS)


def stored(channels, drop=()):
    """Frames as query_frames returns them: enough frames after the window that it is final."""
    n = len(channels[0])
    return {i: [int(round(c[i])) for c in channels] for i in range(n) if i not in drop}


def test_rms_and_median_frequency_of_a_known_sine():
    d = worker.descriptors(sine(1.0, 1000), FS)
    assert abs(d["rms"] - 1000 / np.sqrt(2)) < 0.01
    assert abs(d["median_freq_hz"] - 1.0) < 1 / 60
    assert d["flatline"] is False and d["saturated_pct"] == 0.0


def test_content_outside_the_band_is_ignored():
    in_band = worker.descriptors(sine(1.0, 1000), FS)
    mixed = worker.descriptors(sine(1.0, 1000) + sine(8.0, 700) + 5000, FS)
    assert abs(mixed["rms"] - in_band["rms"]) < 0.01
    assert mixed["median_freq_hz"] == in_band["median_freq_hz"]


def test_flat_channel_is_flagged_and_has_no_median_frequency():
    d = worker.descriptors(np.full(N, 123.0), FS)
    assert d["flatline"] is True and d["rms"] < 1e-6 and "median_freq_hz" not in d


def test_saturation_is_the_share_of_recent_samples_at_the_rails():
    x = sine(1.0, 1000)
    x[-20:] = 32767
    assert worker.descriptors(x, FS)["saturated_pct"] == 10.0


def test_window_ends_before_frames_that_could_still_be_resent():
    assert worker.latest_window(1359, FS) == (0, 1200)
    assert worker.latest_window(1358, FS) is None
    assert worker.latest_window(1959, FS) == (600, 1800)


def test_incomplete_window_reports_only_frames_present():
    ch = sine(1.0, 1000, 1400)
    results = worker.window_results(stored([ch], drop=range(100, 200)), T0, FS)
    assert results[0]["fields"] == {"frames_present": 1100}


def test_small_gap_is_filled_and_channels_are_separate():
    a, b = sine(1.0, 1000, 1400), sine(0.5, 400, 1400)
    results = worker.window_results(stored([a, b], drop=range(100, 110)), T0, FS)
    assert [r["channel"] for r in results] == ["c0", "c1"]
    assert results[0]["fields"]["frames_present"] == 1190
    assert abs(results[0]["fields"]["median_freq_hz"] - 1.0) < 1 / 60
    assert abs(results[1]["fields"]["median_freq_hz"] - 0.5) < 1 / 60


def test_time_is_the_last_frame_of_the_window():
    results = worker.window_results(stored([sine(1.0, 1000, 1400)]), T0, FS)
    assert (results[0]["time"] - T0).total_seconds() == 1199 / FS


def test_no_stored_data_writes_nothing_and_keeps_running():
    def no_data(node, since, latest_run):
        raise SystemExit(f"no ehg_raw data for {node} in last {since}")

    written = []
    assert worker.run_once("EHG-93C0", FS, no_data, lambda *a: written.append(a)) == []
    assert written == []


def test_short_run_writes_nothing():
    written = []
    query = lambda node, since, latest_run: (0, stored([sine(1.0, 1000, 300)]), T0)  # noqa: E731
    assert worker.run_once("EHG-93C0", FS, query, lambda *a: written.append(a)) == []
    assert written == []


def test_a_new_run_replaces_the_old_run_in_the_frames_read(monkeypatch):
    from types import SimpleNamespace as Rec

    from eval import backend_client as bc

    rows = [(9000, 1), (9001, 2), (0, 3), (1, 4)]
    records = [Rec(values={"record_id": "0", "frame_idx": i, "c0": i, "_time": t}) for i, t in rows]
    half_written = Rec(values={"record_id": "0", "frame_idx": 2, "c0": None, "_time": 5})
    seen = []

    def query_stream(q):
        seen.append(q)
        return records + [half_written] if "sort(" in q else records

    monkeypatch.setattr(bc, "_env_cache", {"INFLUX_BUCKET": "ehg"})
    monkeypatch.setattr(bc, "client", lambda: Rec(query_api=lambda: Rec(query_stream=query_stream)))
    assert bc._influx_frames("EHG-93C0", "103s", latest_run=True) == (0, {0: [0], 1: [1]}, 3)
    assert 'sort(columns:["_time"])' in seen[0]
    assert set(bc._influx_frames("EHG-93C0", "103s")[1]) == {0, 1, 9000, 9001}
    assert "sort(" not in seen[1]


def test_dashboard_has_a_panel_for_each_descriptor_and_alerts():
    panels = json.loads((ROOT / "backend/grafana/dashboards/ehg-overview.json").read_text())["panels"]
    queries = " ".join(t["query"] for p in panels for t in p.get("targets", []))
    for field in ("rms", "median_freq_hz", "saturated_pct", "frames_present", "flatline"):
        assert f'r._field == "{field}"' in queries, field
    assert [p for p in panels if p["type"] == "alertlist"]
    assert len({p["id"] for p in panels}) == len(panels)
    described = [p for p in panels if "ehg_metric" in " ".join(t["query"] for t in p.get("targets", []))]
    assert described and all("not clinical information" in p["description"] for p in described)


def test_regulatory_position_states_the_descriptors_and_keeps_the_limits():
    text = (ROOT / "docs/regulatory.md").read_text()
    assert "descriptive signal statistics" in text
    assert "No contraction analysis, classifier, risk score, alert about a person or prediction" in text
