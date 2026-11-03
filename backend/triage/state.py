"""Alert state in PostgreSQL: the lifecycle open -> acknowledged -> escalated -> resolved, with required notes, auto-resolution and an audit row for every change.

PURPOSE: The operator's alert workflow (decision 0011). The rules (backend/triage/rules.py) say WHEN an alert opens or clears; this module stores alerts, enforces the allowed transitions,
  and writes who did what into the audit chain (backend/triage/audit.py) in the SAME transaction, so a change without its audit row cannot happen.
INPUT: open_alert / set_severity / condition_cleared from the stream worker (actor "system"); transition(alert_id, action, actor, role, note) from the API. Actions: ack, escalate, resolve, note.
  e.g. transition(17, "escalate", "alice", "operator", note="node 2 flat for 10 min, phoning the bench")
OUTPUT: alert dicts {id, node, rule, severity, state, opened_at, condition_cleared_at, resolved_at}; node_rows() for the worklist. Errors with a name: NotFound (404), IllegalTransition (409), NoteRequired (422).
  Transitions: ack open->acknowledged; escalate open|acknowledged->escalated (note required); resolve open|acknowledged|escalated->resolved (note required); note any state (note required, state unchanged).
HOW: One ACTIVE alert per (node, rule) is enforced by the database (partial unique index), so open_alert is idempotent. When a condition clears: open and acknowledged alerts resolve automatically as actor
  "system" (condition_cleared_at set); an ESCALATED alert never auto-resolves, a human must (its condition_cleared_at is still recorded).
ROLE IN THESIS: Alert management for the triage page. Operational alerts about the pipeline, not clinical ones.
BEFORE -> AFTER: Before: rules without memory. After: alerts that persist, are worked, and are audited.
"""

from datetime import datetime, timezone

from backend.triage.rules import AlertView, band, node_score, worklist

ACTIVE = ("open", "acknowledged", "escalated")
TRANSITIONS = {"ack": ({"open"}, "acknowledged", False), "escalate": ({"open", "acknowledged"}, "escalated", True), "resolve": ({"open", "acknowledged", "escalated"}, "resolved", True)}
COLS = "id, node, rule, severity, state, opened_at, condition_cleared_at, resolved_at"


class NotFound(Exception):
    """No such alert."""


class IllegalTransition(Exception):
    """The alert is not in a state that allows this action."""


class NoteRequired(Exception):
    """Escalating, resolving and noting need a non-empty note."""


def _alert(r) -> dict:
    """A database row as the dict the API returns."""
    keys = ("id", "node", "rule", "severity", "state", "opened_at", "condition_cleared_at", "resolved_at")
    return dict(zip(keys, r))


class AlertStore:
    """`connect()` returns a psycopg connection; `audit` is a backend.triage.audit.AuditLog. Every method is one transaction."""

    def __init__(self, connect, audit):
        self._connect, self._audit = connect, audit

    def open_alert(self, node: str, rule: str, severity: str, now: datetime | None = None) -> dict:
        """Open the alert for (node, rule), or return the one that is already active."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as c:
            row = c.execute(f"INSERT INTO triage.alerts (node, rule, severity, state, opened_at) VALUES (%s, %s, %s, 'open', %s) ON CONFLICT (node, rule) WHERE state <> 'resolved' DO NOTHING RETURNING {COLS}",
                            (node, rule, severity, now)).fetchone()
            if row is None:
                return _alert(c.execute(f"SELECT {COLS} FROM triage.alerts WHERE node = %s AND rule = %s AND state <> 'resolved'", (node, rule)).fetchone())
            self._event(c, row[0], now, "system", "system", "opened", None, "open", f"{rule} {severity}")
            self._audit.append("system", "system", "alert.open", f"alert:{row[0]}", {"node": node, "rule": rule, "severity": severity}, conn=c, now=now)
            return _alert(row)

    def set_severity(self, node: str, rule: str, severity: str) -> None:
        """The condition got worse or better while the alert stays open."""
        with self._connect() as c:
            r = c.execute("UPDATE triage.alerts SET severity = %s WHERE node = %s AND rule = %s AND state <> 'resolved' AND severity <> %s RETURNING id", (severity, node, rule, severity)).fetchone()
            if r:
                self._audit.append("system", "system", "alert.severity", f"alert:{r[0]}", {"severity": severity}, conn=c)

    def condition_cleared(self, node: str, rule: str, now: datetime | None = None) -> dict | None:
        """The rule has been healthy long enough. Auto-resolve open/acknowledged alerts as 'system'; an escalated alert only gets condition_cleared_at."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as c:
            r = c.execute(f"SELECT {COLS} FROM triage.alerts WHERE node = %s AND rule = %s AND state <> 'resolved' FOR UPDATE", (node, rule)).fetchone()
            if r is None:
                return None
            a = _alert(r)
            c.execute("UPDATE triage.alerts SET condition_cleared_at = %s WHERE id = %s", (now, a["id"]))
            if a["state"] == "escalated":
                self._audit.append("system", "system", "alert.condition_cleared", f"alert:{a['id']}", {"auto_resolved": False}, conn=c, now=now)
                return {**a, "condition_cleared_at": now}
            c.execute("UPDATE triage.alerts SET state = 'resolved', resolved_at = %s WHERE id = %s", (now, a["id"]))
            self._event(c, a["id"], now, "system", "system", "auto_resolve", a["state"], "resolved", "condition cleared")
            self._audit.append("system", "system", "alert.auto_resolve", f"alert:{a['id']}", {"from": a["state"]}, conn=c, now=now)
            return {**a, "state": "resolved", "condition_cleared_at": now, "resolved_at": now}

    def transition(self, alert_id: int, action: str, actor: str, role: str, note: str | None = None, now: datetime | None = None) -> dict:
        """Apply ack / escalate / resolve / note by a person. Raises NotFound, IllegalTransition or NoteRequired and changes nothing in those cases."""
        now = now or datetime.now(timezone.utc)
        if action not in (*TRANSITIONS, "note"):
            raise IllegalTransition(f"unknown action {action!r}")
        note = (note or "").strip() or None
        with self._connect() as c:
            r = c.execute(f"SELECT {COLS} FROM triage.alerts WHERE id = %s FOR UPDATE", (alert_id,)).fetchone()
            if r is None:
                raise NotFound(f"alert {alert_id}")
            a = _alert(r)
            if action == "note":
                if not note:
                    raise NoteRequired("a note needs text")
                self._event(c, alert_id, now, actor, role, "note", a["state"], a["state"], note)
                self._audit.append(actor, role, "alert.note", f"alert:{alert_id}", {"note": note}, conn=c, now=now)
                return a
            allowed, to_state, needs_note = TRANSITIONS[action]
            if a["state"] not in allowed:
                raise IllegalTransition(f"cannot {action} an alert that is {a['state']}")
            if needs_note and not note:
                raise NoteRequired(f"{action} needs a note explaining why")
            c.execute("UPDATE triage.alerts SET state = %s, resolved_at = %s WHERE id = %s", (to_state, now if to_state == "resolved" else None, alert_id))
            self._event(c, alert_id, now, actor, role, action, a["state"], to_state, note)
            self._audit.append(actor, role, f"alert.{action}", f"alert:{alert_id}", {"from": a["state"], "to": to_state, "note": note}, conn=c, now=now)
            return {**a, "state": to_state, "resolved_at": now if to_state == "resolved" else None}

    def get(self, alert_id: int) -> dict:
        """One alert with its history (alert_events), oldest first. The API audits the read."""
        with self._connect() as c:
            r = c.execute(f"SELECT {COLS} FROM triage.alerts WHERE id = %s", (alert_id,)).fetchone()
            if r is None:
                raise NotFound(f"alert {alert_id}")
            ev = c.execute("SELECT ts, actor, role, action, from_state, to_state, note FROM triage.alert_events WHERE alert_id = %s ORDER BY id", (alert_id,)).fetchall()
        return {**_alert(r), "events": [dict(zip(("ts", "actor", "role", "action", "from_state", "to_state", "note"), e)) for e in ev]}

    def list_alerts(self, state: str | None = None, node: str | None = None) -> list[dict]:
        """Alerts newest first, optionally one state and/or one node."""
        with self._connect() as c:
            rows = c.execute(f"SELECT {COLS} FROM triage.alerts WHERE (%s::text IS NULL OR state = %s) AND (%s::text IS NULL OR node = %s) ORDER BY opened_at DESC, id DESC", (state, state, node, node)).fetchall()
        return [_alert(r) for r in rows]

    def node_rows(self, now: datetime, known_nodes=()) -> list[dict]:
        """One worklist row per node (every known node, even with no alerts): score, band, open alerts, oldest unacknowledged alert; most urgent first."""
        with self._connect() as c:
            rows = c.execute(f"SELECT {COLS} FROM triage.alerts WHERE state <> 'resolved'").fetchall()
        by_node: dict[str, list[dict]] = {n: [] for n in known_nodes}
        for r in rows:
            by_node.setdefault(r[1], []).append(_alert(r))
        out = []
        for node, alerts in by_node.items():
            views = [AlertView(a["rule"], a["severity"], a["state"], a["opened_at"].timestamp()) for a in alerts]
            score = node_score(views, now.timestamp())
            unacked = [v.opened_at for v in views if v.state == "open"]
            out.append({"node": node, "score": score, "band": band(score), "open_alerts": len(alerts), "oldest_unacked": min(unacked) if unacked else None,
                        "rules": sorted({a["rule"] for a in alerts})})
        return worklist(out)

    @staticmethod
    def _event(c, alert_id, ts, actor, role, action, from_state, to_state, note) -> None:
        """One line of an alert's history."""
        c.execute("INSERT INTO triage.alert_events (alert_id, ts, actor, role, action, from_state, to_state, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                  (alert_id, ts, actor, role, action, from_state, to_state, note))
