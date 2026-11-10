# deploy/keycloak: the identity realm as code

[`realm-ehg.json`](realm-ehg.json) is imported when Keycloak starts (`kc.sh start --import-realm`, file mounted into `/opt/keycloak/data/import/`). It holds **no secrets and no users**: the three confidential client secrets are
`${...}` placeholders that Keycloak fills from environment variables (`TRIAGE_API_CLIENT_SECRET`, `ENROLL_API_CLIENT_SECRET`, `FHIR_SYNC_CLIENT_SECRET`), and so is the public host name (`EHG_DOMAIN`). Users are created by hand in the admin console.

What it sets up ([decision 0013](../../docs/decisions/0013-keycloak-and-certificate-lifecycle.md)): roles `viewer`, `operator`, `admin`, `gateway`; client `triage-ui` (public, authorisation code with PKCE S256, no implicit flow, no password grant);
client `ios-gateway` (public, OAuth2 Device Authorization Grant, scope `gateway:enrol`); resource servers `triage-api` and `enroll-api`; `fhir-sync` (client credentials); brute-force protection; a browser login flow that requires TOTP for users with the `operator` role.

**Not verified (no Keycloak was available):** that Keycloak 26.8.1 accepts this file as written, in particular the conditional-OTP flow (the `conditional-user-role` condition matches ONE role, `operator`; `admin` is therefore a COMPOSITE role that includes `operator` (and `operator` includes `viewer`), so TOTP covers admins too, as a security review pointed out; whether Keycloak's condition follows composites is still to be confirmed on a real server),
the placeholder substitution, and the device-grant attribute name. `tests/test_realm_export.py` checks only the structure and that no secret is present. Compare with an export from a running Keycloak before relying on it, and record the differences in decision 0013.

## First import: CHECKED on a real Keycloak 26.8.1 (2026-11-04), and the doubt was right

A realm file that lists its own `clientScopes` does **not** get Keycloak's built-in scopes. Imported as it was, the realm had only `gateway:enrol` and `offline_access`, and a token for a user had **no `sub`, no `preferred_username` and no `realm_access`**: `backend/jwtauth.py` requires `sub`, so every real login would have been refused. The built-in flows (`browser`, `direct grant`, ...) were present, so only the scopes were missing.
Fixed: `realm-ehg.json` now carries the built-in `basic`, `roles`, `profile`, `email`, `web-origins` and `acr` scopes (exported from a fresh realm of the same Keycloak version, so they are exactly Keycloak's own), lists them as realm defaults, and gives `ios-gateway` only `gateway:enrol`, `basic` and `roles`. Re-imported into a fresh Keycloak: the example tokens carry `sub`, `preferred_username`, `aud` (`triage-api` / `enroll-api`) and `realm_access.roles`, and a real signed token from the password grant on a throwaway copy was accepted by `JwtVerifier` with the right role and refused for the wrong audience.
After a Keycloak upgrade, repeat the check: start a throwaway Keycloak with `start-dev --import-realm` (see the commands in docs/progress.md, 2026-11-04) and compare the scopes of a fresh realm with this file.
