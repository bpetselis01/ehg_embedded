# 0014. HAPI FHIR (R5) as a self-hosted reference EHR

**Date:** 2026-11-03

## Status

Accepted (Checkpoint 1, 2026-11-03; status of the build is stated in each record). Implemented by Unit 9, which first verifies R5 behaviour on the pinned HAPI version.

## Context

[`tools/fhir_export.py`](../../tools/fhir_export.py) already emits FHIR **R5** `Observation` with `valueSampledData` (`interval` and `intervalUnit` are R5 fields; R4 used `period`).
An enterprise chain ends in an EHR. No hospital EHR is available, and mock clinical notes or medication lists would be fabricated records.

## Decision

- Run a self-hosted **HAPI FHIR JPA server** (Apache-2.0, Postgres backend, `fhir_version: R5`, JVM capped) as the **reference EHR**. It is not a hospital EHR and no HL7 v2 or vendor integration is claimed.
- A fhir-sync worker (JetStream consumer) writes `Patient` (pseudonymous research subject), `Device`, `Observation` (one `SampledData` window per 60 s; raw integer counts, missing frames as `E`),
  `Flag` for open pipeline alerts, `Provenance` and `AuditEvent`. Writes use conditional create or update on a business identifier, so redelivery is idempotent.
  `tools.fhir_export.bundle()` is reused with its signature unchanged.
- HAPI ships **no security**. It listens on the docker network only; the front door exposes `/fhir` after a Keycloak JWT check. The triage service adds a read-only facade with consent checks.
  This is a custom resource-server layer, not SMART-on-FHIR.
- Before building, confirm R5 `Observation` with `SampledData`, conditional create, `Flag`, `Provenance` and `AuditEvent` work and are searchable on the pinned version.
  If R5 is blocked, stop and ask Byron about an R4 fallback behind an adapter; never downgrade silently.
- Keep the local code system `urn:thesisb:ehg`; no LOINC or SNOMED codes are invented. `fhir.resources==8.3.0` checks structure only, which is not conformance.
- **AU Core is not claimed** (it is R4-based). The `Consent` resource is modelled and enforced in the facade as a data-model demonstration, not a legal consent platform.

## Consequences

- A second JVM service (about 2.3 GB limit) shares the VM with Keycloak; HAPI cold start is expected to take minutes (unverified).
- The EHR panel shows real stored data, so the demo cannot show clinical context that does not exist.
- FHIR structure is tested in CI; conformance beyond that is only claimed for what the optional HL7 validator run actually covers.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §2, §4b, §6 (FHIR), §9 Unit 9; [`tools/fhir_export.py`](../../tools/fhir_export.py). HAPI R5 support is unverified until Unit 9.
