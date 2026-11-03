"""Checks the alert lifecycle (decision 0011) on a real PostgreSQL: one active alert per node and rule, the allowed transitions, notes required where promised, auto-resolution by 'system'
(but never of an escalated alert), an audit row for every change in the same transaction, and the worklist built from stored alerts."""

import threading
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from backend.db import migrate
from backend.triage.audit import AuditLog
from backend.triage.state import AlertStore, IllegalTransition, NoteRequired, NotFound

T0 = datetime(2026, 11, 3, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(pg_url):
    migrate.main(["upgrade", "0002", "--url", pg_url])
    dsn = pg_url.replace("+psycopg", "")
    connect = lambda: psycopg.connect(dsn)  # noqa: E731
    audit = AuditLog(connect, emit=lambda *_: None)
    store = AlertStore(connect, audit)
    store.audit, store.dsn = audit, dsn
    return store


def actions(store):
    return [r["action"] for r in reversed(store.audit.tail(100))]


def test_opening_is_idempotent_and_audited(env):
    a = env.open_alert("EHG-1", "FLATLINE", "warning", now=T0)
    b = env.open_alert("EHG-1", "FLATLINE", "warning", now=T0 + timedelta(seconds=5))
    assert a["id"] == b["id"] and a["state"] == "open" and len(env.list_alerts()) == 1
    assert actions(env) == ["alert.open"] and env.audit.verify().ok


def test_the_database_itself_allows_only_one_active_alert_per_node_and_rule(env):
    env.open_alert("EHG-1", "FLATLINE", "warning", now=T0)
    with psycopg.connect(env.dsn) as c, pytest.raises(psycopg.errors.UniqueViolation):
        c.execute("INSERT INTO triage.alerts (node, rule, severity, opened_at) VALUES ('EHG-1', 'FLATLINE', 'warning', now())")


def test_concurrent_opens_still_make_one_alert(env):
    ids = []
    ts = [threading.Thread(target=lambda: ids.append(env.open_alert("EHG-9", "LOSS_RATE", "warning", now=T0)["id"])) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(ids)) == 1 and len(env.list_alerts()) == 1


def test_full_lifecycle_with_notes_and_history(env):
    a = env.open_alert("EHG-1", "NODE_OFFLINE", "critical", now=T0)["id"]
    assert env.transition(a, "ack", "alice", "operator", now=T0 + timedelta(minutes=1))["state"] == "acknowledged"
    env.transition(a, "note", "alice", "operator", note="power cycled the DK", now=T0 + timedelta(minutes=2))
    assert env.transition(a, "escalate", "alice", "operator", note="still silent", now=T0 + timedelta(minutes=3))["state"] == "escalated"
    done = env.transition(a, "resolve", "bob", "admin", note="cable was loose", now=T0 + timedelta(minutes=9))
    assert done["state"] == "resolved" and done["resolved_at"] == T0 + timedelta(minutes=9)
    h = env.get(a)
    assert [(e["action"], e["actor"], e["to_state"]) for e in h["events"]] == [("opened", "system", "open"), ("ack", "alice", "acknowledged"), ("note", "alice", "acknowledged"),
                                                                              ("escalate", "alice", "escalated"), ("resolve", "bob", "resolved")]
    assert actions(env) == ["alert.open", "alert.ack", "alert.note", "alert.escalate", "alert.resolve"] and env.audit.verify().ok


@pytest.mark.parametrize("action", ["escalate", "resolve", "note"])
@pytest.mark.parametrize("note", [None, "", "   "])
def test_notes_are_required_to_escalate_resolve_or_note(env, action, note):
    a = env.open_alert("EHG-1", "FLATLINE", "warning", now=T0)["id"]
    with pytest.raises(NoteRequired):
        env.transition(a, action, "alice", "operator", note=note)
    assert env.get(a)["state"] == "open" and actions(env) == ["alert.open"], "a refused action changes nothing and writes no audit row"


def test_ack_needs_no_note(env):
    a = env.open_alert("EHG-1", "FLATLINE", "warning", now=T0)["id"]
    assert env.transition(a, "ack", "alice", "operator")["state"] == "acknowledged"


@pytest.mark.parametrize("setup, action", [(["ack"], "ack"), (["ack", "escalate"], "ack"), (["escalate"], "ack"), (["resolve"], "ack"), (["resolve"], "escalate"),
                                           (["resolve"], "resolve"), (["escalate", "resolve"], "escalate"), (["escalate"], "escalate")])
def test_illegal_transitions_are_refused_without_side_effects(env, setup, action):
    a = env.open_alert("EHG-1", "FLATLINE", "warning", now=T0)["id"]
    for s in setup:
        env.transition(a, s, "alice", "operator", note="because")
    before = actions(env)
    with pytest.raises(IllegalTransition):
        env.transition(a, action, "alice", "operator", note="again")
    assert actions(env) == before


def test_unknown_alert_and_unknown_action(env):
    with pytest.raises(NotFound):
        env.transition(999, "ack", "alice", "operator")
    with pytest.raises(NotFound):
        env.get(999)
    a = env.open_alert("EHG-1", "FLATLINE", "warning", now=T0)["id"]
    with pytest.raises(IllegalTransition):
        env.transition(a, "delete", "alice", "operator")


def test_a_cleared_condition_auto_resolves_open_and_acknowledged_alerts_as_system(env):
    a = env.open_alert("EHG-1", "FLATLINE", "warning", now=T0)["id"]
    b = env.open_alert("EHG-1", "LOSS_RATE", "warning", now=T0)["id"]
    env.transition(b, "ack", "alice", "operator", now=T0)
    for rule, alert_id in (("FLATLINE", a), ("LOSS_RATE", b)):
        r = env.condition_cleared("EHG-1", rule, now=T0 + timedelta(minutes=5))
        assert r["state"] == "resolved" and env.get(alert_id)["events"][-1]["actor"] == "system"
    assert env.get(a)["condition_cleared_at"] == T0 + timedelta(minutes=5)
    assert env.condition_cleared("EHG-1", "FLATLINE") is None, "nothing active any more"


def test_an_escalated_alert_never_auto_resolves(env):
    a = env.open_alert("EHG-1", "FLATLINE", "warning", now=T0)["id"]
    env.transition(a, "escalate", "alice", "operator", note="human needed", now=T0)
    r = env.condition_cleared("EHG-1", "FLATLINE", now=T0 + timedelta(minutes=5))
    got = env.get(a)
    assert r["state"] == "escalated" and got["state"] == "escalated" and got["condition_cleared_at"] == T0 + timedelta(minutes=5) and got["resolved_at"] is None
    assert actions(env)[-1] == "alert.condition_cleared"


def test_after_resolution_a_returning_condition_makes_a_new_alert(env):
    a = env.open_alert("EHG-1", "FLATLINE", "warning", now=T0)["id"]
    env.condition_cleared("EHG-1", "FLATLINE", now=T0)
    b = env.open_alert("EHG-1", "FLATLINE", "critical", now=T0 + timedelta(hours=1))["id"]
    assert b != a and [x["state"] for x in env.list_alerts(node="EHG-1")] == ["open", "resolved"]


def test_severity_change_is_stored_and_audited_once(env):
    env.open_alert("EHG-1", "LATENCY_P95", "warning", now=T0)
    env.set_severity("EHG-1", "LATENCY_P95", "critical")
    env.set_severity("EHG-1", "LATENCY_P95", "critical")
    assert env.list_alerts()[0]["severity"] == "critical" and actions(env).count("alert.severity") == 1


def test_list_filters(env):
    a = env.open_alert("EHG-1", "FLATLINE", "warning", now=T0)["id"]
    env.open_alert("EHG-2", "FLATLINE", "warning", now=T0)
    env.transition(a, "ack", "alice", "operator")
    assert [x["node"] for x in env.list_alerts(state="acknowledged")] == ["EHG-1"] and [x["node"] for x in env.list_alerts(node="EHG-2")] == ["EHG-2"]


def test_node_rows_give_the_ordered_worklist_including_quiet_nodes(env):
    env.open_alert("EHG-2", "FLATLINE", "warning", now=T0)
    env.open_alert("EHG-3", "NODE_OFFLINE", "critical", now=T0)
    env.open_alert("EHG-3", "LOSS_RATE", "warning", now=T0)
    quiet = env.open_alert("EHG-4", "SATURATION", "warning", now=T0)["id"]
    env.transition(quiet, "ack", "alice", "operator", now=T0)
    rows = env.node_rows(T0 + timedelta(minutes=30), known_nodes=["EHG-1", "EHG-2", "EHG-3", "EHG-4"])
    assert [(r["node"], r["score"], r["band"]) for r in rows] == [("EHG-3", 100 + 5 + 5, "critical"), ("EHG-2", 50 + 5, "warning"), ("EHG-4", 50, "warning"), ("EHG-1", 0, "stable")]
    assert rows[0]["rules"] == ["LOSS_RATE", "NODE_OFFLINE"] and rows[2]["oldest_unacked"] is None
