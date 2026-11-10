"""Shared database queries for the evaluation scripts, for either store: InfluxDB 2.7 (the reference, default) or PostgreSQL/TimescaleDB.

PURPOSE: One place that pulls stored EHG data back out of the database, with the SAME return shapes from both stores so that eval/integrity.py, eval/latency.py and
  tools/fhir_export.py give the same answers from either (the parity proof, eval/parity.py, decision 0007).
INPUT: node id (e.g. EHG-93C0), `since` window (e.g. 10m, 2h, 7d). Backend: set_backend("influx" | "timescale"), the --backend flag of the scripts, or the environment variable EHG_BACKEND.
  influx: credentials from backend/.env (read when first needed, not at import). timescale: EHG_TS_DSN, a libpq connection string (e.g. "host=localhost dbname=ehg user=ehg").
OUTPUT: query_frames(node, since) -> (record_id, {frame_idx: [ch1, ch2, ch3]} raw ADC numbers, time of the first frame).
  e.g. (0, {0: [1093, 171, -748], 1: [1145, 180, -790], ...}, datetime(2026, 10, 6, 2, 31, 8, 18676, tzinfo=utc)); no rows -> exits with "no ehg_raw data for EHG-93C0 in last 10m"
  query_pkts(node, since) -> list of dicts, 1 per packet: node_id, seq, n_frames, retrans, replayed, t_inject_dev, t_inject_host, t_gw_rx, t_mqtt_pub, t_ingest_rx, t_db_ack, _time
  (the influx backend adds Influx's own result/table/_start/_stop/_measurement keys); no rows -> [].
HOW: influx: Flux queries, pivoted so each row is one moment with all fields side by side. timescale: plain SQL on ehg_raw (one row per frame and channel) and ehg_pkt.
ROLE IN THESIS: Read-back half of the pipeline. query_frames is also the proposed classifier hook (docs/interface-spec.md 5).
BEFORE -> AFTER: Before: only InfluxDB, and importing this file failed without backend/.env. After: both stores, chosen by a flag, importable anywhere; default behaviour unchanged.
"""

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

BACKENDS = ("influx", "timescale")
_backend = os.environ.get("EHG_BACKEND", "influx")
_env_cache: dict | None = None


def set_backend(name: str) -> None:
    """Choose the store for the following queries; an unknown name is an error, never a silent default."""
    global _backend
    if name not in BACKENDS:
        raise SystemExit(f"unknown backend {name!r}: choose one of {', '.join(BACKENDS)}")
    _backend = name


def get_backend() -> str:
    """The store queries currently go to."""
    if _backend not in BACKENDS:
        raise SystemExit(f"EHG_BACKEND={_backend!r} is not one of {', '.join(BACKENDS)}")
    return _backend


def parse_since(since: str) -> timedelta:
    """'90s' / '10m' / '2h' / '7d' / '1w' -> timedelta (the same suffixes Flux accepts)."""
    m = re.fullmatch(r"(\d+)([smhdw])", since.strip())
    if not m:
        raise SystemExit(f"bad --since {since!r}: use a number and one of s, m, h, d, w (e.g. 10m, 2h)")
    return timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[m.group(2)]: int(m.group(1))})


# ---------------------------------------------------------------- InfluxDB (reference)

def _env() -> dict:
    """backend/.env as a dict, read the first time it is needed."""
    global _env_cache
    if _env_cache is None:
        text = Path(__file__).parent.parent.joinpath("backend/.env").read_text()
        _env_cache = dict(line.split("=", 1) for line in text.split("\n") if "=" in line and not line.startswith("#"))
    return _env_cache


def client():
    """Connection to the local InfluxDB (long timeout: soak queries can be large)."""
    from influxdb_client import InfluxDBClient
    return InfluxDBClient(url="http://localhost:8086", token=_env()["INFLUX_TOKEN"], org=_env()["INFLUX_ORG"], timeout=120_000)


def _influx_frames(node: str, since: str) -> tuple[int, dict[int, list[int]], datetime]:
    """Frames of the node in the window, from InfluxDB."""
    q = f'''from(bucket:"{_env()["INFLUX_BUCKET"]}") |> range(start:-{since})
      |> filter(fn:(r) => r._measurement == "ehg_raw" and r.node_id == "{node}")
      |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'''
    frames, record_id, times = {}, None, {}
    for rec in client().query_api().query_stream(q):
        v = rec.values
        record_id = int(v["record_id"])
        chans = sorted((k for k in v if k[0] == "c" and k[1:].isdigit()), key=lambda k: int(k[1:]))
        frames[int(v["frame_idx"])] = [int(v[k]) for k in chans]
        times[int(v["frame_idx"])] = v["_time"]
    if not frames:
        raise SystemExit(f"no ehg_raw data for {node} in last {since}")
    return record_id, frames, times[min(times)]


def _influx_pkts(node: str, since: str) -> list[dict]:
    """Packet timing rows of the node in the window, from InfluxDB."""
    q = f'''from(bucket:"{_env()["INFLUX_BUCKET"]}") |> range(start:-{since})
      |> filter(fn:(r) => r._measurement == "ehg_pkt" and r.node_id == "{node}")
      |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'''
    return [rec.values for rec in client().query_api().query_stream(q)]


# ---------------------------------------------------------------- PostgreSQL / TimescaleDB

def _ts_connect():
    """Connection to the enterprise store, from EHG_TS_DSN."""
    import psycopg
    dsn = os.environ.get("EHG_TS_DSN")
    if not dsn:
        raise SystemExit("backend timescale needs EHG_TS_DSN, e.g. 'host=localhost port=5432 dbname=ehg user=ehg' (password via PGPASSWORD)")
    return psycopg.connect(dsn)


def _ts_frames(node: str, since: str) -> tuple[int, dict[int, list[int]], datetime]:
    """Frames of the node in the window, from PostgreSQL: one row per frame and channel, regrouped into frames."""
    with _ts_connect() as c:
        rows = c.execute("SELECT record_id, frame_idx, ch, v, time FROM ehg_raw WHERE node = %s AND time > now() - %s ORDER BY frame_idx, ch",
                         (node, parse_since(since))).fetchall()
    if not rows:
        raise SystemExit(f"no ehg_raw data for {node} in last {since}")
    frames: dict[int, list[int]] = {}
    times: dict[int, datetime] = {}
    for _, frame_idx, _, v, t in rows:
        frames.setdefault(frame_idx, []).append(int(v))
        times[frame_idx] = t
    return int(rows[0][0]), frames, times[min(times)]


def _ts_pkts(node: str, since: str) -> list[dict]:
    """Packet timing rows of the node in the window, from PostgreSQL, with the same keys the Influx rows have."""
    cols = ("seq", "n_frames", "retrans", "replayed", "t_inject_dev", "t_inject_host", "t_gw_rx", "t_mqtt_pub", "t_ingest_rx", "t_db_ack")
    with _ts_connect() as c:
        rows = c.execute(f"SELECT time, {', '.join(cols)} FROM ehg_pkt WHERE node = %s AND time > now() - %s ORDER BY time",
                         (node, parse_since(since))).fetchall()
    return [{"_time": r[0], "node_id": node, **dict(zip(cols, r[1:]))} for r in rows]


# ---------------------------------------------------------------- public API

def query_frames(node: str, since: str, backend: str | None = None) -> tuple[int, dict[int, list[int]], datetime]:
    """Returns (record_id, {frame_idx: [c0..cN]}, time of the first frame) for the node's frames in the window, from the chosen store."""
    return (_ts_frames if (backend or get_backend()) == "timescale" else _influx_frames)(node, since)


def query_pkts(node: str, since: str, backend: str | None = None) -> list[dict]:
    """Returns one dict of timing fields (ehg_pkt) per packet from the node in the window, from the chosen store."""
    return (_ts_pkts if (backend or get_backend()) == "timescale" else _influx_pkts)(node, since)
