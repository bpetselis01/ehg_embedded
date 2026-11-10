"""Hash-chained, append-only audit log (tamper-EVIDENT, not write-once) and its verifier.

  uv run python -m backend.triage.audit --verify            # recompute the whole chain; exit 1 if anything was altered

PURPOSE: A trail of who did what to alerts, the registry and the data, that shows if it was edited afterwards (decision 0011). Every read of an alert, every transition, note, configuration change,
  export and relayed login is one row. Each row's hash covers the previous row's hash, so editing, deleting or reordering any row breaks every hash after it.
INPUT: AuditLog.append(actor, role, action, target, detail) from the triage service; for --verify the connection string in EHG_TRIAGE_DSN (libpq form) or --dsn.
  e.g. append("alice", "operator", "alert.acknowledge", "alert:17", {"note": "looking"})
OUTPUT: a row in triage.audit_log (id, ts, actor, role, action, target, detail_json, prev_hash, hash), one JSON log line on stdout per append (for Loki), and a call to each registered sink (the FHIR
  AuditEvent writer, Unit 9). --verify prints "OK: 80 rows, head 9f2c..." or "TAMPERED at id 41: hash mismatch (the row was edited)" and exits 1.
  hash = SHA-256(prev_hash || canonical JSON of {id, ts, actor, role, action, target, detail}); the first row's prev_hash is 64 zeros; ids are 1, 2, 3... with no gaps.
HOW: append() takes a transaction-level advisory lock, so concurrent writers form one chain. The database also refuses UPDATE, DELETE and TRUNCATE (triggers), and the application role has
  INSERT and SELECT only. LIMITS, said plainly: a database SUPERUSER can disable the triggers and rewrite the whole chain consistently, and deleting the LAST rows leaves a valid shorter chain.
  Both are caught only by comparing head() with a copy kept elsewhere: the backup job ships the head hash off the VM (decision 0017).
ROLE IN THESIS: The audit requirement of the enterprise chain, honestly labelled; NOT write-once storage.
BEFORE -> AFTER: Before: no audit trail. After: a verifiable chain, mirrored to logs and (Unit 9) to FHIR AuditEvent.
"""

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

GENESIS = "0" * 64
LOCK_KEY = 8_001_012  # arbitrary constant: every writer takes the same advisory lock


def canonical(row: dict) -> str:
    """Stable JSON: sorted keys, no spaces, ASCII only, NaN refused. The hash input must not depend on formatting."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def chain_hash(prev_hash: str, row: dict) -> str:
    """SHA-256 over the previous hash and the canonical row."""
    return hashlib.sha256((prev_hash + canonical(row)).encode()).hexdigest()


def iso(ts: datetime) -> str:
    """UTC with microseconds: the exact text that is hashed (the database keeps microseconds, so it round-trips)."""
    return ts.astimezone(timezone.utc).isoformat(timespec="microseconds")


@dataclass
class VerifyResult:
    """Outcome of verify(): ok, how many rows were checked, the head hash, and (if not ok) the first bad id and why."""

    ok: bool
    checked: int
    head: str
    bad_id: int | None = None
    reason: str = ""


class AuditLog:
    """`connect()` returns a psycopg connection; `sinks` are callables taking the appended row (e.g. the FHIR AuditEvent queue). A sink failure never loses the row."""

    def __init__(self, connect, sinks=(), emit=print):
        self._connect, self._sinks, self._emit = connect, list(sinks), emit

    def add_sink(self, sink) -> None:
        """Register a function that is called with every appended row (must be quick and idempotent)."""
        self._sinks.append(sink)

    def append(self, actor: str, role: str, action: str, target: str, detail: dict | None = None, conn=None, now: datetime | None = None) -> dict:
        """Add one row to the chain. With `conn` it joins the caller's transaction (so an alert change and its audit row commit together)."""
        if conn is None:
            with self._connect() as own:
                return self.append(actor, role, action, target, detail, conn=own, now=now)
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        last = conn.execute("SELECT id, hash FROM triage.audit_log ORDER BY id DESC LIMIT 1").fetchone()
        new_id, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
        ts = now or datetime.now(timezone.utc)
        row = {"id": new_id, "ts": iso(ts), "actor": actor, "role": role, "action": action, "target": target, "detail": detail or {}}
        h = chain_hash(prev, row)
        conn.execute("INSERT INTO triage.audit_log (id, ts, actor, role, action, target, detail_json, prev_hash, hash) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
                     (new_id, ts, actor, role, action, target, canonical(row["detail"]), prev, h))
        out = {**row, "prev_hash": prev, "hash": h}
        self._emit(json.dumps({"audit": out}, sort_keys=True))
        for sink in self._sinks:
            try:
                sink(out)
            except Exception as e:  # noqa: BLE001  (a broken mirror must not lose or block the audit row)
                self._emit(f"audit: sink {getattr(sink, '__name__', sink)} failed ({type(e).__name__}: {e}); the row itself is stored")
        return out

    def head(self) -> tuple[int, str]:
        """(id, hash) of the newest row, or (0, GENESIS) if empty: the value the backup ships off the VM."""
        with self._connect() as c:
            r = c.execute("SELECT id, hash FROM triage.audit_log ORDER BY id DESC LIMIT 1").fetchone()
        return (r[0], r[1]) if r else (0, GENESIS)

    def tail(self, limit: int = 20) -> list[dict]:
        """The newest rows, newest first."""
        with self._connect() as c:
            rows = c.execute("SELECT id, ts, actor, role, action, target, detail_json, prev_hash, hash FROM triage.audit_log ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
        return [{"id": r[0], "ts": iso(r[1]), "actor": r[2], "role": r[3], "action": r[4], "target": r[5], "detail": r[6], "prev_hash": r[7], "hash": r[8]} for r in rows]

    def verify(self) -> VerifyResult:
        """Recompute the chain from the first row. Detects an edited row, a deleted or inserted row in the middle, and a reordering."""
        prev, expected_id, head = GENESIS, 1, GENESIS
        with self._connect() as c, c.cursor(name="audit_verify") as cur:
            cur.execute("SELECT id, ts, actor, role, action, target, detail_json, prev_hash, hash FROM triage.audit_log ORDER BY id")
            for r in cur:
                if r[0] != expected_id:
                    return VerifyResult(False, expected_id - 1, head, r[0], f"ids jump from {expected_id - 1} to {r[0]} (a row was deleted or inserted)")
                if r[7] != prev:
                    return VerifyResult(False, expected_id - 1, head, r[0], "prev_hash does not match the previous row (a row was removed or reordered)")
                row = {"id": r[0], "ts": iso(r[1]), "actor": r[2], "role": r[3], "action": r[4], "target": r[5], "detail": r[6]}
                if chain_hash(prev, row) != r[8]:
                    return VerifyResult(False, expected_id - 1, head, r[0], "hash mismatch (the row was edited)")
                prev, head, expected_id = r[8], r[8], expected_id + 1
        return VerifyResult(True, expected_id - 1, head)


def main(argv=None) -> int:
    """Command line: --verify the chain in the database named by --dsn / EHG_TRIAGE_DSN."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true", help="recompute the whole chain; exit 1 if it was altered")
    ap.add_argument("--dsn", default=os.environ.get("EHG_TRIAGE_DSN"), help="libpq connection string (default: $EHG_TRIAGE_DSN)")
    a = ap.parse_args(argv)
    if not a.verify:
        ap.error("nothing to do: pass --verify")
    if not a.dsn:
        ap.error("no database: set EHG_TRIAGE_DSN or pass --dsn")
    import psycopg
    result = AuditLog(lambda: psycopg.connect(a.dsn)).verify()
    print(f"OK: {result.checked} rows, head {result.head}" if result.ok else f"TAMPERED at id {result.bad_id}: {result.reason} (verified {result.checked} rows before it)")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
