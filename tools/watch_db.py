"""Live view of packets landing in InfluxDB, for demos (the last hop of the pipeline).

Usage: uv run python tools/watch_db.py [--node EHG-93C0]

PURPOSE: Shows each packet once the database has stored it, with how long it took end to end.
INPUT: ehg_pkt rows in InfluxDB (written by backend/ingest/ingest.py), polled every second via
  eval/backend_client.py. Login from backend/.env.
OUTPUT: 1 line per stored packet, oldest first, e.g.
    seq    412  10 frames  stored after    476 ms (radio+batching 462, mqtt 3, ingest->db 9)
    seq    398  10 frames  stored after   7213 ms (radio+batching 7190, mqtt 4, ingest->db 12)  RE-SENT
  "stored after" = DB acknowledged - frame 0 sampled on the board (live clock mapping, so rough;
  eval/latency.py gives the exact H1 numbers). RE-SENT = recovered by the gateway's resend request.
HOW: Every second, read the last 2 minutes of ehg_pkt and print rows acknowledged since the last poll.
ROLE IN THESIS: Demo aid only; not a measurement.
BEFORE -> AFTER: Before: backend running, gateway publishing. After: your screen.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from backend_client import BUCKET, client  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", default="EHG-93C0")
    a = ap.parse_args()
    q = f'''from(bucket:"{BUCKET}") |> range(start:-2m)
      |> filter(fn:(r) => r._measurement == "ehg_pkt" and r.node_id == "{a.node}")
      |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'''
    api, last_ack = client().query_api(), time.time()
    print(f"watching InfluxDB bucket '{BUCKET}' for {a.node} ...")
    while True:
        rows = [r.values for r in api.query_stream(q)]
        rows = sorted((v for v in rows if v.get("t_db_ack") is not None), key=lambda v: v["t_db_ack"])  # skip half-written rows
        for v in rows:
            if v["t_db_ack"] <= last_ack:
                continue
            last_ack = v["t_db_ack"]
            ms = lambda x, y: (v[y] - v[x]) * 1000  # noqa: E731
            note = "  RE-SENT" if v["retrans"] else ""
            print(f"seq {v['seq']:6d}  {v['n_frames']} frames  stored after {ms('t_inject_host', 't_db_ack'):6.0f} ms"
                  f" (radio+batching {ms('t_inject_host', 't_gw_rx'):.0f}, mqtt {ms('t_gw_rx', 't_ingest_rx'):.0f},"
                  f" ingest->db {ms('t_ingest_rx', 't_db_ack'):.0f}){note}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
