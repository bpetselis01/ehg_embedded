"""Checks the triage HTTP API (decision 0011): every route against every role (401 / 403 / success), the error codes (404, 409, 422, 503), that reading and changing things is audited,
that the consent rule is enforced, that spoofed headers and dev tokens do not work in oidc mode, and that responses never contain a secret or a stack trace. Real PostgreSQL, real tokens."""

import json
import time
from datetime import datetime, timedelta, timezone

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.db import migrate
from backend.jwtauth import Authenticator, JwtVerifier
from backend.triage.app import PURPOSE, create_app
from backend.triage.audit import AuditLog
from backend.triage.registry import Registry
from backend.triage.state import AlertStore

ISS, AUD = "https://id.example.org/realms/ehg", "triage-api"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)


def token(*roles, exp=300, **over):
    claims = {"iss": ISS, "aud": AUD, "sub": "u-" + "-".join(roles), "preferred_username": "user-" + "-".join(roles) if roles else "nobody",
              "exp": int(time.time()) + exp, "realm_access": {"roles": list(roles)}}
    claims.update(over)
    return jwt.encode(claims, KEY, algorithm="RS256")


def bearer(*roles, **kw):
    return {"Authorization": "Bearer " + token(*roles, **kw)}


@pytest.fixture
def env(pg_url):
    migrate.main(["upgrade", "0002", "--url", pg_url])
    dsn = pg_url.replace("+psycopg", "")
    connect = lambda: psycopg.connect(dsn)  # noqa: E731
    audit = AuditLog(connect, emit=lambda *_: None)
    store, registry = AlertStore(connect, audit), Registry(connect, audit)
    hooks = []
    app = create_app(store=store, audit=audit, auth=Authenticator("oidc", JwtVerifier(ISS, AUD, lambda t: KEY.public_key())), registry=registry,
                     known_nodes=lambda: ["EHG-1", "EHG-2", "EHG-3"], clock=lambda: NOW, on_revoke=lambda g: hooks.append(g) or "crl rewritten")
    with psycopg.connect(dsn) as c:
        c.execute("INSERT INTO registry.subject (id, pseudonym, source_dataset, consent_status) VALUES ('tpehg1007', 'tpehg1007', 'tpehgdb', 'active'), ('tpehg2000', 'tpehg2000', 'tpehgdb', 'none')")
        c.execute("INSERT INTO registry.node (id, fs_hz, channels, subject_id) VALUES ('EHG-1', 20, 3, 'tpehg1007'), ('EHG-2', 20, 3, 'tpehg2000')")
    registry.enrol_gateway("ehg-gw-01", "python", "1005", "enroll-api", "system", now=NOW)
    a = store.open_alert("EHG-3", "FLATLINE", "warning", now=NOW - timedelta(minutes=30))
    env = type("Env", (), {})()
    env.client, env.store, env.audit, env.registry, env.alert, env.dsn, env.hooks = TestClient(app, raise_server_exceptions=False), store, audit, registry, a["id"], dsn, hooks
    return env


def actions(env):
    return [r["action"] for r in reversed(env.audit.tail(200))]


# route, method, body, minimum role
ROUTES = [
    ("GET", "/api/session", None, "viewer"), ("GET", "/api/worklist", None, "viewer"), ("GET", "/api/nodes/EHG-3", None, "viewer"), ("GET", "/api/nodes/EHG-3/trend?range=7d", None, "viewer"),
    ("GET", "/api/alerts", None, "viewer"), ("GET", "/api/alerts/{id}", None, "viewer"),
    ("POST", "/api/alerts/{id}/note", {"note": "hello"}, "operator"),  # state-neutral, so every role can be tried on the same alert; ack/escalate/resolve are tested below
    ("GET", "/api/registry/gateways", None, "admin"), ("GET", "/api/registry/nodes", None, "admin"), ("GET", "/api/registry/subjects", None, "admin"),
    ("POST", "/api/registry/gateways/ehg-gw-01/revoke", None, "admin"), ("GET", "/api/audit", None, "admin"), ("GET", "/api/audit/verify", None, "admin"),
    ("GET", "/api/config", None, "admin"), ("PUT", "/api/config", {"loss_rate": 0.02}, "admin"),
]
ORDER = ["viewer", "operator", "admin"]


@pytest.mark.parametrize("method, path, body, minimum", ROUTES, ids=[f"{m} {p.split('?')[0]}" for m, p, _, _ in ROUTES])
def test_every_route_against_every_role(env, method, path, body, minimum):
    path = path.replace("{id}", str(env.alert))
    call = lambda headers: env.client.request(method, path, json=body, headers=headers)  # noqa: E731
    assert call({}).status_code == 401
    assert call({"Authorization": "Bearer " + token("admin", exp=-10)}).status_code == 401, "an expired token is refused"
    assert call(bearer("offline_access")).status_code == 403, "a valid token with no recognised role has no access"
    for role in ORDER:
        status = call(bearer(role)).status_code
        if ORDER.index(role) < ORDER.index(minimum):
            assert status == 403, f"{role} must not {method} {path}"
        else:
            assert status == 200, f"{role} should {method} {path}, got {status}"


def test_unknown_routes_and_health_probes_need_no_login(env):
    assert env.client.get("/healthz").json() == {"status": "ok"} and env.client.get("/readyz").status_code == 200
    assert env.client.get("/api/nope", headers=bearer("admin")).status_code == 404


def test_spoofed_identity_headers_and_dev_tokens_are_ignored(env):
    for h in ({"X-User": "alice", "X-Role": "admin"}, {"X-Forwarded-User": "admin"}, {"Authorization": "Bearer dev-admin"}, {"X-Dev-User": "dev-admin:admin"}):
        assert env.client.get("/api/audit", headers=h).status_code == 401
    forged = bearer("viewer")
    forged["X-Role"] = "admin"
    assert env.client.get("/api/audit", headers=forged).status_code == 403, "a viewer token stays a viewer whatever other headers say"


def test_session_reports_the_user_the_banner_and_the_purpose(env):
    j = env.client.get("/api/session", headers=bearer("operator")).json()
    assert j["role"] == "operator" and j["banner"] is None and j["purpose"] == PURPOSE and "Not for clinical decision-making" in j["purpose"]


def test_worklist_is_ordered_and_includes_quiet_nodes(env):
    j = env.client.get("/api/worklist", headers=bearer("viewer")).json()
    assert [(n["node"], n["band"]) for n in j["nodes"]] == [("EHG-3", "warning"), ("EHG-1", "stable"), ("EHG-2", "stable")]
    assert j["nodes"][0]["score"] == 50 + 5 and all("sparkline" in n for n in j["nodes"])


# ---------------------------------------------------------------- workflow and errors

def test_workflow_through_the_api_with_the_error_codes(env):
    a = env.alert
    post = lambda action, body=None, role="operator": env.client.post(f"/api/alerts/{a}/{action}", json=body or {}, headers=bearer(role))  # noqa: E731
    assert post("escalate", {"note": ""}).status_code == 422 and "needs a note" in post("escalate", {}).json()["error"]
    assert post("escalate", {"note": "need a human"}).json()["state"] == "escalated"
    assert post("ack").status_code == 409 and "escalated" in post("ack").json()["error"]
    assert post("resolve", {"note": "fixed"}).json()["state"] == "resolved"
    assert env.client.post("/api/alerts/99999/ack", json={}, headers=bearer("operator")).status_code == 404
    assert env.client.get("/api/alerts/99999", headers=bearer("viewer")).status_code == 404
    assert post("ack", {"unexpected": 1}).status_code == 422, "unknown fields in the body are refused"


def test_reads_and_changes_are_audited_with_who_and_what(env):
    env.client.get(f"/api/alerts/{env.alert}", headers=bearer("viewer"))
    env.client.get("/api/alerts", headers=bearer("viewer"))  # a list is not a detail read
    env.client.post(f"/api/alerts/{env.alert}/ack", json={}, headers=bearer("operator"))
    env.client.get("/api/audit?limit=5", headers=bearer("admin"))
    env.client.get("/api/audit/verify", headers=bearer("admin"))
    assert actions(env)[-4:] == ["alert.read", "alert.ack", "audit.read", "audit.verify"], "the plain list was not audited"
    row = [r for r in env.audit.tail(10) if r["action"] == "alert.ack"][0]
    assert (row["actor"], row["role"], row["target"]) == ("user-operator", "operator", f"alert:{env.alert}") and env.audit.verify().ok


def test_listing_filters_and_validation(env):
    assert [a["id"] for a in env.client.get("/api/alerts?state=open", headers=bearer("viewer")).json()["alerts"]] == [env.alert]
    assert env.client.get("/api/alerts?state=bogus", headers=bearer("viewer")).status_code == 422
    assert env.client.get("/api/audit?limit=0", headers=bearer("admin")).status_code == 422 and env.client.get("/api/audit?limit=501", headers=bearer("admin")).status_code == 422


@pytest.mark.parametrize("rng, ok", [("24h", True), ("7d", True), ("30d", True), ("90d", True), ("1h", False), ("1y", False), ("", False), ("7D", False)])
def test_trend_ranges_are_validated(env, rng, ok):
    assert (env.client.get(f"/api/nodes/EHG-3/trend?range={rng}", headers=bearer("viewer")).status_code == 200) is ok


def test_trend_says_honestly_when_the_data_do_not_span_the_range(env):
    with psycopg.connect(env.dsn) as c:
        for m in range(3):
            c.execute("INSERT INTO ehg_health (time, node, delivery_pct, p95_latency_ms, score) VALUES (%s, 'EHG-3', %s, 300, 0)", (NOW - timedelta(minutes=m + 1), 99.5 - m))
    j = env.client.get("/api/nodes/EHG-3/trend?range=24h", headers=bearer("viewer")).json()
    assert len(j["points"]) == 3 and j["complete"] is False and j["bucket"] == "minute"
    with psycopg.connect(env.dsn) as c:
        c.execute("INSERT INTO ehg_health (time, node, delivery_pct, p95_latency_ms, score) VALUES (%s, 'EHG-3', 100, 250, 0)", (NOW - timedelta(days=3, hours=1),))
    j7 = env.client.get("/api/nodes/EHG-3/trend?range=7d", headers=bearer("viewer")).json()
    assert j7["bucket"] == "hour" and j7["complete"] is False and len(j7["points"]) >= 2
    assert env.client.get("/api/nodes/EHG-3/trend?range=24h", headers=bearer("viewer")).json()["covers_from"] is not None


# ---------------------------------------------------------------- consent, registry, configuration

def test_consent_rule_is_enforced_for_nodes_with_a_subject(env):
    assert env.client.get("/api/nodes/EHG-1", headers=bearer("viewer")).json()["consent"] is True, "subject with active consent"
    assert env.client.get("/api/nodes/EHG-2", headers=bearer("admin")).status_code == 403, "subject without active consent: refused even for an admin"
    assert env.client.get("/api/nodes/EHG-2/trend?range=24h", headers=bearer("viewer")).status_code == 403
    assert env.client.get("/api/nodes/EHG-3", headers=bearer("viewer")).json()["consent"] is None, "a node with no subject is not gated"
    with psycopg.connect(env.dsn) as c:
        c.execute("UPDATE registry.subject SET consent_status = 'withdrawn' WHERE id = 'tpehg1007'")
    assert env.client.get("/api/nodes/EHG-1", headers=bearer("viewer")).status_code == 403, "withdrawn consent stops reads at once"


def test_revoking_a_gateway_is_audited_calls_the_hook_and_is_idempotent(env):
    r = env.client.post("/api/registry/gateways/ehg-gw-01/revoke", headers=bearer("admin"))
    assert r.status_code == 200 and r.json()["gateway"]["revoked_at"] is not None and r.json()["crl"] == "crl rewritten" and env.hooks == ["ehg-gw-01"]
    assert env.client.post("/api/registry/gateways/ehg-gw-01/revoke", headers=bearer("admin")).status_code == 200
    assert env.client.post("/api/registry/gateways/nope/revoke", headers=bearer("admin")).status_code == 404
    assert "registry.gateway_revoke" in actions(env)


def test_config_change_is_validated_applied_and_audited_with_before_and_after(env):
    assert env.client.get("/api/config", headers=bearer("admin")).json()["loss_rate"] == 0.01
    assert env.client.put("/api/config", json={"loss_rate": 0.05}, headers=bearer("admin")).json()["loss_rate"] == 0.05
    row = [r for r in env.audit.tail(5) if r["action"] == "config.change"][0]
    assert row["detail"] == {"before": {"loss_rate": 0.01}, "after": {"loss_rate": 0.05}}
    for bad in ({"loss_rate": 5}, {"loss_rate": "high"}, {"loss_rate": True}, {"nonsense": 1}, {"latency_enabled": "yes"}, {"offline_min_s": -1}):
        assert env.client.put("/api/config", json=bad, headers=bearer("admin")).status_code == 422, bad
    assert env.client.get("/api/config", headers=bearer("admin")).json()["loss_rate"] == 0.05, "refused changes change nothing"


# ---------------------------------------------------------------- degraded, metrics, no leaks

def test_database_down_is_a_clean_503_not_a_trace(env, monkeypatch):
    def down(*a, **k):
        raise psycopg.OperationalError("connection to server at \"10.0.0.5\" failed: password authentication failed for user \"ehg_triage\"")
    monkeypatch.setattr(env.store, "list_alerts", down)
    r = env.client.get("/api/alerts", headers=bearer("viewer"))
    assert r.status_code == 503 and r.json() == {"error": "database unavailable"} and "10.0.0.5" not in r.text and "password" not in r.text


def test_an_unexpected_error_returns_500_without_internals(env, monkeypatch):
    monkeypatch.setattr(env.store, "list_alerts", lambda **k: 1 / 0)
    r = env.client.get("/api/alerts", headers=bearer("viewer"))
    assert r.status_code == 500 and "ZeroDivisionError" not in r.text and "Traceback" not in r.text


def test_readyz_reports_degraded_with_the_reason(env):
    from backend.jwtauth import Authenticator as A
    app = create_app(store=env.store, audit=env.audit, auth=A("dev"), registry=env.registry, ready=lambda: (False, "consumer lag 90 s"))
    r = TestClient(app).get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "degraded", "reason": "consumer lag 90 s"}


def test_metrics_are_for_the_internal_network_only(env):
    text = env.client.get("/metrics").text
    assert 'ehg_triage_alerts{state="open"} 1.0' in text and 'ehg_triage_alerts{state="resolved"} 0.0' in text
    assert env.client.get("/metrics", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 404, "a request that came through the proxy is refused"


def test_no_response_contains_a_secret_or_a_credential_field(env):
    bodies = []
    for method, path, body, role in [("GET", "/api/session", None, "viewer"), ("GET", "/api/worklist", None, "viewer"), ("GET", f"/api/alerts/{env.alert}", None, "viewer"),
                                     ("GET", "/api/registry/gateways", None, "admin"), ("GET", "/api/registry/nodes", None, "admin"), ("GET", "/api/audit", None, "admin"),
                                     ("GET", "/api/config", None, "admin"), ("GET", "/api/nodes/EHG-1", None, "viewer")]:
        bodies.append(env.client.request(method, path, json=body, headers=bearer(role)).text.lower())
    joined = " ".join(bodies)
    for forbidden in ("password", "private", "secret", "dsn", "begin ", "bearer "):
        assert forbidden not in joined, f"{forbidden!r} appears in a response"
    assert json.loads(bodies[0])["user"]


def test_dev_mode_shows_its_banner_and_works_on_loopback(env):
    from backend.jwtauth import Authenticator as A
    c = TestClient(create_app(store=env.store, audit=env.audit, auth=A("dev"), registry=env.registry))
    j = c.get("/api/session", headers={"Authorization": "Bearer dev-viewer"}).json()
    assert "DEV MODE" in j["banner"] and j["role"] == "viewer"
