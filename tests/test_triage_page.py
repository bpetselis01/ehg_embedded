"""Checks the triage page (viewer/triage.html, .css, .js) and how the API serves it. Static checks only: no browser ran this page in the tests (see docs/progress.md for what was looked at by hand).
They enforce the rules that make the page safe under the front door's Content-Security-Policy, that no token is stored, that server text is never treated as HTML, and that no clinical wording appears."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.jwtauth import Authenticator
from backend.triage.app import PURPOSE, UI_DIR, create_app

FILES = {n: (UI_DIR / n).read_text() for n in ("triage.html", "triage.css", "triage.js")}
ALL = "\n".join(FILES.values())


def test_no_inline_script_or_style_and_no_external_resources():
    html = FILES["triage.html"]
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and not re.search(r"\sstyle\s*=", html) and "<style" not in html, "the CSP allows only same-origin files"
    assert not re.search(r"\bon[a-z]+\s*=", html), "no inline event handlers"
    assert not re.search(r"(?:src|href)\s*=\s*[\"']?(?:https?:)?//", html) and "@import" not in FILES["triage.css"] and "url(" not in FILES["triage.css"]
    assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", FILES["triage.js"]), "the script talks to its own origin only (the one URL is the SVG namespace)"


def test_script_never_builds_html_from_text_or_runs_strings_as_code():
    js = FILES["triage.js"]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout(\"", "setInterval(\""):
        assert banned not in js, banned
    assert not re.search(r'setAttribute\(\s*["\'](?:style|on)', js), "no style or event attributes set from script"


def test_no_token_or_secret_is_stored_in_the_browser():
    js = re.sub(r"/\*.*?\*/", "", FILES["triage.js"], flags=re.S)  # the header comment may say what the page does NOT use
    assert "localStorage" not in js and "document.cookie" not in js and "indexedDB" not in js
    keys = set(re.findall(r"sessionStorage\.(?:set|get|remove)Item\(\s*[\"']([^\"']+)", js))
    assert keys == {"ehg.pkce"}, "sessionStorage holds only the sign-in verifier and state"
    assert "access_token" in js and "refresh_token" not in js


def test_sign_in_is_the_code_flow_with_s256_pkce_and_checks_state():
    js = FILES["triage.js"]
    assert 'response_type: "code"' in js and 'code_challenge_method: "S256"' in js and "code_verifier" in js and "params.get(\"state\") !== saved.state" in js
    assert "response_type=token" not in js and "grant_type: \"password\"" not in js


def test_no_clinical_or_diagnostic_wording():
    """The page reports pipeline health only (decision 0011)."""
    banned = r"diagnos|prognos|preterm|pre-term|labou?r|contraction|pregnan|gestation|obstetric|risk of|patient|treat(?:ment)?\b|\bpredict"
    assert not re.search(banned, ALL, re.I), re.search(banned, ALL, re.I).group(0)


def test_page_is_accessible_by_construction():
    html = FILES["triage.html"]
    assert 'lang="en"' in html and 'name="viewport"' in html and "<title>" in html
    assert 'role="alert"' in html and 'aria-live="polite"' in html and 'scope="col"' in html and 'role="tablist"' in html
    assert "prefers-color-scheme: dark" in FILES["triage.css"] and "focus-visible" in FILES["triage.css"]
    assert "▲ Critical" in FILES["triage.js"], "status is a word and a symbol, not colour alone"


def test_every_api_route_the_script_calls_exists_in_the_api(tmp_path):
    """Keeps the page and the API from drifting apart."""
    app = create_app(store=None, audit=None, auth=Authenticator("dev"), registry=None, fhir_upstream=object())
    paths = set(app.openapi()["paths"])
    called = set(re.findall(r"[\"'`](/(?:api|fhir)/[A-Za-z]+)", FILES["triage.js"]))
    assert len(called) >= 5, called
    templates = {re.sub(r"\{[^}]*\}", "{}", p) for p in paths}
    for c in called:
        assert any(t == c or t.startswith(c + "/") for t in templates), f"the page calls {c}, which the API does not serve"


@pytest.fixture
def client():
    return TestClient(create_app(store=None, audit=None, auth=Authenticator("dev"), registry=None))


def test_the_three_files_are_served_without_login_with_the_right_types(client):
    for path, kind in (("/", "text/html"), ("/triage.js", "text/javascript"), ("/triage.css", "text/css")):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(kind) and r.headers["x-content-type-options"] == "nosniff", path
    assert client.get("/viewer/index.html").status_code == 404 and client.get("/triage.py").status_code == 404 and client.get("/../backend/jwtauth.py").status_code == 404, "only the three files, no directory serving"


def test_ui_config_in_dev_mode_lists_the_fixed_users_and_the_banner(client):
    j = client.get("/ui-config.json").json()
    assert j["mode"] == "dev" and "DEV MODE" in j["banner"] and j["purpose"] == PURPOSE and {u["role"] for u in j["dev_users"]} == {"viewer", "operator", "admin"}


def test_ui_config_in_oidc_mode_has_no_dev_users_and_no_secret():
    class V:  # a verifier stand-in: only its presence matters here
        pass
    app = create_app(store=None, audit=None, auth=Authenticator("oidc", V()), registry=None, ui_oidc={"authority": "https://id.example.org/realms/ehg", "client_id": "triage-ui"})
    j = TestClient(app).get("/ui-config.json").json()
    assert j["mode"] == "oidc" and "dev_users" not in j and j["banner"] is None and j["oidc"]["client_id"] == "triage-ui"
    assert not re.search(r"secret|password|token", str(j), re.I)
