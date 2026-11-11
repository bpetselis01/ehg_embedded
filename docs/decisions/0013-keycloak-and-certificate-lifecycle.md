# 0013. Keycloak for people and services, step-ca for device certificates

**Superseded 11 Nov 2026.** The enrolment API was removed; the `step` CLI with step-ca covers this. See the [reuse ledger](../licences.md#reuse-ledger). The record below is kept as written.

**Date:** 2026-11-03

## Status

Accepted (Checkpoint 1, 2026-11-03). Code written in Unit 7 on 2026-11-03 (realm file, enrolment API, CSR vetting, step-ca client, CLI); tested with a fake CA that issues real certificates, NOT against a real step-ca or Keycloak. The findings the prompt tells Unit 7 to record (step-ca templates, revocation/CRL, direct Keycloak-token use) are STILL to be checked and added here.

## Context

The system needs sign-in with multi-factor authentication and roles for people, tokens for services, and a machine identity for each gateway.
Keycloak issues tokens, not X.509 client certificates, but the broker's credential is a certificate ([decision 0008](0008-mqtt-mtls-and-topic-acls.md)).

## Decision

- **Keycloak** in production mode on the shared Postgres, realm `ehg` defined as code (`deploy/keycloak/realm-ehg.json`, no secrets in it; client secrets from the environment),
  version 26.8.1 or later. Roles `viewer`, `operator`, `admin`, plus `gateway`; TOTP required for operator and admin.
- Clients: `triage-ui` (authorisation code with PKCE, no implicit flow), `ios-gateway` (Device Authorization Grant, RFC 8628), and confidential resource-server clients for the triage API,
  fhir-sync and the enrolment API. Services validate signature, issuer, audience, expiry and roles; expired, wrong-audience, wrong-issuer, `alg=none` and tampered tokens are rejected.
- `AUTH_MODE=dev` (fixed users, loud banner, 127.0.0.1 only) lets everything run and be tested without Keycloak.
- **Smallstep step-ca** is the private CA (not hand-written signing code). A small enrolment API checks the Keycloak token, the role and the registry (not revoked), then asks step-ca to sign a CSR.
  The key pair is generated on the device. The certificate lasts **7 days**, the CN comes from the registry (the caller cannot choose it), EKU is client authentication only.
- Revocation: an admin revokes in the registry, the API regenerates a CRL, Mosquitto reloads it (**unverified**; if it does not reload live, document the restart procedure). Short lifetimes bound the exposure.
- The root CA key stays offline on Byron's Mac; the intermediate key lives on the VM (or OCI Vault, optional). Secrets in git use SOPS with age, never plaintext.

## Consequences

- Keycloak's memory limit (about 1.36 GB) is a large share of the free VM; the budget is checked at Checkpoint 6.
- Seven-day certificates match the seven-day free Apple signing window and force a working renewal path.
- Hash-chained audit rows relay authentication events; this is tamper-evident, not write-once ([decision 0011](0011-triage-service-pipeline-health-not-clinical.md)).
- Whether step-ca can consume Keycloak tokens directly (a simpler route) is open and decided in Unit 7.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §5d, §7 (identity design, certificate lifecycle), §9 Unit 7. Keycloak, step-ca and Mosquitto behaviour is from the prompt and unverified until Unit 7.
