"""Checks the hash-chained audit log (decision 0011) on a real PostgreSQL: the chain verifies, EVERY kind of tampering the design claims to catch is caught (edit, delete in the middle,
reorder), what it cannot catch is shown honestly (deleting the last rows; only the off-VM head hash reveals that), concurrent writers form one chain, the database refuses UPDATE/DELETE/TRUNCATE
even to the application role, and the application role can do nothing else to the log."""

import json
import threading
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from backend.db import migrate
from backend.triage.audit import GENESIS, AuditLog, canonical, chain_hash, main as audit_main


@pytest.fixture
def db(pg_url):
    migrate.main(["upgrade", "0002", "--url", pg_url])
    dsn = pg_url.replace("+psycopg", "")
    lines = []
    log = AuditLog(lambda: psycopg.connect(dsn), emit=lines.append)
    log.dsn, log.lines = dsn, lines
    return log


def fill(log, n=5):
    base = datetime(2026, 11, 3, 9, 0, 0, 123456, tzinfo=timezone.utc)
    return [log.append("alice", "operator", "alert.acknowledge", f"alert:{i}", {"note": f"n{i}", "k": [1, {"z": None}]}, now=base + timedelta(seconds=i)) for i in range(n)]


def tamper(log, sql, *args):
    """What only a database superuser can do: switch the guard triggers off, change the table, switch them on again."""
    with psycopg.connect(log.dsn, autocommit=True) as c:
        c.execute("ALTER TABLE triage.audit_log DISABLE TRIGGER USER")
        c.execute(sql, args or None)
        c.execute("ALTER TABLE triage.audit_log ENABLE TRIGGER USER")


def test_an_untouched_chain_verifies_and_links_each_row_to_the_previous_one(db):
    rows = fill(db, 5)
    assert [r["id"] for r in rows] == [1, 2, 3, 4, 5] and rows[0]["prev_hash"] == GENESIS
    assert all(rows[i]["prev_hash"] == rows[i - 1]["hash"] for i in range(1, 5))
    res = db.verify()
    assert res.ok and res.checked == 5 and res.head == rows[-1]["hash"] == db.head()[1] and db.head()[0] == 5


def test_an_empty_log_verifies_and_has_the_genesis_head(db):
    assert db.verify().ok and db.verify().checked == 0 and db.head() == (0, GENESIS)


def test_the_hash_is_reproducible_from_the_documented_formula(db):
    r = fill(db, 1)[0]
    row = {k: r[k] for k in ("id", "ts", "actor", "role", "action", "target", "detail")}
    assert r["hash"] == chain_hash(GENESIS, row) and canonical({"b": 1, "a": "é"}) == '{"a":"\\u00e9","b":1}'


def test_editing_a_row_is_detected_at_that_row(db):
    fill(db, 5)
    tamper(db, "UPDATE triage.audit_log SET actor = 'mallory' WHERE id = 3")
    res = db.verify()
    assert not res.ok and res.bad_id == 3 and "edited" in res.reason and res.checked == 2


def test_editing_the_detail_json_is_detected(db):
    fill(db, 3)
    tamper(db, "UPDATE triage.audit_log SET detail_json = '{\"note\": \"innocent\"}' WHERE id = 2")
    assert db.verify().bad_id == 2


def test_deleting_a_row_in_the_middle_is_detected(db):
    fill(db, 5)
    tamper(db, "DELETE FROM triage.audit_log WHERE id = 3")
    res = db.verify()
    assert not res.ok and res.bad_id == 4 and "deleted" in res.reason


def test_reordering_rows_is_detected(db):
    fill(db, 4)
    tamper(db, "UPDATE triage.audit_log SET id = 100 WHERE id = 2; UPDATE triage.audit_log SET id = 2 WHERE id = 3; UPDATE triage.audit_log SET id = 3 WHERE id = 100")
    assert not db.verify().ok


def test_rewriting_a_row_and_its_own_hash_still_breaks_the_next_row(db):
    rows = fill(db, 4)
    forged = chain_hash(rows[0]["hash"], {**{k: rows[1][k] for k in ("id", "ts", "role", "action", "target", "detail")}, "actor": "mallory"})
    tamper(db, "UPDATE triage.audit_log SET actor = 'mallory', hash = %s WHERE id = 2", forged)
    res = db.verify()
    assert not res.ok and res.bad_id == 3 and "prev_hash" in res.reason, "the next row still points at the old hash"


def test_deleting_the_newest_rows_is_NOT_caught_by_the_chain_only_by_the_saved_head(db):
    rows = fill(db, 5)
    saved_head = db.head()  # what the backup job would ship off the VM
    tamper(db, "DELETE FROM triage.audit_log WHERE id IN (4, 5)")
    assert db.verify().ok, "a shorter chain is still a valid chain: this is the documented limit"
    assert db.head() != saved_head and db.head()[0] == 3, "but the head no longer matches the copy kept elsewhere"
    assert rows[-1]["hash"] == saved_head[1]


def test_concurrent_writers_form_a_single_gap_free_chain(db):
    errors = []

    def writer(k):
        try:
            for i in range(10):
                db.append(f"user{k}", "operator", "alert.note", f"alert:{k}", {"i": i})
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(k,)) for k in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and db.head()[0] == 80
    res = db.verify()
    assert res.ok and res.checked == 80


def test_the_database_refuses_update_delete_and_truncate_even_with_the_owners_rights(db):
    fill(db, 2)
    with psycopg.connect(db.dsn, autocommit=True) as c:
        for sql in ("UPDATE triage.audit_log SET actor = 'x'", "DELETE FROM triage.audit_log", "TRUNCATE triage.audit_log"):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                c.execute(sql)
    assert db.verify().ok


def test_the_application_role_can_append_and_read_but_nothing_else(db):
    fill(db, 1)
    app = db.dsn.replace("user=postgres", "user=ehg_triage") if "user=" in db.dsn else db.dsn.replace("postgresql://postgres@", "postgresql://ehg_triage@")
    with psycopg.connect(app, autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM triage.audit_log").fetchone()[0] == 1
        applog = AuditLog(lambda: psycopg.connect(app))
        applog.append("svc", "system", "alert.open", "alert:9", {})
        assert applog.verify().ok and applog.verify().checked == 2
        for sql in ("UPDATE triage.audit_log SET actor = 'x'", "DELETE FROM triage.audit_log", "TRUNCATE triage.audit_log", "DROP TABLE triage.audit_log", "CREATE TABLE triage.x (a int)"):
            with pytest.raises(psycopg.Error):
                c.execute(sql)


def test_each_append_prints_one_json_line_for_loki_and_calls_the_sinks(db):
    seen = []
    db.add_sink(seen.append)
    r = fill(db, 1)[0]
    assert json.loads(db.lines[0])["audit"]["hash"] == r["hash"] and seen == [r]


def test_a_failing_sink_never_loses_the_row(db):
    def broken(row):
        raise RuntimeError("fhir server down")
    db.add_sink(broken)
    fill(db, 1)
    assert db.head()[0] == 1 and any("sink broken failed" in str(l) for l in db.lines)


def test_unusual_values_survive_the_database_round_trip_exactly(db):
    db.append("ünï", "operator", "x", "t", {"float": 0.1, "big": 2**53, "text": "line\nbreak \"quoted\" ☃", "nested": {"a": [1, 2, {"b": None}]}, "neg": -1.5e-7})
    assert db.verify().ok


def test_command_line_verify_exits_0_for_a_good_chain_and_1_for_a_tampered_one(db, capsys):
    fill(db, 3)
    assert audit_main(["--verify", "--dsn", db.dsn]) == 0 and "OK: 3 rows" in capsys.readouterr().out
    tamper(db, "UPDATE triage.audit_log SET target = 'x' WHERE id = 2")
    assert audit_main(["--verify", "--dsn", db.dsn]) == 1 and "TAMPERED at id 2" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        audit_main(["--dsn", db.dsn])
