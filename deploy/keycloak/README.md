# deploy/keycloak: the identity realm as code

[`realm-ehg.json`](realm-ehg.json) is imported when Keycloak starts (`kc.sh start --import-realm`, file mounted into `/opt/keycloak/data/import/`). It holds **no secrets and no users**: the three confidential client secrets are
`${...}` placeholders that Keycloak fills from environment variables (`TRIAGE_API_CLIENT_SECRET`, `ENROLL_API_CLIENT_SECRET`, `FHIR_SYNC_CLIENT_SECRET`), and so is the public host name (`EHG_DOMAIN`). Users are created by hand in the admin console.

What it sets up ([decision 0013](../../docs/decisions/0013-keycloak-and-certificate-lifecycle.md)): roles `viewer`, `operator`, `admin`, `gateway`; client `triage-ui` (public, authorisation code with PKCE S256, no implicit flow, no password grant);
client `ios-gateway` (public, OAuth2 Device Authorization Grant, scope `gateway:enrol`); resource servers `triage-api` and `enroll-api`; `fhir-sync` (client credentials); brute-force protection; a browser login flow that requires TOTP for users with the `operator` role.

**Not verified (no Keycloak was available):** that Keycloak 26.8.1 accepts this file as written, in particular the conditional-OTP flow (the `conditional-user-role` condition matches ONE role, `operator`; `admin` is therefore a COMPOSITE role that includes `operator` (and `operator` includes `viewer`), so TOTP covers admins too, as a security review pointed out; whether Keycloak's condition follows composites is still to be confirmed on a real server),
the placeholder substitution, and the device-grant attribute name. `tests/test_realm_export.py` checks only the structure and that no secret is present. Compare with an export from a running Keycloak before relying on it, and record the differences in decision 0013.
