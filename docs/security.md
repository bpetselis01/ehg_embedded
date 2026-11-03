# Security

**Intended purpose.** Research prototype for measuring wireless telemetry performance. Not for clinical decision-making. Not supplied to any person for patient care.
Makes no diagnostic or predictive claims. The replayed data are public, de-identified PhysioNet records.

**Status of this document (2026-11-03, after Unit 2).** It starts the threat model and the controls table of the enterprise-style evolution
([production-topology.md](production-topology.md)). Only the **gateway to broker** boundary is analysed so far, because it is the only one with new code.
Each later unit adds its boundary and updates the Status column. Nothing here says "secure" or "compliant": a control is *Implemented*, an *Optional flag*, *Planned* or a *Documented gap*.

## Controls

| Control | Where | Status |
|---|---|---|
| Wire CRC-16 + sequence number + RESEND | BLE link ([interface-spec.md](interface-spec.md) §3) | Implemented (existing) |
| MQTT over TLS 1.3 with client certificates, one identity per component, topic ACLs | `backend/mosquitto/mosquitto.tls.conf`, `acl`, `docker-compose.cloud.yml` | **Implemented as an optional cloud profile, tested against a real Mosquitto 2.1.2** (Unit 2). The default local stack stays anonymous on 1883 |
| Client side of the above | `gateway --mqtt-tls`; `MQTT_*` variables for ingest and the viewer | Implemented (Unit 2); half-set TLS configuration refuses to start instead of falling back to plain MQTT |
| Private CA bootstrap (root, intermediate, broker and client certificates, CRL) | `tools/pki.sh` | Implemented (Unit 2). Client certificates last 30 days by default |
| Short-lived (7-day) gateway certificates, CSR enrolment, revocation by an admin | Enrolment API and registry | Planned (Unit 7) |
| LESC passkey pairing, `_AUTHEN` GATT permissions | Firmware | Planned, optional flag (Unit 13) |
| MCUboot with our own ECDSA P-256 key | Firmware | Planned, optional; key kept outside the repo (Unit 13) |
| OIDC sign-in, TOTP multi-factor, roles, token validation | Keycloak and services | Planned (Units 7, 8) |
| Hash-chained audit log, FHIR `AuditEvent` | Triage service | Planned; will be tamper-evident, **not** write-once |
| Secrets kept out of git | `.gitignore` (`backend/pki/`, `*.key`, `*.pem`, `*.p12`) | Implemented for keys and certificates. SOPS with age for deployment secrets is Planned (Unit 4) |
| Encryption of the gateway outbox | Python: file mode 0600 and FileVault. iOS: Data Protection class | Planned (Units 3, 15); documented gap against SQLCipher |
| Encryption at rest on the cloud VM | Provider disk encryption; encrypted backups | Planned; partial |
| Container hardening, image scan, SBOM, signing | Compose, Trivy, Syft, cosign | Planned (Units 4, 14) |
| Readback protection (APPROTECT) and secure element on the board | nRF52840 DK | **Documented gap** (replay rig, not a product) |
| Regulatory framing | `docs/regulatory.md` | Planned (Unit 17); "designed with reference to" wording only |

## Threat model: gateway to broker (MQTT QoS 1, JSON on `ehg/<node>/pkt`)

| STRIDE | Threat | Control (Unit 2) | Residual risk |
|---|---|---|---|
| **S**poofing | Someone publishes as a gateway, or reads the stream, without being one | TLS 1.3 with `require_certificate`; the certificate CN becomes the MQTT user name **and the client id** (`use_username_as_clientid`); no anonymous access in the cloud profile | A stolen client key works until the certificate expires or is revoked and the broker reloads its CRL |
| **S**poofing | A fake broker captures gateway data | The client trusts only the private CA and checks the broker name against the certificate | A wrong `--host` at certificate creation fails closed (name mismatch) |
| **T**ampering | A gateway publishes packets for nodes it does not serve | ACL limits what each identity can publish | `ehg/+/pkt` lets a gateway publish for **any** node id (documented limit in the ACL file) |
| **T**ampering | Data altered in transit | TLS integrity; the application CRC-16 detects corruption before the gateway | Samples are not signed end to end; a compromised gateway can publish altered data |
| **R**epudiation | A publisher denies sending | One identity per component, visible in the broker log | The broker log is not tamper-evident (audit log planned, Unit 8) |
| **I**nformation disclosure | Packets read in transit or by an unauthorised reader | TLS 1.3; readers are listed in the ACL; no `#` grants | Public de-identified data only; real patient data must not be used (see below) |
| **D**enial of service | Flooding, an expired revocation list, or one identity taking over another's client id and session | Client id forced to the CN, so a takeover is impossible (reproduced and fixed, below); `max_queued_messages 100000` bounds the backlog | An **expired CRL refuses every client** (tested, below); no rate limiting at the broker; two processes sharing one certificate kick each other off, so each process needs its own certificate |
| **E**levation of privilege | A reader publishes, or a gateway writes another gateway's status | ACL; both cases tested against the real broker | The CA signing key controls every identity (see "Keys") |

## What was checked, and how

- **Mosquitto 2.1.2 (Homebrew, 2026-11-03) manual:** `tls_version` sets the *minimum* version, so `tlsv1.3` refuses TLS 1.2; `use_identity_as_username` takes the certificate CN; `crlfile` is a PEM revocation list.
  The manual also warns that any CA in `cafile` can issue certificates the broker will accept, so keep only this project's root and intermediate in `ca-chain.crt`.
- **Real broker tests** (`EHG_INTEGRATION=1 uv run pytest tests/test_mqtt_tls_config.py`, needs `brew install mosquitto`): the repo's real config and ACL, only paths and the port rewritten.
  A reader cannot publish; a gateway cannot write another gateway's status but can write its own; no client certificate, TLS 1.2 only and a certificate from another CA are all refused;
  `gateway --simulate 1 --mqtt-tls` publishes packets that the viewer identity reads; a refused certificate is reported by the gateway instead of failing silently.
- **Revocation:** after `tools/pki.sh revoke`, a **SIGHUP** makes the running broker refuse the revoked identity while others still connect (test). Done by hand once, before the signal, the revoked certificate still connected,
  so the CRL is picked up on reload or restart, not on its own. How an already-open session is treated at reload was **not** tested.
- **Expired CRL:** with the CRL checked, a list past its next-update date refuses every client until `tools/pki.sh crl` rewrites it (tested with Python's OpenSSL server context, which uses the same mechanism as the broker; not tested against Mosquitto itself).
  `tools/pki.sh` writes CRLs valid for 365 days; put a refresh in the calendar.
- **Client-id takeover (found by an automated security review, 2026-11-03):** without `use_username_as_clientid`, the viewer identity connected with the ingest service's client id and the broker dropped ingest and gave the viewer its session. Reproduced with a failing test, then fixed in `mosquitto.tls.conf`; the test now passes.
- **CA keys stay out of containers (same review):** the override mounts single certificate files, never `backend/pki`, which also holds `intermediate/ca.key` (and `root/root.key` until it is moved off). A test checks the merged compose file.
- **No Docker daemon needed:** all of the above ran without Docker running. `docker compose config` (which only reads files) confirms the merged cloud profile publishes only `127.0.0.1:8883`,
  mounts the TLS config, ACL and certificates read-only, and still has the same 4 services; the default stack still publishes 1883. The containers themselves were **not started** (Docker was not running).

## Known limit: a gateway can publish for any node id

`topic write ehg/+/pkt` lets an authenticated gateway publish packets labelled with another node's id. Pinning a gateway to its nodes needs either a gateway id in the topic or message (a change to the documented `ehg/<node>/pkt` interface that `eval/` and the recorded runs rely on) or a check against a registry of which gateway serves which node.
It is **not fixed in Unit 2** and is accepted for now: the planned registry (`node.assigned_gateway`, Units 6 and 7) and the triage worker's checks (Unit 8) are where it gets closed, and the pin in `docs/handoff.md` carries it forward. Until then, a compromised gateway certificate can forge data for any node.

## Not verified (carried forward)

- The `eclipse-mosquitto:2` image's exact version (the tests ran 2.1.2 from Homebrew), and whether the container's `mosquitto` user can read `backend/pki/broker.key` (mode 600). On Linux this may need a group-readable key or running the container with the host user. Check when Docker is available (Unit 5 on the VM).
- The WebSocket-over-TLS path on port 443, whether a carrier blocks 8883, and client certificates through a SNI-routing front door.
- Whether the single-file bind mount of `ca.crl` follows a rewrite by `tools/pki.sh crl` (it should, since OpenSSL rewrites the file in place, but a replaced file would leave the container with a stale list). Check with Docker.
- OpenSSL 3 is required by `tools/pki.sh`; macOS's own `/usr/bin/openssl` is LibreSSL and is refused with a message.

## Keys

- `backend/pki/` is gitignored, as are `*.key`, `*.pem` and `*.p12`. Private keys are created mode 600 and never printed.
- `tools/pki.sh init` tells you to move the **root key off this computer**; it is only needed to replace the intermediate. Losing it means re-issuing every certificate; copying it casually defeats the purpose.
- If a key is ever committed or pasted somewhere, treat its identity as compromised: revoke it, rewrite the CRL, reload the broker, and rotate.

## Data

Only the public, de-identified PhysioNet recordings used by this project belong on the cloud VM. Recording real people with the nRF52840 would make the data identifiable health information and
needs ethics approval and privacy obligations before it touches this chain; that is the line the prototype must not cross without approval ([decision 0010](decisions/0010-oracle-always-free-cloud-target.md)).
