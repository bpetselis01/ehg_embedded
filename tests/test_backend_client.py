"""Checks eval/backend_client.py: importable without backend/.env, the same return shapes from InfluxDB and from PostgreSQL, a bad backend or window is an error not a default,
and the SQL store returns exactly what the writer stored (so integrity/latency/FHIR give the same answers from either store)."""

import sys
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "eval"))
import backend_client as bc  # noqa: E402  (must import with no backend/.env present)
from backend.db import migrate  # noqa: E402


def test_imports_without_a_dotenv_file_and_defaults_to_the_reference_store():
    assert bc.BACKENDS == ("influx", "timescale") and bc.get_backend() in bc.BACKENDS


@pytest.mark.parametrize("text, delta", [("90s", timedelta(seconds=90)), ("10m", timedelta(minutes=10)), ("2h", timedelta(hours=2)), ("7d", timedelta(days=7)), ("1w", timedelta(weeks=1))])
def test_parse_since(text, delta):
    assert bc.parse_since(text) == delta


@pytest.mark.parametrize("bad", ["", "10", "m10", "1.5h", "10 minutes", "-5m"])
def test_bad_windows_are_errors(bad):
    with pytest.raises(SystemExit):
        bc.parse_since(bad)


def test_unknown_backend_is_an_error_not_a_silent_default(monkeypatch):
    with pytest.raises(SystemExit):
        bc.set_backend("mongodb")
    monkeypatch.setattr(bc, "_backend", "oops")
    with pytest.raises(SystemExit):
        bc.get_backend()


def test_timescale_needs_a_dsn(monkeypatch):
    monkeypatch.delenv("EHG_TS_DSN", raising=False)
    with pytest.raises(SystemExit, match="EHG_TS_DSN"):
        bc.query_pkts("EHG-93C0", "1h", backend="timescale")


def test_influx_path_still_builds_frames_from_pivoted_rows(monkeypatch):
    """The refactor must not have changed the reference path: fake the Influx client."""
    t0 = SimpleNamespace()
    recs = [SimpleNamespace(values={"record_id": "7", "frame_idx": 11 - i, "c0": 1 + i, "c1": 2 + i, "c2": 3 + i, "_time": i}) for i in range(2)]
    fake = SimpleNamespace(query_api=lambda: SimpleNamespace(query_stream=lambda q: iter(recs)))
    monkeypatch.setattr(bc, "client", lambda: fake)
    monkeypatch.setattr(bc, "_env_cache", {"INFLUX_BUCKET": "ehg", "INFLUX_TOKEN": "t", "INFLUX_ORG": "o"})
    record_id, frames, first = bc.query_frames("EHG-93C0", "10m", backend="influx")
    assert record_id == 7 and frames == {11: [1, 2, 3], 10: [2, 3, 4]} and first == 1, "time of the frame with the smallest frame_idx"
    assert t0 is not None


def message(seq):
    return {"node_id": "EHG-93C0", "seq": seq, "frame_idx": seq * 10, "samples": [[1093 + f, 171 - f, -748 - 3 * f] for f in range(10)]}


@pytest.fixture
def loaded(pg_url, monkeypatch):
    """A migrated throwaway database holding three packets (20 Hz, 10 frames each), exposed through EHG_TS_DSN."""
    import time
    from datetime import datetime

    import psycopg
    migrate.main(["upgrade", "0001", "--url", pg_url])
    dsn = pg_url.replace("+psycopg", "")
    now = time.time()
    msgs = [message(s) | {"t_inject_host": now - 60 + s * 0.5} for s in (0, 1, 3)]  # seq 2 never arrived

    def ts(t):
        return datetime.fromtimestamp(t, tz=timezone.utc)
    with psycopg.connect(dsn) as conn:
        for m in msgs:
            t0, seq = m["t_inject_host"], m["seq"]
            conn.cursor().executemany("INSERT INTO ehg_raw (time, node, record_id, seq, frame_idx, ch, v) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                      [(ts(t0 + i / 20), m["node_id"], "0", seq, m["frame_idx"] + i, ch, v) for i, f in enumerate(m["samples"]) for ch, v in enumerate(f)])
            conn.execute("INSERT INTO ehg_pkt (time, node, seq, n_frames, retrans, replayed, t_inject_dev, t_inject_host, t_gw_rx, t_mqtt_pub, t_ingest_rx, t_db_ack) "
                         "VALUES (%s, %s, %s, 10, false, false, %s, %s, %s, %s, %s, %s)", (ts(t0), m["node_id"], seq, seq * 16384, t0, t0 + 0.5, t0 + 0.6, now - 59.0, now - 58.9))
    monkeypatch.setenv("EHG_TS_DSN", dsn)
    return msgs


def test_timescale_frames_equal_what_was_stored_raw(loaded):
    record_id, frames, first = bc.query_frames("EHG-93C0", "5m", backend="timescale")
    expected = {m["frame_idx"] + i: list(f) for m in loaded for i, f in enumerate(m["samples"])}
    assert record_id == 0 and frames == expected and len(frames) == 30
    assert first.tzinfo is not None and first.astimezone(timezone.utc).timestamp() == pytest.approx(loaded[0]["t_inject_host"], abs=1e-3)
    assert all(isinstance(v, int) for f in frames.values() for v in f)


def test_timescale_pkts_have_the_keys_the_influx_rows_have(loaded):
    pkts = bc.query_pkts("EHG-93C0", "5m", backend="timescale")
    assert [p["seq"] for p in pkts] == [0, 1, 3]
    assert {"node_id", "seq", "n_frames", "retrans", "replayed", "t_inject_dev", "t_inject_host", "t_gw_rx", "t_mqtt_pub", "t_ingest_rx", "t_db_ack", "_time"} <= set(pkts[0])
    assert pkts[0]["retrans"] is False and pkts[0]["t_db_ack"] >= pkts[0]["t_ingest_rx"]


def test_window_and_node_are_respected(loaded):
    assert bc.query_pkts("EHG-0000", "5m", backend="timescale") == []
    with pytest.raises(SystemExit, match="no ehg_raw data"):
        bc.query_frames("EHG-0000", "5m", backend="timescale")
    with pytest.raises(SystemExit, match="no ehg_raw data"):
        bc.query_frames("EHG-93C0", "10s", backend="timescale")  # the data is a minute old


def test_integrity_reconcile_gives_the_same_answer_from_the_sql_store(loaded):
    """End to end for H2/H4: frames read back from PostgreSQL reconcile bit-exactly against the source samples they were made from, and the one lost packet shows as one gap."""
    import numpy as np
    from integrity import reconcile
    _, frames, _ = bc.query_frames("EHG-93C0", "5m", backend="timescale")
    source = np.zeros((40, 3), dtype=int)  # seq 0, 1 and 3 delivered = frames 0-19 and 30-39 (their frame_idx is seq * 10 + i); seq 2 was lost
    for m in loaded:
        for i, f in enumerate(m["samples"]):
            source[m["frame_idx"] + i] = f
    r = reconcile(frames, source, 40)
    assert r["delivered"] == 30 and r["mismatches"] == [] and r["missing_ranges"] == [(20, 29)] and r["delivery_pct"] == 75.0


def _fake_influx(monkeypatch, rows):
    recs = [SimpleNamespace(values=v) for v in rows]
    monkeypatch.setattr(bc, "client", lambda: SimpleNamespace(query_api=lambda: SimpleNamespace(query_stream=lambda q: iter(recs))))
    monkeypatch.setattr(bc, "_env_cache", {"INFLUX_BUCKET": "ehg", "INFLUX_TOKEN": "t", "INFLUX_ORG": "o"})


def test_a_half_written_frame_row_is_skipped_not_a_crash(monkeypatch):
    """Register item 6: a query during a run can see a row InfluxDB is still filling in."""
    _fake_influx(monkeypatch, [
        {"record_id": "0", "frame_idx": 5, "c0": 1, "c1": 2, "c2": 3, "_time": 1},
        {"record_id": "0", "frame_idx": 6, "c0": 4, "c1": None, "c2": 6, "_time": 2},
        {"record_id": None, "frame_idx": None, "c0": 7, "c1": 8, "c2": 9, "_time": 3},
    ])
    record_id, frames, _ = bc.query_frames("EHG-93C0", "2m", backend="influx")
    assert record_id == 0 and frames == {5: [1, 2, 3]}


def test_a_half_written_packet_row_is_dropped(monkeypatch):
    full = {"seq": 1, "n_frames": 10, "t_inject_dev": 16384, "t_gw_rx": 10.5, "t_db_ack": 10.6}
    _fake_influx(monkeypatch, [full, {**full, "seq": 2, "t_db_ack": None}, {**full, "seq": 3, "t_gw_rx": None}])
    assert [p["seq"] for p in bc.query_pkts("EHG-93C0", "2m", backend="influx")] == [1]
