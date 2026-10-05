"""Checks eval/latency.py: the drift fit recovers a clock running 50 ppm slow over 6 h,
and the batching wait is counted as latency rather than hidden in the clock offset (H1)."""

import numpy as np

from eval.latency import drift_corrected_inject_host


def test_drift_fit_recovers_skewed_clock():
    # device clock runs 50 ppm slow; host rx = true inject time + delay (>= 20 ms, jittered)
    rng = np.random.default_rng(0)
    t_true = np.arange(0, 6 * 3600, 0.5)                   # 6 h, one packet per 0.5 s
    ticks = np.round(t_true * (1 - 50e-6) * 32768).astype(np.int64) & 0xFFFFFFFF
    rx = 1_700_000_000 + t_true + 0.020 + rng.exponential(0.05, len(t_true))
    est = drift_corrected_inject_host(ticks, rx)
    err = est - (1_700_000_000 + t_true)
    # residual error bounded by the min link delay (20 ms) and envelope noise, not by 1 s of drift
    assert np.abs(err - np.median(err)).max() < 0.01


def test_batching_delay_is_not_absorbed_into_clock_offset():
    from eval.latency import per_sample_latencies
    fs, nf = 20.0, 10
    pkts = []
    for k in range(2000):  # 1000 s of packets
        t_first = 1_700_000_000 + k * nf / fs
        t_rx = t_first + (nf - 1) / fs + 0.020  # sent when last frame is injected, 20 ms link
        pkts.append({"t_inject_dev": int((t_first - 1_700_000_000) * 32768), "t_gw_rx": t_rx,
                     "t_db_ack": t_rx + 0.010, "n_frames": nf, "fs_hz": fs})
    lat = per_sample_latencies(pkts, fs)
    # newest sample waits ~link+db (10 ms after offset floor), oldest waits the full batch too
    assert lat.min() >= 0
    assert abs(lat.max() - lat.min() - (nf - 1) / fs) < 0.005
    assert abs(np.median(lat) - (0.010 + 0.225)) < 0.03
