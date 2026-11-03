"""Device registry (gateways, nodes, pseudonymous subjects) and per-node health history, in PostgreSQL.

PURPOSE: Know which gateways are enrolled, which are revoked, which node belongs to which gateway and which research subject, and whether a subject has an ACTIVE consent record (decision 0013).
  Also reads the health history the triage worker writes (ehg_health) for the trend charts. Registry changes are audited like alert changes.
INPUT: calls from the triage API and the enrolment API; the schema from migration 0002 (registry.gateway, registry.node, registry.subject) and ehg_health.
  e.g. enrol_gateway("ehg-gw-01", "python", cert_serial="1005", actor="enroll-api", role="system")
OUTPUT: dicts for list/get; revoke_gateway() sets revoked_at (and the audit row); consent_ok(node) -> True / False / None (None = the node has no subject, so there is nothing to consent to).
  trend(node, range) -> {"range", "bucket", "points": [{"t", "delivery_pct", "p95_latency_ms", "score"}], "covers_from", "complete"}: `complete` is False when the data do not reach back as far as the range asked for, and the UI says so.
HOW: A subject is a research LABEL for a public PhysioNet record (e.g. tpehg1007): no names, dates of birth or demographics. consent_status is none / active / withdrawn; only `active` allows reads.
  This is a data-model demonstration of enforced consent, not a legal consent platform. Trend buckets: 24 h per minute (raw rows), 7 d per hour, 30 d and 90 d per day (plain date_trunc, so it also works without TimescaleDB).
ROLE IN THESIS: The multi-subject data model and the device registry of the enterprise capability checklist (docs/production-topology.md).
BEFORE -> AFTER: Before: nothing recorded which gateway or subject a node belonged to. After: a registry that the APIs enforce.
"""

from datetime import datetime, timedelta, timezone

RANGES = {"24h": (timedelta(hours=24), None, "minute"), "7d": (timedelta(days=7), "hour", "hour"), "30d": (timedelta(days=30), "day", "day"), "90d": (timedelta(days=90), "day", "day")}
G_COLS = "id, kind, cert_serial, enrolled_at, revoked_at, last_seen, app_version"
N_COLS = "id, fw_version, record, fs_hz, channels, assigned_gateway, subject_id, last_seen"
S_COLS = "id, pseudonym, source_dataset, consent_status"


class UnknownGateway(Exception):
    """No such gateway in the registry."""


class RevokedGateway(Exception):
    """The gateway has been revoked and may not enrol again."""


def _dicts(cols: str, rows) -> list[dict]:
    """Rows as dicts keyed by the column list."""
    keys = [c.strip() for c in cols.split(",")]
    return [dict(zip(keys, r)) for r in rows]


class Registry:
    """`connect()` returns a psycopg connection; `audit` is a backend.triage.audit.AuditLog."""

    def __init__(self, connect, audit):
        self._connect, self._audit = connect, audit

    def gateways(self) -> list[dict]:
        """All gateways, newest enrolment first."""
        with self._connect() as c:
            return _dicts(G_COLS, c.execute(f"SELECT {G_COLS} FROM registry.gateway ORDER BY enrolled_at DESC NULLS LAST, id").fetchall())

    def gateway(self, gateway_id: str) -> dict:
        """One gateway or UnknownGateway."""
        with self._connect() as c:
            r = c.execute(f"SELECT {G_COLS} FROM registry.gateway WHERE id = %s", (gateway_id,)).fetchone()
        if r is None:
            raise UnknownGateway(gateway_id)
        return _dicts(G_COLS, [r])[0]

    def nodes(self) -> list[dict]:
        """All nodes."""
        with self._connect() as c:
            return _dicts(N_COLS, c.execute(f"SELECT {N_COLS} FROM registry.node ORDER BY id").fetchall())

    def node(self, node_id: str) -> dict | None:
        """One node, or None if it was never registered (simulated nodes usually are not)."""
        with self._connect() as c:
            r = c.execute(f"SELECT {N_COLS} FROM registry.node WHERE id = %s", (node_id,)).fetchone()
        return _dicts(N_COLS, [r])[0] if r else None

    def subjects(self) -> list[dict]:
        """All subjects."""
        with self._connect() as c:
            return _dicts(S_COLS, c.execute(f"SELECT {S_COLS} FROM registry.subject ORDER BY id").fetchall())

    def enrol_gateway(self, gateway_id: str, kind: str, cert_serial: str, actor: str, role: str, app_version: str | None = None, now: datetime | None = None, not_after: datetime | None = None) -> dict:
        """Record (or renew) a gateway's certificate; every serial is kept so a revocation can reach all of them. A revoked gateway cannot enrol again: RevokedGateway."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as c:
            r = c.execute("SELECT revoked_at FROM registry.gateway WHERE id = %s FOR UPDATE", (gateway_id,)).fetchone()
            if r and r[0] is not None:
                raise RevokedGateway(gateway_id)
            c.execute("""INSERT INTO registry.gateway (id, kind, cert_serial, enrolled_at, app_version) VALUES (%s, %s, %s, %s, %s)
                         ON CONFLICT (id) DO UPDATE SET cert_serial = EXCLUDED.cert_serial, enrolled_at = EXCLUDED.enrolled_at, app_version = EXCLUDED.app_version""",
                      (gateway_id, kind, cert_serial, now, app_version))
            c.execute("INSERT INTO registry.gateway_cert (serial, gateway_id, issued_at, not_after) VALUES (%s, %s, %s, %s) ON CONFLICT (serial) DO NOTHING",
                      (cert_serial, gateway_id, now, not_after or now + timedelta(days=7)))
            self._audit.append(actor, role, "registry.gateway_enrol", f"gateway:{gateway_id}", {"kind": kind, "serial": cert_serial}, conn=c, now=now)
        return self.gateway(gateway_id)

    def active_serials(self, gateway_id: str, now: datetime | None = None) -> list[str]:
        """Serials of this gateway's unexpired certificates that the CA has NOT yet confirmed revoked. A revocation that failed at the CA stays in this list, so repeating /revoke retries it."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as c:
            return [r[0] for r in c.execute("SELECT serial FROM registry.gateway_cert WHERE gateway_id = %s AND ca_revoked_at IS NULL AND not_after > %s ORDER BY issued_at", (gateway_id, now))]

    def mark_ca_revoked(self, serial: str, now: datetime | None = None) -> None:
        """Record that the CA confirmed this serial revoked (only then does it leave active_serials)."""
        with self._connect() as c:
            c.execute("UPDATE registry.gateway_cert SET ca_revoked_at = COALESCE(ca_revoked_at, %s) WHERE serial = %s", (now or datetime.now(timezone.utc), serial))

    def revoke_gateway(self, gateway_id: str, actor: str, role: str, now: datetime | None = None) -> dict:
        """Mark a gateway revoked (idempotent). The CRL is regenerated by the enrolment API, which is told by the caller."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as c:
            r = c.execute("UPDATE registry.gateway SET revoked_at = COALESCE(revoked_at, %s) WHERE id = %s RETURNING id", (now, gateway_id)).fetchone()
            if r is None:
                raise UnknownGateway(gateway_id)
            c.execute("UPDATE registry.gateway_cert SET revoked_at = COALESCE(revoked_at, %s) WHERE gateway_id = %s AND not_after > %s", (now, gateway_id, now))
            self._audit.append(actor, role, "registry.gateway_revoke", f"gateway:{gateway_id}", {}, conn=c, now=now)
        return self.gateway(gateway_id)

    def consent_ok(self, node_id: str) -> bool | None:
        """None: the node has no subject. True: its subject has an ACTIVE consent record. False: it does not (none or withdrawn)."""
        with self._connect() as c:
            r = c.execute("SELECT s.consent_status FROM registry.node n JOIN registry.subject s ON s.id = n.subject_id WHERE n.id = %s", (node_id,)).fetchone()
        return None if r is None else r[0] == "active"

    # ---------------------------------------------------------------- health history

    def latest_health(self, node_id: str) -> dict | None:
        """The newest ehg_health row for the node."""
        with self._connect() as c:
            r = c.execute("SELECT time, delivery_pct, p95_latency_ms, rx, missing, unrecovered, flatline_channels, saturated_pct, score FROM ehg_health WHERE node = %s ORDER BY time DESC LIMIT 1", (node_id,)).fetchone()
        return dict(zip(("time", "delivery_pct", "p95_latency_ms", "rx", "missing", "unrecovered", "flatline_channels", "saturated_pct", "score"), r)) if r else None

    def trend(self, node_id: str, range_: str, now: datetime | None = None) -> dict:
        """Health history for one of 24h, 7d, 30d, 90d; says honestly if the data do not span the whole range."""
        if range_ not in RANGES:
            raise ValueError(f"range must be one of {', '.join(RANGES)}")
        now = now or datetime.now(timezone.utc)
        span, trunc, label = RANGES[range_]
        start = now - span
        with self._connect() as c:
            first = c.execute("SELECT min(time) FROM ehg_health WHERE node = %s", (node_id,)).fetchone()[0]
            if trunc is None:
                rows = c.execute("SELECT time, delivery_pct, p95_latency_ms, score FROM ehg_health WHERE node = %s AND time >= %s AND time <= %s ORDER BY time", (node_id, start, now)).fetchall()
            else:
                rows = c.execute(f"SELECT date_trunc('{trunc}', time) AS t, avg(delivery_pct), max(p95_latency_ms), max(score) FROM ehg_health WHERE node = %s AND time >= %s AND time <= %s GROUP BY t ORDER BY t",
                                 (node_id, start, now)).fetchall()
        return {"range": range_, "bucket": label, "from": start, "to": now, "covers_from": first, "complete": first is not None and first <= start,
                "points": [{"t": r[0], "delivery_pct": r[1], "p95_latency_ms": r[2], "score": r[3]} for r in rows]}

    def sparkline(self, node_id: str, now: datetime | None = None) -> list[float]:
        """Hourly delivery_pct for the last 7 days (the worklist sparkline), oldest first; gaps are simply absent."""
        return [p["delivery_pct"] for p in self.trend(node_id, "7d", now)["points"] if p["delivery_pct"] is not None]
