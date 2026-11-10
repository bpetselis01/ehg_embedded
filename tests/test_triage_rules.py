"""Checks the alert rules, hysteresis, severity score and worklist order (decision 0011): every rule at its boundary, node-vs-gateway attribution, the 3-up/10-down anti-fatigue behaviour,
the score formula with its caps, the bands, and that the ordering is deterministic. All pure functions; no I/O."""

import copy
from dataclasses import replace

import pytest

from backend.triage.rules import AlertView, Config, RuleTracker, Snapshot, band, evaluate, node_score, worklist


def snap(**kw):
    base = dict(now=1000.0, last_packet_t=999.8, packet_interval_s=0.5, gateway_status="online", rx_60=100, n_channels=3)
    base.update(kw)
    return Snapshot(**base)


def active(s, cfg=Config()):
    return {r: v for r, v in evaluate(s, cfg).items() if v}


def test_a_healthy_node_has_no_conditions():
    assert active(snap()) == {}


@pytest.mark.parametrize("silent, interval, expect", [(4.9, 0.5, None), (5.1, 0.5, "critical"), (5.0, 0.5, None), (11.0, 4.0, None), (12.5, 4.0, "critical")])
def test_node_offline_threshold_is_the_larger_of_5_s_and_3_intervals(silent, interval, expect):
    assert evaluate(snap(last_packet_t=1000.0 - silent, packet_interval_s=interval))["NODE_OFFLINE"] == expect


def test_a_node_that_never_sent_is_not_declared_offline_by_silence_alone():
    assert evaluate(snap(last_packet_t=None))["NODE_OFFLINE"] is None


def test_silence_is_attributed_to_the_gateway_when_it_reports_offline():
    c = active(snap(last_packet_t=980.0, gateway_status="offline"))
    assert c == {"GATEWAY_OFFLINE": "critical"}, "the node is not blamed for its gateway's silence"
    assert active(snap(gateway_status="offline"))["GATEWAY_OFFLINE"] == "critical", "a gateway that says offline is critical even while packets still arrive"
    assert active(snap(last_packet_t=980.0, gateway_status=None)) == {"NODE_OFFLINE": "critical"}


def test_unrecovered_loss_is_critical_and_loss_rate_needs_enough_packets_and_more_than_one_percent():
    assert evaluate(snap(unrecovered=1))["LOSS_UNRECOVERED"] == "critical" and evaluate(snap())["LOSS_UNRECOVERED"] is None
    assert evaluate(snap(rx_60=99, missing_60=1))["LOSS_RATE"] is None, "exactly 1 % is not above 1 %"
    assert evaluate(snap(rx_60=98, missing_60=2))["LOSS_RATE"] == "warning"
    assert evaluate(snap(rx_60=4, missing_60=5))["LOSS_RATE"] is None, "9 packets in 60 s is too few to judge"
    assert evaluate(snap(rx_60=5, missing_60=5))["LOSS_RATE"] == "warning", "10 is enough"


@pytest.mark.parametrize("p95, expect", [(None, None), (2000, None), (2001, "warning"), (5000, "warning"), (5001, "critical")])
def test_latency_bounds_are_2_s_and_5_s(p95, expect):
    assert evaluate(snap(latency_p95_ms=p95))["LATENCY_P95"] == expect


def test_latency_rule_can_be_disabled_until_the_clock_offset_is_known():
    assert evaluate(snap(latency_p95_ms=9000), Config(latency_enabled=False))["LATENCY_P95"] is None


@pytest.mark.parametrize("flat, expect", [(0, None), (1, "warning"), (2, "warning"), (3, "critical")])
def test_flatline_is_a_warning_and_critical_when_every_channel_is_flat(flat, expect):
    assert evaluate(snap(flatline_channels=flat))["FLATLINE"] == expect


@pytest.mark.parametrize("pct, expect", [(4.99, None), (5.0, "warning"), (40, "warning")])
def test_saturation_threshold(pct, expect):
    assert evaluate(snap(saturated_pct=pct))["SATURATION"] == expect


def test_sequence_restart_is_a_warning():
    assert evaluate(snap(seq_restarted=True))["SEQ_RESTART"] == "warning"


def test_thresholds_are_configurable():
    assert evaluate(snap(rx_60=95, missing_60=5), Config(loss_rate=0.10))["LOSS_RATE"] is None
    assert evaluate(snap(last_packet_t=993.0), Config(offline_min_s=10))["NODE_OFFLINE"] is None


def test_evaluate_is_pure():
    s = snap(unrecovered=2, flatline_channels=3)
    before = copy.deepcopy(s)
    assert evaluate(s) == evaluate(s) and s == before


# ---------------------------------------------------------------- hysteresis

def feed(tracker, node, states):
    """states: list of severity-or-None for one rule; returns the events per step."""
    return [tracker.update(node, {"FLATLINE": st}) for st in states]


def test_a_rule_opens_only_after_three_bad_evaluations_in_a_row():
    t = RuleTracker()
    ev = feed(t, "n", ["warning", "warning", "warning"])
    assert ev == [[], [], [("open", "FLATLINE", "warning")]] and t.is_open("n", "FLATLINE")


def test_a_single_good_evaluation_resets_the_count_to_open():
    t = RuleTracker()
    assert all(e == [] for e in feed(t, "n", ["warning", "warning", None, "warning", "warning"])) and not t.is_open("n", "FLATLINE")


def test_it_clears_only_after_ten_healthy_evaluations_in_a_row():
    t = RuleTracker()
    feed(t, "n", ["warning"] * 3)
    ev = feed(t, "n", [None] * 9)
    assert all(e == [] for e in ev) and t.is_open("n", "FLATLINE"), "nine healthy seconds are not enough"
    assert t.update("n", {"FLATLINE": None}) == [("clear", "FLATLINE")] and not t.is_open("n", "FLATLINE")


def test_flapping_does_not_clear_an_open_alert():
    t = RuleTracker()
    feed(t, "n", ["warning"] * 3)
    feed(t, "n", [None] * 9 + ["warning"] + [None] * 9)
    assert t.is_open("n", "FLATLINE")


def test_severity_change_while_open_is_reported_once_and_there_is_one_alert_per_node_and_rule():
    t = RuleTracker()
    feed(t, "n", ["warning"] * 3)
    assert t.update("n", {"FLATLINE": "critical"}) == [("update", "FLATLINE", "critical")]
    assert t.update("n", {"FLATLINE": "critical"}) == []
    assert feed(t, "n", ["warning"] * 3)[0] == [("update", "FLATLINE", "warning")]
    t2 = RuleTracker()
    ev = [e for _ in range(10) for e in t2.update("n", {"FLATLINE": "warning"})]
    assert ev == [("open", "FLATLINE", "warning")], "ten bad evaluations still make exactly one alert"


def test_nodes_and_rules_are_tracked_independently():
    t = RuleTracker()
    for _ in range(3):
        t.update("a", {"FLATLINE": "warning"})
        t.update("b", {"FLATLINE": None})
    assert t.is_open("a", "FLATLINE") and not t.is_open("b", "FLATLINE") and not t.is_open("a", "LOSS_RATE")


# ---------------------------------------------------------------- score and worklist

def alert(rule="FLATLINE", sev="warning", state="open", opened=1000.0):
    return AlertView(rule, sev, state, opened)


@pytest.mark.parametrize("alerts, now, score", [
    ([], 1000, 0),
    ([alert()], 1000, 50),
    ([alert(sev="critical")], 1000, 100),
    ([alert(sev="critical"), alert("LOSS_RATE")], 1000, 105),
    ([alert(rule=r) for r in ("A", "B", "C", "D", "E", "F")], 1000, 50 + 20),  # extra rules cap at 20
    ([alert()], 1000 + 6 * 60, 51),     # 6 minutes unacknowledged = +1
    ([alert()], 1000 + 3600, 60),       # an hour: capped at +10
    ([alert(sev="critical")], 1000 + 10 * 3600, 110),
    ([alert(state="acknowledged")], 1000 + 3600, 50),  # an acknowledged alert no longer ages the score
    ([alert(state="escalated"), alert("LOSS_RATE", state="open", opened=1000 - 600)], 1000, 50 + 5 + 1),
])
def test_score_formula(alerts, now, score):
    assert node_score(alerts, now) == score


def test_bands():
    assert [band(s) for s in (0, 50, 99, 100, 125)] == ["stable", "warning", "warning", "critical", "critical"]


def test_worklist_order_is_score_then_oldest_unacknowledged_then_node_id():
    rows = [
        {"node": "c", "score": 50, "oldest_unacked": 100.0},
        {"node": "a", "score": 50, "oldest_unacked": 100.0},
        {"node": "b", "score": 50, "oldest_unacked": 50.0},
        {"node": "z", "score": 105, "oldest_unacked": 900.0},
        {"node": "m", "score": 50, "oldest_unacked": None},
        {"node": "s", "score": 0, "oldest_unacked": None},
    ]
    assert [r["node"] for r in worklist(rows)] == ["z", "b", "a", "c", "m", "s"]
    assert worklist(list(reversed(rows))) == worklist(rows), "input order must not matter"
    assert rows[0]["node"] == "c", "the input is not modified"


def test_snapshot_replace_keeps_dataclass_semantics():
    assert replace(snap(), unrecovered=3).unrecovered == 3
