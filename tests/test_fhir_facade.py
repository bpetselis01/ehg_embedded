"""Checks the read-only FHIR facade (backend/triage/fhir_api.py): roles, the strict consent rule, mandatory node scoping, GET-only, audit, a misbehaving upstream cannot leak another node's data,
and upstream failures become 502 without details. Real PostgreSQL and real tokens; the FHIR server is a fake that records the queries it receives."""

import time
from datetime import datetime, timezone

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.db import migrate
from backend.fhir_sync.worker import NODE_SYSTEM, OBS_SYSTEM
from backend.jwtauth import Authenticator, JwtVerifier
from backend.triage.app import create_app
from backend.triage.audit import AuditLog
from backend.triage.fhir_api import Upstream
from backend.triage.registry import Registry
from backend.triage.state import AlertStore

ISS, AUD = "https://id.example.org/realms/ehg", "triage-api"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)


def bearer(*roles):
    claims = {"iss": ISS, "aud": AUD, "sub": "u", "preferred_username": "user-" + "-".join(roles), "exp": int(time.time()) + 300, "realm_access": {"roles": list(roles)}}
    return {"Authorization": "Bearer " + jwt.encode(claims, KEY, algorithm="RS256")}


def obs(node, k):
    return {"resource": {"resourceType": "Observation", "identifier": [{"system": OBS_SYSTEM, "value": f"{node}/0/{k}"}]}}


@pytest.fixture
def env(pg_url):
    migrate.main(["upgrade", "0002", "--url", pg_url])
    dsn = pg_url.replace("+psycopg", "")
    connect = lambda: psycopg.connect(dsn)  # noqa: E731
    audit = AuditLog(connect, emit=lambda *_: None)
    registry = Registry(connect, audit)
    seen, answer = [], {"entries": []}

    def fake_fhir(request: httpx.Request):
        seen.append(request)
        if answer.get("down"):
            return httpx.Response(500, text="secret internal stack trace at http://hapi:8080")
        return httpx.Response(200, json={"resourceType": "Bundle", "type": "searchset", "entry": answer["entries"]})

    up = Upstream("http://hapi:8080/fhir", http=httpx.Client(transport=httpx.MockTransport(fake_fhir)))
    app = create_app(store=AlertStore(connect, audit), audit=audit, auth=Authenticator("oidc", JwtVerifier(ISS, AUD, lambda t: KEY.public_key())), registry=registry, clock=lambda: NOW, fhir_upstream=up)
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO registry.subject (id, pseudonym, source_dataset, consent_status) VALUES ('s1', 's1', 'tpehgdb', 'active'), ('s2', 's2', 'tpehgdb', 'none')")
        c.execute("INSERT INTO registry.node (id, fs_hz, channels, subject_id) VALUES ('EHG-1', 20, 3, 's1'), ('EHG-2', 20, 3, 's2'), ('EHG-3', 20, 3, NULL)")
    e = type("Env", (), {})()
    e.client, e.seen, e.answer, e.audit, e.dsn = TestClient(app, raise_server_exceptions=False), seen, answer, audit, dsn
    return e


def test_roles_viewer_can_read_and_anonymous_cannot(env):
    env.answer["entries"] = [obs("EHG-1", 0)]
    assert env.client.get("/fhir/Observation?node=EHG-1").status_code == 401
    assert env.client.get("/fhir/Observation?node=EHG-1", headers=bearer("offline_access")).status_code == 403
    r = env.client.get("/fhir/Observation?node=EHG-1", headers=bearer("viewer"))
    assert r.status_code == 200 and r.json()["total"] == 1 and r.json()["type"] == "searchset"


def test_consent_is_required_for_false_none_and_withdrawn_and_nothing_is_asked_of_the_server(env):
    for node in ("EHG-2", "EHG-3"):  # subject without consent; no subject at all
        for path in ("Observation", "Device"):
            assert env.client.get(f"/fhir/{path}?node={node}", headers=bearer("admin")).status_code == 403, f"{path} {node}: refused even for an admin"
    assert env.seen == [], "the FHIR server was never contacted for a node without consent"
    with psycopg.connect(env.dsn) as c:
        c.execute("UPDATE registry.subject SET consent_status = 'withdrawn' WHERE id = 's1'")
    assert env.client.get("/fhir/Observation?node=EHG-1", headers=bearer("viewer")).status_code == 403, "withdrawal takes effect on the next request"
    denied = [r for r in env.audit.tail(20) if r["action"] == "consent.denied"]
    assert len(denied) == 5 and all(r["detail"].get("via") == "fhir" for r in denied)


def test_node_is_mandatory_and_validated_so_there_is_no_unscoped_search(env):
    h = bearer("admin")
    for q in ("", "?node=", "?node=EHG-1.%2A", "?node=EHG-1%7Cx", "?node=a%20b"):
        assert env.client.get("/fhir/Observation" + q, headers=h).status_code == 422, q
        assert env.client.get("/fhir/Device" + q, headers=h).status_code == 422, q
    assert env.client.get("/fhir/Observation?node=EHG-1&_count=0", headers=h).status_code == 422
    assert env.client.get("/fhir/Observation?node=EHG-1&_count=21", headers=h).status_code == 422
    assert env.seen == []


def test_only_get_is_possible(env):
    h = bearer("admin")
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert env.client.request(method, "/fhir/Observation?node=EHG-1", headers=h, json={}).status_code == 405, method
        assert env.client.request(method, "/fhir/Observation/123", headers=h, json={}).status_code in (404, 405)
    assert env.seen == []


def test_the_upstream_query_is_scoped_to_the_node_and_encoded(env):
    env.client.get("/fhir/Observation?node=EHG-1&_count=3", headers=bearer("viewer"))
    q = env.seen[0].url.params
    assert q["device.identifier"] == f"{NODE_SYSTEM}|EHG-1" and q["_count"] == "3" and q["_sort"] == "-date"
    assert env.seen[0].method == "GET"


def test_a_misbehaving_upstream_cannot_leak_another_nodes_data(env):
    env.answer["entries"] = [obs("EHG-1", 0), obs("EHG-2", 0), obs("EHG-10", 0), {"resource": {"resourceType": "Patient", "name": [{"family": "X"}]}}]
    j = env.client.get("/fhir/Observation?node=EHG-1", headers=bearer("viewer")).json()
    assert [e["resource"]["identifier"][0]["value"] for e in j["entry"]] == ["EHG-1/0/0"], "EHG-2, the prefix-lookalike EHG-10 and a non-Observation are dropped"


def test_upstream_failure_is_502_without_details(env):
    env.answer["down"] = True
    r = env.client.get("/fhir/Observation?node=EHG-1", headers=bearer("viewer"))
    assert r.status_code == 502 and "secret" not in r.text and "hapi" not in r.text


def test_reads_are_audited_and_capability_statement_is_read_only(env):
    env.client.get("/fhir/Device?node=EHG-1", headers=bearer("viewer"))
    assert [r["action"] for r in env.audit.tail(5)][0] == "fhir.read"
    cs = env.client.get("/fhir/metadata", headers=bearer("viewer")).json()
    interactions = {i["code"] for res in cs["rest"][0]["resource"] for i in res["interaction"]}
    assert cs["resourceType"] == "CapabilityStatement" and interactions == {"read", "search-type"}
    assert "Not for clinical" in cs["description"] or "not for clinical" in cs["description"].lower()


def test_without_a_configured_fhir_server_the_routes_do_not_exist(pg_url):
    migrate.main(["upgrade", "0002", "--url", pg_url])
    dsn = pg_url.replace("+psycopg", "")
    connect = lambda: psycopg.connect(dsn)  # noqa: E731
    audit = AuditLog(connect, emit=lambda *_: None)
    app = create_app(store=AlertStore(connect, audit), audit=audit, auth=Authenticator("oidc", JwtVerifier(ISS, AUD, lambda t: KEY.public_key())), registry=Registry(connect, audit))
    assert TestClient(app).get("/fhir/metadata", headers=bearer("admin")).status_code == 404


def test_fhir_responses_are_never_cached_including_errors(env):
    """Waveform data and consent refusals must not sit in a browser or proxy cache (automated review, Unit 9)."""
    env.answer["entries"] = [obs("EHG-1", 0)]
    for path, h in (("/fhir/Observation?node=EHG-1", bearer("viewer")), ("/fhir/Device?node=EHG-1", bearer("viewer")), ("/fhir/metadata", bearer("viewer")),
                    ("/fhir/Observation?node=EHG-2", bearer("viewer")), ("/fhir/Observation?node=bad.id", bearer("viewer")), ("/fhir/Observation?node=EHG-1", {})):
        r = env.client.get(path, headers=h)
        assert r.headers.get("cache-control") == "no-store", f"{path} -> {r.status_code}"


def test_the_service_configures_the_facade_from_the_environment():
    from backend.triage.fhir_api import upstream_from_env
    assert upstream_from_env({}) is None and upstream_from_env({"FHIR_BASE_URL": "  "}) is None, "no server configured: no /fhir routes"
    assert upstream_from_env({"FHIR_BASE_URL": "http://hapi:8080/fhir"}) is not None


def test_observations_from_before_the_current_subject_was_assigned_are_hidden(env):
    """A node moved to another subject must not show the earlier subject's windows under the new subject's consent (security review, 2026-11-04)."""
    def window(k, start):
        o = obs("EHG-1", k)
        o["resource"]["effectivePeriod"] = {"start": start, "end": start}
        return o
    with psycopg.connect(env.dsn) as c:
        c.execute("UPDATE registry.node SET subject_since = '2026-11-03T12:00:00+00:00' WHERE id = 'EHG-1'")
    env.answer["entries"] = [window(0, "2026-11-03T11:59:30+00:00"), window(1, "2026-11-03T12:00:00+00:00"), window(2, "2026-11-03T12:00:30+00:00"), obs("EHG-1", 3)]
    got = [e["resource"]["identifier"][0]["value"] for e in env.client.get("/fhir/Observation?node=EHG-1", headers=bearer("viewer")).json()["entry"]]
    assert got == ["EHG-1/0/1", "EHG-1/0/2"], "window 0 is older, window 3 has no readable start (hidden, not guessed)"
    with psycopg.connect(env.dsn) as c:
        c.execute("UPDATE registry.node SET subject_since = NULL WHERE id = 'EHG-1'")
    assert len(env.client.get("/fhir/Observation?node=EHG-1", headers=bearer("viewer")).json()["entry"]) == 4, "rows written by hand have no assignment time and hide nothing"
