"""Checks the Keycloak realm file (decision 0013): valid JSON, no secrets or users, the two remaining clients are configured the safe way (device grant for the phone, no implicit flow, secrets only as placeholders),
the roles exist, and TOTP is wired to the staff roles. Structural only: it was never imported into a real Keycloak."""

import json
import re
from pathlib import Path

import pytest

REALM = Path(__file__).parents[1] / "deploy/keycloak/realm-ehg.json"
realm = json.loads(REALM.read_text())
clients = {c["clientId"]: c for c in realm["clients"]}


def test_roles_and_realm_hardening():
    assert {r["name"] for r in realm["roles"]["realm"]} == {"viewer", "operator", "admin", "gateway"}
    assert realm["sslRequired"] == "all" and realm["registrationAllowed"] is False and realm["bruteForceProtected"] is True and realm["accessTokenLifespan"] <= 300


def test_the_phone_client_uses_the_device_grant_and_only_the_enrol_scope():  # plus basic (the `sub` claim) and roles (realm_access): the API refuses a token without them
    ios = clients["ios-gateway"]
    assert ios["attributes"]["oauth2.device.authorization.grant.enabled"] == "true" and not ios["standardFlowEnabled"] and not ios["implicitFlowEnabled"] and ios["defaultClientScopes"] == ["gateway:enrol", "basic", "roles"]


def test_service_clients_have_secrets_only_as_environment_placeholders():
    assert set(clients) == {"ios-gateway", "fhir-sync"}, "the triage and enrolment clients went with those services on 2026-11-11"
    assert re.fullmatch(r"\$\{[A-Z_]+_CLIENT_SECRET\}", clients["fhir-sync"]["secret"])
    assert clients["fhir-sync"]["serviceAccountsEnabled"] and not clients["fhir-sync"]["directAccessGrantsEnabled"]


def test_no_secret_user_or_credential_is_in_the_file():
    text = REALM.read_text()
    assert realm["users"] == [] and "credentials" not in text and "-----BEGIN" not in text
    for m in re.finditer(r'"(?:secret|password|clientSecret|value)"\s*:\s*"([^"]*)"', text):
        assert m.group(1).startswith("${"), f"literal secret-like value: {m.group(1)[:20]}"


def test_totp_is_required_for_staff_through_a_conditional_flow():
    assert realm["browserFlow"] == "browser-ehg"
    flows = {f["alias"]: f for f in realm["authenticationFlows"]}
    otp = [e["authenticator"] for e in flows["browser-ehg-otp-for-staff"]["authenticationExecutions"] if "authenticator" in e]
    assert otp == ["conditional-user-role", "auth-otp-form"]
    assert {e["requirement"] for e in flows["browser-ehg-otp-for-staff"]["authenticationExecutions"]} == {"REQUIRED"}
    assert any(e.get("requirement") == "CONDITIONAL" for e in flows["browser-ehg-forms"]["authenticationExecutions"])
    assert realm["authenticatorConfig"][0]["config"]["condUserRole"] == "operator" and realm["otpPolicyType"] == "totp"


def test_every_role_above_viewer_gets_totp_either_directly_or_through_operator():
    """The OTP condition matches ONE role (operator). A review found that an admin without `operator` would skip TOTP, so admin must contain operator."""
    cond = realm["authenticatorConfig"][0]["config"]["condUserRole"]
    roles = {r["name"]: r for r in realm["roles"]["realm"]}
    for name in ("operator", "admin"):
        covered = name == cond or cond in roles[name].get("composites", {}).get("realm", [])
        assert covered and (name == cond or roles[name]["composite"] is True), f"{name} would be able to log in without TOTP"
    assert "operator" in roles["admin"]["composites"]["realm"] and "viewer" in roles["operator"]["composites"]["realm"]
    assert not roles["gateway"].get("composite"), "the phone role carries no staff rights"


@pytest.mark.parametrize("client", ["ios-gateway", "fhir-sync"])
def test_no_client_allows_the_password_grant(client):
    assert not clients[client].get("directAccessGrantsEnabled", False)


def test_builtin_scopes_that_the_apis_need_are_in_the_file():
    """Importing a realm with its own clientScopes list creates NO built-in scopes (shown on a real Keycloak 26.8.1, 2026-11-04): without these,
    tokens had no `sub`, no `preferred_username` and no realm_access."""
    scopes = {s["name"]: s for s in realm["clientScopes"]}
    assert {"basic", "roles", "profile", "email", "web-origins", "acr", "gateway:enrol"} <= set(scopes)
    basic = {m["protocolMapper"]: m for m in scopes["basic"]["protocolMappers"]}
    assert "oidc-sub-mapper" in basic and basic["oidc-sub-mapper"]["config"]["access.token.claim"] == "true"
    realm_roles = next(m for m in scopes["roles"]["protocolMappers"] if m["protocolMapper"] == "oidc-usermodel-realm-role-mapper")
    assert realm_roles["config"]["claim.name"] == "realm_access.roles" and realm_roles["config"]["access.token.claim"] == "true"
    username = next(m for m in scopes["profile"]["protocolMappers"] if m["name"] == "username")
    assert username["config"]["claim.name"] == "preferred_username"
    assert set(realm["defaultDefaultClientScopes"]) >= {"basic", "roles", "profile"}


def test_the_phone_client_carries_its_user_name_because_that_is_the_gateway_id():
    """The token's preferred_username is the gateway id (the certificate's CN). Without this mapper the id silently became the user's UUID
    (found by enrolling through a real Keycloak and step-ca, 2026-11-04, with the enrolment service that was later removed). The client is not given the whole `profile` scope, only this claim."""
    mappers = {m["name"]: m for m in clients["ios-gateway"]["protocolMappers"]}
    m = mappers["gateway-id-from-username"]
    assert m["config"]["claim.name"] == "preferred_username" and m["config"]["user.attribute"] == "username" and m["config"]["access.token.claim"] == "true"
    assert "profile" not in clients["ios-gateway"]["defaultClientScopes"]
