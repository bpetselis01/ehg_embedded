"""Checks the stream monitor and engine (decision 0011) with synthetic packet streams and a fake clock: what each rule's input measures, that silence is attributed to the right party, that loss is
'unrecovered' only after the retry budget, that latency is off without a clock offset, flatline/saturation over 10 s, the held sequence restart, hysteresis end to end (open after 3, clear after 10),
and the once-a-minute health rows."""

from datetime import datetime, timezone

import pytest

from backend.triage.rules import Config
from backend.triage.stream import Engine, FleetMonitor, NodeMonitor

BASE = 1_791_000_000.0  # a plausible wall-clock time: the shared validator refuses epoch-0 timestamps


def pkt(seq, t_inject_host=None, node="EHG-1", frames=10, n_ch=3, flags=0, fill=None, **extra):
    """A packet message; samples vary by default (so they are not flat); `fill` makes every sample that constant."""
    samples = [[(seq * 7 + f * 3 + c * 11) % 1000 for c in range(n_ch)] for f in range(frames)] if fill is None else [[fill] * n_ch for _ in range(frames)]
    m = {"node_id": node, "seq": seq, "frame_idx": seq * frames, "t_inject": seq * 16384, "t_inject_host": BASE + seq * 0.5 if t_inject_host is None else t_inject_host,
         "n_ch": n_ch, "fs_hz": 20, "record_id": 0, "flags": flags, "samples": samples, "t_gw_rx": BASE, "t_mqtt_pub": BASE}
    m.update(extra)
    return m


def stream(mon, seqs, start=0.0, step=0.5, **kw):
    """Feed packets seqs at one per `step` seconds starting at `start`; returns the time of the last one."""
    t = start
    for s in seqs:
        mon.on_packet(pkt(s, **kw), t)
        t += step
    return t - step


def test_a_healthy_stream_has_no_missing_no_unrecovered_and_a_full_window():
    m = NodeMonitor("EHG-1")
    last = stream(m, range(130))
    s = m.snapshot(last, "online")
    assert (s.unrecovered, s.missing_60, s.flatline_channels, s.saturated_pct, s.seq_restarted) == (0, 0, 0, 0.0, False) and 118 <= s.rx_60 <= 121 and s.packet_interval_s == 0.5 and s.n_channels == 3


def test_a_lost_packet_is_unrecovered_only_after_the_retry_budget():
    m = NodeMonitor("EHG-1")
    stream(m, range(10))                       # seq 0..9 at t=0..4.5
    stream(m, range(15, 40), start=5.0)       # 10..14 never arrive
    assert m.snapshot(6.0, None).unrecovered == 0 and m.snapshot(6.0, None).missing_60 == 5, "5 missing, but still inside the 5 x 1 s + 2 s budget"
    assert m.snapshot(5.0 + 7.1, None).unrecovered == 5


def test_a_resend_that_arrives_in_time_is_not_unrecovered():
    m = NodeMonitor("EHG-1")
    stream(m, range(10))
    m.on_packet(pkt(12), 5.0)                 # 10 and 11 missing
    for i, s in enumerate((10, 11)):
        m.on_packet(pkt(s, flags=1), 6.0 + i * 0.1)  # the board re-sent them (retransmission flag)
    s = m.snapshot(20.0, None)
    assert s.unrecovered == 0 and s.missing_60 == 0


def test_loss_rate_counts_what_went_missing_in_the_last_60_s_and_still_is():
    m = NodeMonitor("EHG-1")
    stream(m, list(range(30)) + list(range(35, 60)))   # 5 missing out of 60 expected
    s = m.snapshot(30.0, None)
    assert s.missing_60 == 5 and s.rx_60 == 55
    assert m.snapshot(100.0, None).missing_60 == 0, "after 60 s the old gap no longer counts toward the loss RATE (unrecovered still does)"
    assert m.snapshot(100.0, None).unrecovered == 5


def test_latency_is_off_without_a_clock_offset_and_ignores_resends_and_replays():
    off = NodeMonitor("EHG-1")
    off.on_packet(pkt(1, t_inject_host=BASE + 10.0), BASE + 10.4)
    assert off.snapshot(BASE + 11.0, None).latency_p95_ms is None, "no offset configured: no latency claim"
    m = NodeMonitor("EHG-1", clock_offset_s=0.0)
    for i in range(20):
        m.on_packet(pkt(i, t_inject_host=BASE + 100.0 + i), BASE + 100.0 + i + 0.5)   # 500 ms each
    m.on_packet(pkt(30, t_inject_host=BASE + 100.0, flags=1), BASE + 160.0)              # a re-sent copy, 60 s old: excluded
    m.on_packet(pkt(31, t_inject_host=BASE + 100.0, replayed=True), BASE + 161.0)        # an outbox replay: excluded
    assert m.snapshot(BASE + 162.0, None).latency_p95_ms == pytest.approx(500.0, abs=1)
    shifted = NodeMonitor("EHG-1", clock_offset_s=0.2)
    shifted.on_packet(pkt(1, t_inject_host=BASE + 10.0), BASE + 10.7)
    assert shifted.snapshot(BASE + 11.0, None).latency_p95_ms == pytest.approx(500.0, abs=1), "the configured offset is removed"


def test_flatline_needs_ten_seconds_of_constant_samples_per_channel():
    m = NodeMonitor("EHG-1")
    stream(m, range(10), fill=5)               # 5 s of constant: not enough yet
    assert m.snapshot(5.0, None).flatline_channels == 0
    last = stream(m, range(10, 30), start=5.0, fill=5)
    assert m.snapshot(last, None).flatline_channels == 3
    mixed = NodeMonitor("EHG-1")
    for s in range(30):
        p = pkt(s, fill=5)
        p["samples"] = [[5, 5, f] for f in range(10)] if s >= 0 else p["samples"]
        mixed.on_packet(p, s * 0.5)
    assert mixed.snapshot(15.0, None).flatline_channels == 2, "two channels flat, one moving"


def test_saturation_uses_the_int16_rails_or_the_24_bit_rails_by_flag():
    m16 = NodeMonitor("EHG-1")
    last = stream(m16, range(30), fill=32767)
    assert m16.snapshot(last, None).saturated_pct == 100.0
    m24 = NodeMonitor("EHG-1")
    last = stream(m24, range(30), fill=32767, flags=2)
    assert m24.snapshot(last, None).saturated_pct == 0.0, "32767 is nowhere near a 24-bit rail"
    last = stream(NodeMonitor("x"), range(1), fill=0)
    m24b = NodeMonitor("EHG-1")
    last = stream(m24b, range(30), fill=8388607, flags=2)
    assert m24b.snapshot(last, None).saturated_pct == 100.0


def test_a_sequence_restart_is_held_for_five_seconds():
    m = NodeMonitor("EHG-1")
    stream(m, range(3000, 3010))
    m.on_packet(pkt(0), 10.0)                  # the node rebooted: seq jumps back by more than 1000
    assert m.snapshot(10.5, None).seq_restarted and m.snapshot(14.9, None).seq_restarted and not m.snapshot(15.1, None).seq_restarted


# ---------------------------------------------------------------- fleet and engine

class FakeStore:
    def __init__(self):
        self.calls, self.alerts = [], []

    def open_alert(self, node, rule, severity, when):
        self.calls.append(("open", node, rule, severity))
        self.alerts.append({"rule": rule, "severity": severity, "state": "open", "opened_at": when, "node": node})

    def set_severity(self, node, rule, severity):
        self.calls.append(("severity", node, rule, severity))

    def condition_cleared(self, node, rule, when):
        self.calls.append(("clear", node, rule))
        self.alerts = [a for a in self.alerts if not (a["node"] == node and a["rule"] == rule)]

    def list_alerts(self, node=None, state=None):
        return [a for a in self.alerts if node is None or a["node"] == node]


def run_engine(engine, until, start=0.0, feeder=None):
    out = []
    t = start
    while t <= until:
        if feeder:
            feeder(t)
        out += engine.tick(t)
        t += 1.0
    return out


def feeder(fleet, silent=(), lost=()):
    """Two packets per second with contiguous sequence numbers; no packets during the `silent` (from, to) window of seconds; the sequence numbers in `lost` are skipped for good."""
    n = [0]

    def feed(t):
        if any(a <= t < b for a, b in silent):
            return
        for k in (0, 1):
            if n[0] not in lost:
                fleet.on_packet(pkt(n[0]), t + 0.5 * k)
            n[0] += 1
    return feed


def test_a_nodes_first_packet_is_not_a_sequence_restart():
    m = NodeMonitor("EHG-1")
    m.on_packet(pkt(5), 1.0)
    assert m.snapshot(2.0, None).seq_restarted is False


def test_silence_opens_node_offline_after_three_bad_seconds_and_clears_after_ten_good():
    fleet, store = FleetMonitor(), FakeStore()
    fleet.on_status("ehg-gw-01", "online", 0.0)
    engine = Engine(fleet, store)
    events = run_engine(engine, 29, feeder=feeder(fleet, silent=[(20, 40)]))
    opened = [e for e in events if e[2] == "NODE_OFFLINE"]
    assert opened == [("EHG-1", "open", "NODE_OFFLINE", "critical")] and store.calls == [("open", "EHG-1", "NODE_OFFLINE", "critical")]
    assert not any(c[0] == "clear" for c in store.calls), "still silent: nothing clears"
    f = feeder(fleet, silent=[(20, 30)])
    for t in range(0, 30):
        f(float(t))                              # replay the sequence counter up to the silent window
    cleared = [e for e in run_engine(engine, 50, start=30.0, feeder=f) if e[1] == "clear" and e[2] == "NODE_OFFLINE"]
    assert len(cleared) == 1


def test_with_the_gateway_offline_the_gateway_is_blamed_not_the_node():
    fleet, store = FleetMonitor(), FakeStore()
    fleet.on_status("ehg-gw-01", "online", 0.0)
    engine = Engine(fleet, store)
    run_engine(engine, 8, feeder=feeder(fleet, silent=[(5, 100)]))
    fleet.on_status("ehg-gw-01", "offline", 9.0)
    run_engine(engine, 20, start=9.0)
    opened = [c[2] for c in store.calls if c[0] == "open"]
    assert "GATEWAY_OFFLINE" in opened and "NODE_OFFLINE" not in opened


def test_gateway_attribution_uses_the_registry_then_the_only_known_gateway():
    fleet = FleetMonitor(gateway_of=lambda node: "gw-b" if node == "EHG-1" else None)
    fleet.on_status("gw-a", "online", 0.0)
    fleet.on_status("gw-b", "offline", 0.0)
    assert fleet.gateway_status("EHG-1") == "offline" and fleet.gateway_status("EHG-2") is None, "two gateways known and no mapping: unknown, not guessed"
    solo = FleetMonitor()
    solo.on_status("gw-a", " Offline\n", 0.0)
    assert solo.gateway_status("EHG-9") == "offline"


def test_unrecovered_loss_opens_a_critical_alert_through_the_engine():
    fleet, store = FleetMonitor(), FakeStore()
    run_engine(Engine(fleet, store), 40, feeder=feeder(fleet, lost={20, 21, 22, 23}))
    assert ("open", "EHG-1", "LOSS_UNRECOVERED", "critical") in store.calls


def test_health_rows_are_written_once_a_minute_with_the_measured_values():
    fleet, store, written = FleetMonitor(), FakeStore(), []
    engine = Engine(fleet, store, sink=written.append)
    run_engine(engine, 130, feeder=feeder(fleet))
    assert [len(rows) for rows in written] == [1, 1], "at t=60 and t=120"
    row = written[-1][0]
    assert row["node"] == "EHG-1" and row["rx"] > 100 and row["delivery_pct"] == 100.0 and row["unrecovered"] == 0 and row["score"] == 0
    assert row["time"] == datetime.fromtimestamp(120.0, tz=timezone.utc) and row["p95_latency_ms"] is None


def test_thresholds_in_the_engine_config_take_effect():
    fleet, store = FleetMonitor(), FakeStore()
    run_engine(Engine(fleet, store, cfg=Config(offline_min_s=60.0)), 30, feeder=feeder(fleet, silent=[(5, 100)]))
    assert store.calls == [], "a 60 s offline threshold means 25 s of silence is not yet an alert"
    fleet2, store2 = FleetMonitor(), FakeStore()
    run_engine(Engine(fleet2, store2), 30, feeder=feeder(fleet2, silent=[(5, 100)]))
    assert ("open", "EHG-1", "NODE_OFFLINE", "critical") in store2.calls


# ---------------------------------------------------------------- hostile input (automated security review, 2026-11-03)

@pytest.mark.parametrize("bad", [{"fs_hz": 0}, {"fs_hz": float("nan")}, {"fs_hz": 10**9}, {"samples": [[1.5, 2, 3]]}, {"samples": [[0] * 65]}, {"samples": [[0, 0, 0]] * 20000}, {"samples": []},
                                 {"node_id": "bad.node"}, {"flags": "x"}, {"seq": 2**40}])
def test_the_monitor_refuses_the_messages_the_writer_refuses_and_stays_unchanged(bad):
    fleet = FleetMonitor()
    fleet.on_packet(pkt(1), 1.0)
    before = fleet.nodes["EHG-1"].snapshot(2.0, None)
    broken = pkt(2)
    broken.update(bad)
    with pytest.raises(ValueError):
        fleet.on_packet(broken, 2.0)
    assert fleet.nodes["EHG-1"].snapshot(2.0, None) == before and list(fleet.nodes) == ["EHG-1"] if bad.get("node_id") is None else not fleet.nodes.get("bad.node")


def test_a_channel_count_change_does_not_break_the_evaluation():
    m = NodeMonitor("EHG-1")
    stream(m, range(25))
    stream(m, range(25, 60), start=12.5, n_ch=16)
    s = m.snapshot(30.0, None)
    assert s.n_channels == 16 and s.flatline_channels >= 0


def test_a_huge_sequence_jump_is_a_restart_not_a_two_billion_entry_list():
    import time
    m = NodeMonitor("EHG-1")
    stream(m, range(5))
    start = time.time()
    m.on_packet(pkt(2**31 - 1), 3.0)
    assert time.time() - start < 1.0, "must not build a list of 2 billion missing numbers"
    s = m.snapshot(3.5, None)
    assert s.seq_restarted and s.missing_60 == 0 and len(m.tracker.missing) == 0


def test_the_number_of_unregistered_nodes_is_capped():
    fleet = FleetMonitor(max_nodes=3)
    for i in range(3):
        fleet.on_packet(pkt(1, node=f"N-{i}"), 1.0)
    with pytest.raises(ValueError, match="too many"):
        fleet.on_packet(pkt(1, node="N-3"), 1.0)
    fleet.on_packet(pkt(2, node="N-0"), 2.0)  # an existing node still works
    assert len(fleet.nodes) == 3


def test_kept_frames_are_bounded_whatever_the_sampling_rate():
    m = NodeMonitor("EHG-1")
    for s in range(200):
        m.on_packet(pkt(s, frames=1000, fs_hz=100000), s * 0.01)
    assert sum(n for n, _ in m._frames) <= 4000 + 1000


def test_one_bad_node_cannot_stop_the_evaluation_of_the_others(monkeypatch):
    fleet, store, logs = FleetMonitor(), FakeStore(), []
    fleet.on_packet(pkt(1, node="GOOD"), 0.0)
    fleet.on_packet(pkt(1, node="BAD"), 0.0)
    real = NodeMonitor.snapshot

    def snapshot(self, now, status):
        if self.id == "BAD":
            raise RuntimeError("corrupt state")
        return real(self, now, status)
    monkeypatch.setattr(NodeMonitor, "snapshot", snapshot)
    engine = Engine(fleet, store, log=logs.append)
    run_engine(engine, 12)
    assert ("open", "GOOD", "NODE_OFFLINE", "critical") in store.calls and any("BAD" in str(l) and "corrupt state" in str(l) for l in logs)
