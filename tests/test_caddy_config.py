"""Checks the Caddyfile (Unit 11): the route list is exactly the designed one, nothing internal is proxied, every response gets the security headers, bodies are size-limited, and the file does not weaken TLS.
Structural (text) checks always run; `caddy validate` runs only where the caddy binary exists."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
CADDYFILE = ROOT / "deploy/caddy/Caddyfile"
text = CADDYFILE.read_text()
body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))  # code only, not the comments


def upstreams() -> dict[str, str]:
    """handle matcher -> upstream, for each handle block."""
    return {m.group(1).strip(): m.group(2) for m in re.finditer(r"handle(?:_path)?\s*([^\{\n]*)\{[^}]*?reverse_proxy\s+(\S+)", body, re.S)} | {}


def test_all_routing_is_inside_one_route_block_so_order_is_the_written_order():
    """Regression for a review finding: separate handle blocks are sorted by matcher specificity, so the deny rule must not depend on source order of handle blocks."""
    open_at, close_at = body.index("route {"), body.rindex("\n\t}\n}")
    handles = [m.start(1) for m in re.finditer(r"^[ \t]*(handle)", body, re.M)]
    assert handles and all(open_at < h < close_at for h in handles), "every handle block must be inside the route block"
    assert body.index("handle @internal") == min(handles), "the deny rule is the first thing the route does"
    assert body.rstrip().endswith("}\n}") and re.search(r"\thandle \{\s*respond 404\s*\}\s*\}\s*\}\s*$", body), "the catch-all is last and answers 404"


def test_grafana_does_not_serve_its_own_metrics_even_if_a_route_were_wrong():
    import yaml
    compose = yaml.safe_load((Path(__file__).parents[1] / "deploy/compose/docker-compose.cloud.yml").read_text())
    assert compose["services"]["grafana"]["environment"]["GF_METRICS_ENABLED"] == "false"


def test_the_routes_are_exactly_the_designed_ones():
    assert re.findall(r"^\s*(handle(?:_path)?)\s+(\S+)?\s*\{", body, re.M) == [("handle", "@internal"), ("handle", "@keycloak"), ("handle", "/auth/*"), ("handle", "/grafana/*"), ("handle", "")]
    proxied = re.findall(r"reverse_proxy\s+(\S+)", body)
    assert sorted(proxied) == ["grafana:3000", "keycloak:8080"]


@pytest.mark.parametrize("internal", ["prometheus", "alertmanager", "loki", "postgres", "hapi", "mosquitto", "node-exporter", "cadvisor", "alloy", "9090", "5432", "8086", "3100"])
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
    assert body.index("handle @internal") < body.index("handle @keycloak")
    internal = matcher("internal")
    for path in ("/metrics", "/metrics/x", "/healthz", "/readyz", "/grafana/metrics", "/auth/healthz"):
        assert matches(internal, path), f"{path} must be blocked"
    for path in ("/", "/grafana/d/abc", "/auth/realms/ehg"):
        assert not matches(internal, path), f"{path} must stay reachable"


def test_only_the_ehg_realms_public_paths_of_keycloak_are_reachable():
    kc = matcher("keycloak")
    for ok in ("/auth/realms/ehg", "/auth/realms/ehg/protocol/openid-connect/auth", "/auth/realms/ehg/protocol/openid-connect/token", "/auth/resources/x1/login/keycloak/css/login.css"):
        assert matches(kc, ok), ok
    for blocked in ("/auth/admin/master/console/", "/auth/admin/realms/ehg/users", "/auth/realms/master/protocol/openid-connect/auth", "/auth/realms/ehg-evil/x", "/auth/health", "/auth/metrics", "/auth/"):
        assert not matches(kc, blocked), blocked
    assert re.search(r"handle /auth/\* \{\s*respond 404\s*\}", body) and body.index("handle @keycloak") < body.index("handle /auth/*")


@pytest.mark.parametrize("header", ["Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Permissions-Policy", "-Server"])
def test_every_response_gets_the_security_headers(header):
    assert re.search(rf"^\s*{re.escape(header)}\b", body, re.M)


def test_hsts_lasts_at_least_six_months():
    assert int(re.search(r"max-age=(\d+)", body).group(1)) >= 15_552_000


def test_bodies_are_size_limited():
    assert re.findall(r"max_size\s+(\S+)", body) == ["1MB"]


def test_tls_is_not_weakened_and_the_domain_comes_from_the_environment():
    for weak in ("tls internal", "skip_verify", "insecure", "auto_https off", "disable_redirects", "http://"):
        assert weak not in body, weak
    assert body.lstrip().startswith("{$EHG_DOMAIN} {"), "the one site block"


def test_the_missing_rate_limit_is_written_down_not_implied():
    assert "NO rate-limit directive" in text and "rate_limit" not in body


@pytest.mark.skipif(shutil.which("caddy") is None, reason="caddy not installed")
def test_caddy_validate_accepts_it():
    r = subprocess.run(["caddy", "validate", "--config", str(CADDYFILE), "--adapter", "caddyfile"], capture_output=True, text=True, env={**os.environ, "EHG_DOMAIN": "ehg.example.org"})
    assert r.returncode == 0, r.stderr


def test_no_content_security_policy_is_set_here_because_keycloak_and_grafana_send_their_own():
    """Keycloak's sign-in pages and Grafana need inline scripts and styles and send their own policy: a blanket header would replace it and break them."""
    assert "Content-Security-Policy" not in body and "import " not in body


def test_grafana_is_configured_for_the_sub_path_the_front_door_serves_it_on():
    env = yaml.safe_load((ROOT / "deploy/compose/docker-compose.cloud.yml").read_text())["services"]["grafana"]["environment"]
    assert env["GF_SERVER_SERVE_FROM_SUB_PATH"] == "true" and env["GF_SERVER_ROOT_URL"].endswith("/grafana/") and "${EHG_DOMAIN" in env["GF_SERVER_ROOT_URL"]
    assert "Keycloak single sign-on is NOT configured" in body or "NOT configured" in (ROOT / "deploy/caddy/Caddyfile").read_text(), "do not claim a Keycloak login for Grafana"

