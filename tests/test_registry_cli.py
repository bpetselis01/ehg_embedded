"""The registry command line (backend/triage/registry_cli.py): subjects, consent and node assignment, each audited, on a real PostgreSQL. Consent is what the FHIR worker and facade obey, so
the tests check the effect (consent_ok), the audit trail and the refusals, not only that rows change."""

import json

import psycopg
import pytest

from backend.db import migrate
from backend.triage import registry_cli
from backend.triage.audit import AuditLog
from backend.triage.registry import Registry


@pytest.fixture
def reg(pg_url):
    migrate.main(["upgrade", "0002", "--url", pg_url])
    dsn = pg_url.replace("+psycopg", "")
    connect = lambda: psycopg.connect(dsn)  # noqa: E731
    audit = AuditLog(connect, emit=lambda *_: None)
    r = Registry(connect, audit)
    r.audit = audit
    return r


def run(reg, capsys, *argv):
    code = registry_cli.main(["--actor", "cli:test", *argv], registry=reg)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_subject_consent_and_node_flow_changes_what_the_pipeline_obeys(reg, capsys):
    assert run(reg, capsys, "subject", "add", "tpehg1007", "--pseudonym", "P-0001", "--source-dataset", "tpehgdb")[0] == 0
    assert run(reg, capsys, "node", "EHG-1", "--subject", "tpehg1007")[0] == 0
    assert reg.consent_ok("EHG-1") is False, "a new subject has no consent until someone records it"
    code, out, _ = run(reg, capsys, "consent", "tpehg1007", "active")
    assert code == 0 and json.loads(out)["consent_status"] == "active" and reg.consent_ok("EHG-1") is True
    run(reg, capsys, "consent", "tpehg1007", "withdrawn")
    assert reg.consent_ok("EHG-1") is False, "withdrawal takes effect at once"


def test_every_change_is_in_the_audit_log_with_before_and_after(reg, capsys):
    run(reg, capsys, "subject", "add", "s1", "--pseudonym", "P-1", "--source-dataset", "d")
    run(reg, capsys, "consent", "s1", "active")
    run(reg, capsys, "consent", "s1", "withdrawn")
    rows = list(reversed(reg.audit.tail(10)))
    assert [r["action"] for r in rows] == ["registry.subject_add", "registry.consent_set", "registry.consent_set"]
    assert rows[1]["detail"] == {"from": "none", "to": "active"} and rows[2]["detail"] == {"from": "active", "to": "withdrawn"}
    assert all(r["actor"] == "cli:test" and r["role"] == "admin" for r in rows) and reg.audit.verify().ok


def test_refusals_change_nothing(reg, capsys):
    assert run(reg, capsys, "consent", "nobody", "active")[0] == 1, "unknown subject"
    assert run(reg, capsys, "node", "EHG-1", "--subject", "nobody")[0] == 1
    assert run(reg, capsys, "node", "EHG-1")[0] == 1, "nothing to set"
    assert run(reg, capsys, "node", "EHG-1", "--gateway", "ghost")[0] == 1, "unknown gateway"
    assert run(reg, capsys, "subject", "add", "bad id;", "--pseudonym", "p", "--source-dataset", "d")[0] == 1, "id must be plain"
    run(reg, capsys, "subject", "add", "s1", "--pseudonym", "P-1", "--source-dataset", "d")
    assert run(reg, capsys, "subject", "add", "s1", "--pseudonym", "P-2", "--source-dataset", "d")[0] == 1, "duplicate id"
    assert reg.node("EHG-1") is None and len(reg.subjects()) == 1
    with pytest.raises(SystemExit):
        registry_cli.main(["consent", "s1", "maybe"], registry=reg)  # not a consent state


def test_a_failure_does_not_print_a_traceback_or_the_connection_string(reg, capsys, monkeypatch):
    monkeypatch.delenv("EHG_TRIAGE_DSN", raising=False)
    assert registry_cli.main(["list"]) == 1
    assert "EHG_TRIAGE_DSN" in capsys.readouterr().err


def test_list_shows_the_registry(reg, capsys):
    run(reg, capsys, "subject", "add", "s1", "--pseudonym", "P-1", "--source-dataset", "d")
    code, out, _ = run(reg, capsys, "list")
    assert code == 0 and json.loads(out)["subjects"][0]["id"] == "s1"


def test_a_node_cannot_silently_change_subject_and_a_reassignment_starts_a_new_period(reg, capsys):
    for s in ("a", "b"):
        run(reg, capsys, "subject", "add", s, "--pseudonym", f"P-{s}", "--source-dataset", "d")
    run(reg, capsys, "node", "EHG-1", "--subject", "a")
    first = reg.subject_since("EHG-1")
    assert first is not None
    code, _, err = run(reg, capsys, "node", "EHG-1", "--subject", "b")
    assert code == 1 and "SubjectChange" in err and reg.node("EHG-1")["subject_id"] == "a" and reg.subject_since("EHG-1") == first
    assert run(reg, capsys, "node", "EHG-1", "--subject", "a")[0] == 0 and reg.subject_since("EHG-1") == first, "the same subject again changes nothing"
    assert run(reg, capsys, "node", "EHG-1", "--subject", "b", "--reassign")[0] == 0
    assert reg.node("EHG-1")["subject_id"] == "b" and reg.subject_since("EHG-1") > first
    last = reg.audit.tail(1)[0]
    assert last["action"] == "registry.node_assign" and last["detail"]["previous_subject"] == "a"
