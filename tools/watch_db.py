"""Live view of packets landing in InfluxDB, for demos (the last hop of the pipeline).

Usage: uv run python tools/watch_db.py [--node EHG-93C0]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from backend_client import client, influx_bucket  # noqa: E402

NEEDED = ("seq", "n_frames", "retrans", "t_inject_host", "t_gw_rx", "t_ingest_rx", "t_db_ack")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", default="EHG-93C0")
    a = ap.parse_args()
    BUCKET = influx_bucket()
    q = f'''from(bucket:"{BUCKET}") |> range(start:-2m)
      |> filter(fn:(r) => r._measurement == "ehg_pkt" and r.node_id == "{a.node}")
      |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'''
    api, start, shown = client().query_api(), time.time(), {}
    print(f"watching InfluxDB bucket '{BUCKET}' for {a.node} ...")
    while True:
        rows = [r.values for r in api.query_stream(q)]
        # a row can be read while its fields are still being written: wait until every field is there, then show it once
        rows = sorted((v for v in rows if all(v.get(k) is not None for k in NEEDED)), key=lambda v: v["t_db_ack"])
        for v in rows:
            if v["t_db_ack"] <= start or v["_time"] in shown:
                continue
            shown[v["_time"]] = v["t_db_ack"]
            ms = lambda x, y: (v[y] - v[x]) * 1000  # noqa: E731
            note = "  RE-SENT" if v["retrans"] else ""
            print(f"seq {v['seq']:6d}  {v['n_frames']} frames  stored after {ms('t_inject_host', 't_db_ack'):6.0f} ms"
                  f" (radio+batching {ms('t_inject_host', 't_gw_rx'):.0f}, mqtt {ms('t_gw_rx', 't_ingest_rx'):.0f},"
                  f" ingest->db {ms('t_ingest_rx', 't_db_ack'):.0f}){note}", flush=True)
        shown = {t: ack for t, ack in shown.items() if ack > time.time() - 180}  # the query only looks 2 min back
        time.sleep(1)


if __name__ == "__main__":
    main()
