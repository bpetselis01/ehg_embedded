# deploy/keycloak: the identity realm as code

[`realm-ehg.json`](realm-ehg.json) is imported when Keycloak starts (`kc.sh start --import-realm`, file mounted into `/opt/keycloak/data/import/`). It holds **no secrets and no users**: the three confidential client secrets are
`${...}` placeholders that Keycloak fills from environment variables (`TRIAGE_API_CLIENT_SECRET`, `ENROLL_API_CLIENT_SECRET`, `FHIR_SYNC_CLIENT_SECRET`), and so is the public host name (`EHG_DOMAIN`). Users are created by hand in the admin console.

What it sets up ([decision 0013](../../docs/decisions/0013-keycloak-and-certificate-lifecycle.md)): roles `viewer`, `operator`, `admin`, `gateway`; client `triage-ui` (public, authorisation code with PKCE S256, no implicit flow, no password grant);
client `ios-gateway` (public, OAuth2 Device Authorization Grant, scope `gateway:enrol`); resource servers `triage-api` and `enroll-api`; `fhir-sync` (client credentials); brute-force protection; a browser login flow that requires TOTP for users with the `operator` role.

**Not verified (no Keycloak was available):** that Keycloak 26.8.1 accepts this file as written, in particular the conditional-OTP flow (the `conditional-user-role` condition matches ONE role, `operator`; `admin` is therefore a COMPOSITE role that includes `operator` (and `operator` includes `viewer`), so TOTP covers admins too, as a security review pointed out; whether Keycloak's condition follows composites is still to be confirmed on a real server),
the placeholder substitution, and the device-grant attribute name. `tests/test_realm_export.py` checks only the structure and that no secret is present. Compare with an export from a running Keycloak before relying on it, and record the differences in decision 0013.

## Check right after the FIRST import (an independent review raised a doubt I could not settle offline)

The realm file lists its own `clientScopes` (only `gateway:enrol`) and its own `authenticationFlows`. As I remember Keycloak's importer, it creates the built-in client scopes (`basic`, `roles`, `profile`, `email`, ...) and the built-in flows (browser, direct grant, client authentication) **only when those lists are absent**, so with these lists present they may be missing. If so, access tokens would carry no `sub` and no `realm_access.roles`, and every call to the triage API would be refused after a successful sign-in; the `fhir-sync` client-credentials login could also break.
Not verified, so check it once: sign in as a test user, decode the access token (for example at jwt.io is NOT acceptable for a real token: use `python -c "import jwt,sys;print(jwt.decode(sys.argv[1],options={'verify_signature':False}))" TOKEN`) and confirm it has `sub`, `aud` containing `triage-api`, and `realm_access.roles`; open Authentication > Flows in the admin console (SSH tunnel) and confirm the built-in flows exist. If they do not, add the missing scopes and flows to `realm-ehg.json` (export them from a throw-away realm) and record the difference in decision 0013.

