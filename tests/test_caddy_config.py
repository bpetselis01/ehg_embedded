"""Checks the Caddyfile (Unit 11): the route list is exactly the designed one, nothing internal is proxied, every response gets the security headers, bodies are size-limited, and the file does not weaken TLS.
Structural (text) checks always run; `caddy validate` runs only where the caddy binary exists."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CADDYFILE = Path(__file__).parents[1] / "deploy/caddy/Caddyfile"
text = CADDYFILE.read_text()
body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))  # code only, not the comments


def upstreams() -> dict[str, str]:
    """handle matcher -> upstream, for each handle block."""
    return {m.group(1).strip(): m.group(2) for m in re.finditer(r"handle(?:_path)?\s*([^\{\n]*)\{[^}]*?reverse_proxy\s+(\S+)", body, re.S)} | {}


def test_the_routes_are_exactly_the_designed_ones():
    assert re.findall(r"^\s*(handle(?:_path)?)\s+(\S+)?\s*\{", body, re.M) == [("handle", "@internal"), ("handle", "/api/*"), ("handle", "/fhir/*"), ("handle", "@keycloak"), ("handle", "/auth/*"), ("handle", "/grafana/*"), ("handle_path", "/enroll-api/*"), ("handle", "")]
    proxied = re.findall(r"reverse_proxy\s+(\S+)", body)
    assert sorted(proxied) == sorted(["triage:8780", "triage:8780", "keycloak:8080", "grafana:3000", "enroll:8790", "triage:8780"])


@pytest.mark.parametrize("internal", ["prometheus", "alertmanager", "loki", "postgres", "nats", "hapi", "mosquitto", "node-exporter", "cadvisor", "alloy", "9090", "5432", "4222", "8086", "3100"])
def test_nothing_internal_is_proxied(internal):
    assert internal not in " ".join(re.findall(r"reverse_proxy\s+(\S+)", body))


def matcher(name: str) -> list[str]:
    """The path patterns of a named matcher."""
    return re.search(rf"@{name} path ([^\n]+)", body).group(1).split()


def matches(patterns: list[str], path: str) -> bool:
    """Caddy's path matcher: a trailing /* matches below the prefix; a leading */ matches under any prefix (fnmatch semantics are close enough for these literals)."""
    from fnmatch import fnmatchcase
    return any(fnmatchcase(path, p) for p in patterns)


def test_metrics_and_probe_paths_are_blocked_by_the_first_handle_block_not_a_bare_respond():
    """Regression for a review finding: Caddy runs handle blocks BEFORE a bare `respond`, so a respond beside them was unreachable."""
    assert re.search(r"handle @internal \{\s*respond 404\s*\}", body) and not re.search(r"^\s*respond @internal", body, re.M)
    assert body.index("handle @internal") < body.index("handle /api/*")
    internal = matcher("internal")
    for path in ("/metrics", "/metrics/x", "/healthz", "/readyz", "/grafana/metrics", "/enroll-api/healthz", "/api/metrics", "/auth/healthz"):
        assert matches(internal, path), f"{path} must be blocked"
    for path in ("/api/alerts", "/", "/grafana/d/abc", "/enroll-api/enroll"):
        assert not matches(internal, path), f"{path} must stay reachable"


def test_only_the_ehg_realms_public_paths_of_keycloak_are_reachable():
    kc = matcher("keycloak")
    for ok in ("/auth/realms/ehg", "/auth/realms/ehg/protocol/openid-connect/auth", "/auth/realms/ehg/protocol/openid-connect/token", "/auth/resources/x1/login/keycloak/css/login.css"):
        assert matches(kc, ok), ok
    for blocked in ("/auth/admin/master/console/", "/auth/admin/realms/ehg/users", "/auth/realms/master/protocol/openid-connect/auth", "/auth/realms/ehg-evil/x", "/auth/health", "/auth/metrics", "/auth/"):
        assert not matches(kc, blocked), blocked
    assert re.search(r"handle /auth/\* \{\s*respond 404\s*\}", body) and body.index("handle @keycloak") < body.index("handle /auth/*")


@pytest.mark.parametrize("header", ["Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Permissions-Policy", "Content-Security-Policy", "-Server"])
def test_every_response_gets_the_security_headers(header):
    assert re.search(rf"^\s*{re.escape(header)}\b", body, re.M)


def test_hsts_lasts_at_least_six_months_and_csp_allows_nothing_external_or_inline():
    max_age = int(re.search(r"max-age=(\d+)", body).group(1))
    assert max_age >= 15_552_000
    csp = re.search(r'Content-Security-Policy "([^"]+)"', body).group(1)
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp and "http:" not in csp and "*" not in csp.replace("'self'", "")
    assert "frame-ancestors 'none'" in csp and "default-src 'self'" in csp and "script-src 'self'" in csp


def test_bodies_are_size_limited_and_enrolment_more_tightly():
    sizes = re.findall(r"max_size\s+(\S+)", body)
    assert sizes == ["1MB", "16KB"]


def test_tls_is_not_weakened_and_the_domain_comes_from_the_environment():
    for weak in ("tls internal", "skip_verify", "insecure", "auto_https off", "disable_redirects", "http://"):
        assert weak not in body, weak
    assert body.lstrip().startswith("{$EHG_DOMAIN} {")


def test_the_missing_rate_limit_is_written_down_not_implied():
    assert "NO rate-limit directive" in text and "rate_limit" not in body


@pytest.mark.skipif(shutil.which("caddy") is None, reason="caddy not installed")
def test_caddy_validate_accepts_it():
    r = subprocess.run(["caddy", "validate", "--config", str(CADDYFILE), "--adapter", "caddyfile"], capture_output=True, text=True, env={**os.environ, "EHG_DOMAIN": "ehg.example.org"})
    assert r.returncode == 0, r.stderr
