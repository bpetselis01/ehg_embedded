"""Checks eval/latency.py: the drift fit recovers a clock running 50 ppm slow over 6 h, the
batching wait is counted as latency rather than hidden in the clock offset, and serial clock pings
recover the true offset that the BLE envelope fit misses by the minimum link delay (H1)."""

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


def sync_rows(offset, ppm, n=3600, seed=1):
    """Pings once a second: board stamps at true time, round trip 1-6 ms with random asymmetry."""
    rng = np.random.default_rng(seed)
    t_true = 1_700_000_000 + np.arange(n, dtype=float)
    up = 0.0005 + rng.exponential(0.001, n)
    down = 0.0005 + rng.exponential(0.002, n)
    ticks = np.round((t_true - 1_700_000_000 + offset) * (1 - ppm * 1e-6) * 32768) % 2**32
    return np.column_stack([np.arange(n), t_true - up, t_true + down, ticks])


def test_sync_mapping_recovers_offset_and_drift():
    from eval.latency import sync_mapping
    rows = sync_rows(offset=12.345, ppm=40)
    to_host, unc = sync_mapping(rows)
    err = to_host(rows[:, 3]) - (1_700_000_000 + np.arange(len(rows)))
    assert np.abs(err).max() < unc + 0.0005
    assert unc < 0.002


def test_sync_removes_the_min_link_delay_bias():
    from eval.latency import per_sample_latencies, sync_mapping
    fs, nf, link = 20.0, 10, 0.030  # every packet takes at least one 30 ms connection interval
    pkts = []
    for k in range(2000):
        t_first = 1_700_000_000 + k * nf / fs
        t_rx = t_first + (nf - 1) / fs + link
        pkts.append({"t_inject_dev": int((t_first - 1_700_000_000) * 32768), "t_gw_rx": t_rx,
                     "t_db_ack": t_rx + 0.010, "n_frames": nf, "fs_hz": fs})
    env = per_sample_latencies([dict(p) for p in pkts], fs)
    to_host, _ = sync_mapping(sync_rows(offset=0.0, ppm=0, n=1000))
    lat = per_sample_latencies(pkts, fs, to_host)
    assert abs((np.median(lat) - np.median(env)) - link) < 0.003  # envelope hid the 30 ms
