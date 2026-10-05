"""Shared InfluxDB queries for the evaluation scripts. Reads credentials from backend/.env.

PURPOSE: One place that knows how to pull stored EHG data back out of the database.
HOW: Reads the password/token from backend/.env, sends Flux queries (InfluxDB's query
  language) and "pivots" the result so each row is one moment with all its fields side by side.
ROLE IN THESIS: The read-back half of the pipeline. query_frames is also the proposed hook for
  a downstream classifier (docs/interface-spec.md section 5).
BEFORE -> AFTER: Before: rows written by backend/ingest/ingest.py. After: eval/integrity.py
  uses query_frames (samples), eval/latency.py uses query_pkts (timings).
"""

from pathlib import Path

from influxdb_client import InfluxDBClient

ENV = dict(line.split("=", 1) for line in Path(__file__).parent.parent.joinpath("backend/.env").read_text().split("\n")
           if "=" in line and not line.startswith("#"))
BUCKET = ENV["INFLUX_BUCKET"]


def client() -> InfluxDBClient:
    """Connection to the local InfluxDB (long timeout: soak queries can be large)."""
    return InfluxDBClient(url="http://localhost:8086", token=ENV["INFLUX_TOKEN"], org=ENV["INFLUX_ORG"], timeout=120_000)


def query_frames(node: str, since: str) -> tuple[int, dict[int, list[int]]]:
    """Returns (record_id, {frame_idx: [c0..cN]}) for the node's frames in the window."""
    q = f'''from(bucket:"{BUCKET}") |> range(start:-{since})
      |> filter(fn:(r) => r._measurement == "ehg_raw" and r.node_id == "{node}")
      |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'''
    frames, record_id = {}, None
    for rec in client().query_api().query_stream(q):
        v = rec.values
        record_id = int(v["record_id"])
        chans = sorted((k for k in v if k[0] == "c" and k[1:].isdigit()), key=lambda k: int(k[1:]))
        frames[int(v["frame_idx"])] = [int(v[k]) for k in chans]
    if not frames:
        raise SystemExit(f"no ehg_raw data for {node} in last {since}")
    return record_id, frames


def query_pkts(node: str, since: str) -> list[dict]:
    """Returns one dict of timing fields (ehg_pkt) per packet from the node in the window."""
    q = f'''from(bucket:"{BUCKET}") |> range(start:-{since})
      |> filter(fn:(r) => r._measurement == "ehg_pkt" and r.node_id == "{node}")
      |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'''
    return [rec.values for rec in client().query_api().query_stream(q)]
