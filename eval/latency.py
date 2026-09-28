"""H1 - end-to-end latency: sample injected in firmware -> queryable in InfluxDB.

Per-sample latency = t_db_ack - (t_inject_host + i/fs) for frame i of each packet, so
batching delay is included. Prints median/p95/p99/max overall and per stage; saves a CDF.

Caveat (report with results): t_inject_host comes from the gateway's min-offset clock
map, so absolute values exclude the minimum BLE link delay (one connection interval
at most). See gateway/tracking.py:ClockMap.

Usage: uv run python eval/latency.py --node EHG-0001 --since 1h
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))


def per_sample_latencies(pkts: list[dict], fs: float) -> np.ndarray:
    out = []
    for p in pkts:
        for i in range(int(p["n_frames"])):
            out.append(p["t_db_ack"] - (p["t_inject_host"] + i / fs))
    return np.array(out)


def summary(x: np.ndarray) -> str:
    q = np.percentile(x, [50, 95, 99]) * 1000
    return f"n={len(x)} median={q[0]:.1f} ms p95={q[1]:.1f} ms p99={q[2]:.1f} ms max={x.max() * 1000:.1f} ms"


def main():
    from backend_client import query_pkts

    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--since", default="1h")
    ap.add_argument("--fs", type=float, default=20.0)
    a = ap.parse_args()
    pkts = [p for p in query_pkts(a.node, a.since) if not p["retrans"]]
    if not pkts:
        raise SystemExit("no packets")
    lat = per_sample_latencies(pkts, a.fs)
    print("H1 per-sample end-to-end:", summary(lat))
    print(f"   target p95 < 2000 ms: {'PASS' if np.percentile(lat, 95) < 2 else 'FAIL'};"
          f" stretch p95 < 1000 ms: {'PASS' if np.percentile(lat, 95) < 1 else 'FAIL'}")
    stages = [("inject->gateway rx (incl. batching)", "t_inject_host", "t_gw_rx"),
              ("gateway rx->mqtt publish", "t_gw_rx", "t_mqtt_pub"),
              ("mqtt publish->ingest rx", "t_mqtt_pub", "t_ingest_rx"),
              ("ingest rx->db ack", "t_ingest_rx", "t_db_ack")]
    for name, a0, a1 in stages:
        print(f"  {name:38s}", summary(np.array([p[a1] - p[a0] for p in pkts])))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out = Path("eval/results")
    out.mkdir(parents=True, exist_ok=True)
    s = np.sort(lat) * 1000
    plt.plot(s, np.arange(1, len(s) + 1) / len(s))
    plt.axvline(2000, ls="--", c="r", label="H1 target 2 s")
    plt.axvline(1000, ls=":", c="orange", label="stretch 1 s")
    plt.xlabel("latency (ms)"); plt.ylabel("CDF"); plt.title(f"H1 latency CDF - {a.node}"); plt.legend()
    f = out / f"latency_cdf_{a.node}.png"
    plt.savefig(f, dpi=120)
    print("saved", f)


if __name__ == "__main__":
    main()
