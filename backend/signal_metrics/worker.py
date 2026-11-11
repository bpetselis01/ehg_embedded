"""Signal descriptors per channel for the Grafana dashboard: RMS, median frequency, flatline and saturation.

  uv run python -m backend.signal_metrics.worker --node EHG-93C0

PURPOSE: Fills the Thesis A dashboard metrics from what the pipeline stored. These are descriptive statistics of the signal, not clinical information.
INPUT: stored frames of one node (eval/backend_client.py:query_frames), every --interval seconds.
  e.g. {0: [1093, 171, -748], 1: [1145, 180, -790], ...} at 20 Hz, 1200 frames per window
OUTPUT: InfluxDB measurement ehg_metric, tags node_id and channel, 1 point per channel per window, stamped with the time of the window's last frame.
  fields frames_present (int); and only when the window is at least 95 % complete: rms (float, raw ADC units), median_freq_hz (float, absent for a constant channel),
  flatline (bool), saturated_pct (float).
  e.g. ehg_metric,node_id=EHG-93C0,channel=c0 frames_present=1200i,rms=412.7,median_freq_hz=0.45,flatline=false,saturated_pct=0 1791243128018676224
HOW: Windows are 60 s with 30 s overlap, aligned to frame numbers, and end 8 s before the newest frame so re-sent packets have arrived. Rewriting a window therefore replaces the same point.
  Up to 5 % of missing frames are filled by linear interpolation. An FFT keeps 0.08 to 4 Hz, which is zero-phase. RMS is taken on the band-limited signal. Median frequency is where cumulative
  in-band power reaches half. Flatline and saturation use the last 10 s: flatline = zero variance on a channel, saturation = share of samples at the int16 rails. Window and band are the settings Thesis A reports for the lab's pipeline.
ROLE IN THESIS: The signal-quality, RMS and median-frequency metrics promised for Grafana in Thesis A section 8.5. It runs beside the data path, so ingest and the H1/H2/H4 numbers are unaffected.
BEFORE -> AFTER: Before: frames stored by backend/ingest/ingest.py. After: ehg_metric, read by backend/grafana/dashboards/ehg-overview.json and the alert rules.
"""

import argparse
import time
from datetime import datetime, timedelta

import numpy as np

from eval import backend_client

WINDOW_S, STEP_S, HOLD_S, QUALITY_S = 60, 30, 8, 10
BAND_HZ = (0.08, 4.0)
MIN_COMPLETE = 0.95
RAIL16 = (32767, -32768)  # shortcut: int16 rails only, use the 24-bit rails (8388607, -8388608) if 24-bit records are ever replayed


def descriptors(x: np.ndarray, fs: float) -> dict:
    """The descriptors of one channel over one window, from a gap-free array of samples."""
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, 1 / fs)
    in_band = (freqs >= BAND_HZ[0]) & (freqs <= BAND_HZ[1])
    filtered = np.fft.irfft(np.where(in_band, spectrum, 0), x.size)
    recent = x[-int(QUALITY_S * fs):]
    out = {
        "rms": float(np.sqrt(np.mean(filtered ** 2))),
        "flatline": bool(np.ptp(recent) == 0),
        "saturated_pct": float(100.0 * np.mean((recent >= RAIL16[0]) | (recent <= RAIL16[1]))),
    }
    if np.ptp(x) > 0:
        power = np.cumsum(np.abs(spectrum[in_band]) ** 2)
        if power[-1] > 0:
            out["median_freq_hz"] = float(freqs[in_band][np.searchsorted(power, power[-1] / 2)])
    return out


def latest_window(newest: int, fs: float) -> tuple[int, int] | None:
    """(first frame, end frame exclusive) of the newest whole window that re-sent packets can no longer change, or None before one exists."""
    step, size = int(STEP_S * fs), int(WINDOW_S * fs)
    end = (newest + 1 - int(HOLD_S * fs)) // step * step
    return (end - size, end) if end >= size else None


def window_results(frames: dict[int, list[int]], t_first: datetime, fs: float) -> list[dict]:
    """One {"channel", "time", "fields"} per channel for the newest window, or [] when no window is available."""
    bounds = latest_window(max(frames), fs)
    if bounds is None:
        return []
    first, end = bounds
    idx = np.array(sorted(i for i in frames if first <= i < end))
    if idx.size == 0:
        return []
    when = t_first + timedelta(seconds=(end - 1 - min(frames)) / fs)
    data = np.array([frames[int(i)] for i in idx], dtype=float)
    results = []
    for ch in range(data.shape[1]):
        fields = {"frames_present": int(idx.size)}
        if idx.size >= MIN_COMPLETE * (end - first):
            fields |= descriptors(np.interp(np.arange(first, end), idx, data[:, ch]), fs)
        results.append({"channel": f"c{ch}", "time": when, "fields": fields})
    return results


def run_once(node: str, fs: float, query, write) -> list[dict]:
    """Query, compute and write one window. No stored data is normal between runs, so it writes nothing and returns []."""
    try:
        _, frames, t_first = query(node, f"{WINDOW_S + STEP_S + HOLD_S + 5}s", latest_run=True)
    except SystemExit:
        return []
    results = window_results(frames, t_first, fs)
    if results:
        write(node, results)
    return results


def influx_writer():
    """A write(node, results) function for the local InfluxDB."""
    from influxdb_client import Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS

    api = backend_client.client().write_api(write_options=SYNCHRONOUS)

    def write(node: str, results: list[dict]) -> None:
        points = []
        for r in results:
            p = Point("ehg_metric").tag("node_id", node).tag("channel", r["channel"]).time(r["time"], WritePrecision.NS)
            for name, value in r["fields"].items():
                p = p.field(name, value)
            points.append(p)
        api.write(backend_client.influx_bucket(), record=points)

    return write


def main() -> None:
    """Compute a window every --interval seconds until stopped. A database that is restarting is reported and retried."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--node", default="EHG-93C0")
    ap.add_argument("--fs", type=float, default=20.0)
    ap.add_argument("--interval", type=float, default=STEP_S)
    args = ap.parse_args()
    write = None
    print(f"signal descriptors for {args.node}: first values about 70 s after the data starts (not clinical information)")
    while True:
        try:
            write = write or influx_writer()
            for r in run_once(args.node, args.fs, backend_client.query_frames, write):
                print(r["time"].isoformat(timespec="seconds"), r["channel"], r["fields"])
        except OSError as e:
            print(f"database not reachable, retrying: {e}")
        except Exception as e:  # the worker must outlive a bad query or a restarting database
            print(f"window skipped: {type(e).__name__}: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
