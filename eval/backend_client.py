"""Shared InfluxDB queries for the evaluation scripts. Reads credentials from backend/.env.

PURPOSE: One place that pulls stored EHG data back out of the database.
INPUT: InfluxDB bucket `ehg` (schema: docs/interface-spec.md 5). Args: node id (e.g. EHG-0001),
  `since` window (e.g. 2h). Login from backend/.env.
OUTPUT: query_frames(node, since) -> (record_id, {frame_idx: [ch1, ch2, ch3]} raw ADC numbers, time of first frame).
  e.g. query_frames("EHG-93C0", "10m") ->
       (0, {0: [1093, 171, -748], 1: [1145, 180, -790], ...}, datetime(2026, 10, 6, 2, 31, 8, 18676, tzinfo=utc))
       no rows in the window -> exits with "no ehg_raw data for EHG-93C0 in last 10m"
  query_pkts(node, since) -> list of dicts, 1 per packet (seq, t_gw_rx, t_mqtt_pub, t_db_ack, ...).
  e.g. [{"_time": ..., "node_id": "EHG-93C0", "seq": 0, "n_frames": 10, "retrans": False,
         "t_inject_dev": 98304, "t_inject_host": 1791243068.0186763, "t_gw_rx": 1791243068.468658,
         "t_mqtt_pub": 1791243068.46912, "t_ingest_rx": ..., "t_db_ack": ...}, ...]  (plus Influx's own
       "result", "table", "_start", "_stop", "_measurement" keys); no rows -> []
HOW: Flux queries (InfluxDB's query language), "pivoted" so each row = one moment with all fields side by side.
ROLE IN THESIS: Read-back half of the pipeline. query_frames is also the proposed classifier hook
  (docs/interface-spec.md 5).
BEFORE -> AFTER: Before: rows written by backend/ingest/ingest.py. After: eval/integrity.py and
  tools/fhir_export.py use query_frames; eval/latency.py uses query_pkts.
"""

from datetime import datetime
from pathlib import Path

from influxdb_client import InfluxDBClient

ENV = dict(line.split("=", 1) for line in Path(__file__).parent.parent.joinpath("backend/.env").read_text().split("\n")
           if "=" in line and not line.startswith("#"))
BUCKET = ENV["INFLUX_BUCKET"]


def client() -> InfluxDBClient:
    """Connection to the local InfluxDB (long timeout: soak queries can be large)."""
    return InfluxDBClient(url="http://localhost:8086", token=ENV["INFLUX_TOKEN"], org=ENV["INFLUX_ORG"], timeout=120_000)


def query_frames(node: str, since: str) -> tuple[int, dict[int, list[int]], datetime]:
    """Returns (record_id, {frame_idx: [c0..cN]}, time of the first frame) for the node's frames in the window."""
    q = f'''from(bucket:"{BUCKET}") |> range(start:-{since})
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


def query_pkts(node: str, since: str) -> list[dict]:
    """Returns one dict of timing fields (ehg_pkt) per packet from the node in the window."""
    q = f'''from(bucket:"{BUCKET}") |> range(start:-{since})
      |> filter(fn:(r) => r._measurement == "ehg_pkt" and r.node_id == "{node}")
      |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'''
    return [rec.values for rec in client().query_api().query_stream(q)]
