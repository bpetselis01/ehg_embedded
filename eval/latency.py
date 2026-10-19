"""H1 - end-to-end latency: sample injected in firmware -> queryable in InfluxDB.

Usage: uv run python eval/latency.py --node EHG-0001 --since 1h

PURPOSE: H1: time from "sample produced on the board" to "sample available in the database".
  Target: 95 % of samples under 2 s (stretch: under 1 s).
INPUT: ehg_pkt rows (1 per packet; timestamps t_inject_dev, t_gw_rx, t_mqtt_pub, t_ingest_rx, t_db_ack)
  from eval/backend_client.py:query_pkts. Fields: docs/interface-spec.md 5.
  Optional: eval/results/sync_<node>.csv from tools/feed.py, e.g. "7,1791243069.10012,1791243069.10498,81920".
  e.g. 1 row: t_inject_dev 98304, t_inject_host 1791243068.0187, t_gw_rx 1791243068.4687,
       t_mqtt_pub 1791243068.4691, t_ingest_rx ..., t_db_ack ...  (full row: eval/backend_client.py)
OUTPUT: printed median / p95 / p99 / max, overall and per stage (radio, MQTT, ingest, database);
  eval/results/latency_cdf_<node>.png (CDF = "fraction of samples arrived within X ms").
  e.g. (median/p95 from the 2026-10-06 OTA run; n, p99, max and stage numbers shown as x):
    offset method: serial sync (600 pings, +/-x ms); BLE envelope would read x ms lower (median)
    H1 per-sample end-to-end: n=x median=255.0 ms p95=472.0 ms p99=x ms max=x ms
       target p95 < 2000 ms: PASS; stretch p95 < 1000 ms: PASS
      inject->gateway rx (incl. batching)    n=x median=x ms p95=x ms p99=x ms max=x ms
      gateway rx->mqtt publish               ...  (same for mqtt->ingest, ingest->db ack)
    saved eval/results/latency_cdf_EHG-93C0.png
HOW: Per sample: latency = t_db_ack - (t_inject_host + i/fs) for frame i of its packet, so batching delay counts.
  Re-converts board clock to laptop time offline: linear fit of the lower envelope of (t_gw_rx - t_dev)
  removes crystal drift (20-50 ppm = 72-180 ms/h; Moon, Skelly & Towsley, INFOCOM 1999). The live
  gateway uses a fixed min-offset (no drift), fine for dashboards.
  That fit only knows the offset up to the minimum BLE delay (reads low by up to 1 connection interval,
  15-30 ms), so when eval/results/sync_<node>.csv exists (tools/feed.py clock pings over serial) the
  offset and drift come from those instead, +/- half the fastest ping round trip.
ROLE IN THESIS: The H1 result. Stage breakdown shows which part of the pipeline costs most.
BEFORE -> AFTER: Before: ehg_pkt rows written by backend/ingest/ingest.py. After: numbers on screen +
  eval/results/latency_cdf_<node>.png for the thesis. Tested by tests/test_latency.py.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))


TICK_HZ = 32768
WINDOW_S = 60


def drift_corrected_inject_host(ticks: np.ndarray, t_rx: np.ndarray) -> np.ndarray:
    """Map u32 device ticks to host epoch using a linear fit of the per-window minimum offset.

    In plain terms: in each 60 s window find the packet that arrived fastest, draw a straight
    line through those points (the slope is the clock drift), then lower the line so no packet
    comes out with a negative delay.
    """
    order = np.argsort(t_rx)
    t_dev = np.empty(len(ticks))
    t_dev[order] = np.unwrap(np.asarray(ticks, dtype=float)[order], period=2**32) / TICK_HZ
    d = t_rx - t_dev
    win = ((t_dev - t_dev.min()) // WINDOW_S).astype(int)
    mins = [(t_dev[win == w][np.argmin(d[win == w])], d[win == w].min()) for w in np.unique(win)]
    x, y = np.array(mins).T
    if len(x) < 2:  # too short to fit drift: fall back to a constant min-offset
        return t_dev + y.min()
    slope, icept = np.polyfit(x, y, 1)
    # shift the line down onto the envelope so no packet gets negative latency
    icept += (d - (slope * t_dev + icept)).min()
    return t_dev + slope * t_dev + icept


def sync_mapping(sync: np.ndarray):
    """Board ticks -> host epoch from serial clock pings (tools/feed.py, eval/results/sync_<node>.csv).

    sync columns: ping, t_send, t_recv, tick. A ping's board tick was taken somewhere between
    t_send and t_recv, so the midpoint is the best guess, wrong by at most half the round trip.
    Only the fastest 10 % of pings are used (the least queueing), and a straight line through them
    gives both offset and drift. Unlike the envelope fit this does not depend on the BLE delay.
    Returns (map_function, uncertainty_s = median half round trip of the pings used).
    shortcut: assumes the board did not reboot or wrap its 32-bit tick (36 h) during the run;
      upgrade by splitting the sync log at tick jumps if runs get that long.
    """
    rtt = sync[:, 2] - sync[:, 1]
    keep = rtt <= np.percentile(rtt, 10)
    t_dev = np.unwrap(sync[:, 3], period=2**32) / TICK_HZ
    mid = (sync[:, 1] + sync[:, 2]) / 2
    slope, icept = np.polyfit(t_dev[keep], mid[keep] - t_dev[keep], 1)
    base = t_dev[0] - sync[0, 3] / TICK_HZ  # unwrap offset of the sync series, reused for packets

    def to_host(ticks):
        t = np.asarray(ticks, dtype=float) / TICK_HZ + base
        return t + slope * t + icept

    return to_host, float(np.median(rtt[keep]) / 2)


def per_sample_latencies(pkts: list[dict], fs: float, to_host=None) -> np.ndarray:
    """Latency in seconds for every sample of every packet (includes the batching wait).

    to_host: board ticks -> host epoch from sync_mapping(); None = estimate it from the packets."""
    # A packet is sendable only once its LAST frame is injected, so align that instant with
    # t_gw_rx; aligning frame 0 would fold the batching delay into the clock offset.
    batch_s = np.array([(p["n_frames"] - 1) / fs for p in pkts])
    if to_host is not None:
        t0 = to_host([p["t_inject_dev"] for p in pkts])
    else:
        t_last_ticks = np.array([p["t_inject_dev"] for p in pkts]) + np.round(batch_s * TICK_HZ)
        t0 = drift_corrected_inject_host(t_last_ticks, np.array([p["t_gw_rx"] for p in pkts])) - batch_s
    out = []
    for p, t in zip(pkts, t0):
        p["t_inject_host"] = t  # stage breakdown below uses the corrected value too
        for i in range(int(p["n_frames"])):
            out.append(p["t_db_ack"] - (t + i / fs))
    return np.array(out)


def summary(x: np.ndarray) -> str:
    """Median / 95th / 99th percentile / max, in ms, as one line."""
    q = np.percentile(x, [50, 95, 99]) * 1000
    return f"n={len(x)} median={q[0]:.1f} ms p95={q[1]:.1f} ms p99={q[2]:.1f} ms max={x.max() * 1000:.1f} ms"


def main():
    """Query, compute, print PASS/FAIL against the H1 targets, and save the CDF plot."""
    from backend_client import query_pkts

    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--since", default="1h")
    ap.add_argument("--fs", type=float, default=20.0)
    a = ap.parse_args()
    pkts = [p for p in query_pkts(a.node, a.since) if not p["retrans"]]
    if not pkts:
        raise SystemExit("no packets")
    to_host, sync_file = None, Path("eval/results") / f"sync_{a.node}.csv"
    if sync_file.exists():
        rx = [p["t_gw_rx"] for p in pkts]
        rows = np.loadtxt(sync_file, delimiter=",", skiprows=1, ndmin=2)
        rows = rows[(rows[:, 1] > min(rx) - 60) & (rows[:, 1] < max(rx) + 60)]  # this run's pings only
        if len(rows) >= 10:
            to_host, unc = sync_mapping(rows)
    if to_host is None:
        print("offset method: BLE lower envelope (no serial sync pings for this run): latency reads low "
              "by the minimum BLE delay, up to 1 connection interval")
        lat = per_sample_latencies(pkts, a.fs)
    else:
        env = per_sample_latencies([dict(p) for p in pkts], a.fs)
        lat = per_sample_latencies(pkts, a.fs, to_host)
        print(f"offset method: serial sync ({len(rows)} pings, +/-{unc * 1000:.1f} ms); "
              f"BLE envelope would read {(np.median(lat) - np.median(env)) * 1000:.1f} ms lower (median)")
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
