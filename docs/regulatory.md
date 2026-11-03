# Regulatory and engineering pack

**Intended purpose.** Research prototype for measuring wireless telemetry performance. Not for clinical decision-making. Not supplied to any person for patient care. Makes no diagnostic or predictive claims.

**Read this first.** Nothing here is a compliance claim. The standards below are cited as *"designed with reference to"* and only for the engineering habits they describe (a hazard list, a component list, a security life cycle). No conformity assessment, audit, certification or legal advice was involved, the prototype is not a medical device,
and Byron is a student, not a regulatory affairs professional. The statements about the Therapeutic Goods Administration (TGA), the Privacy Act and the NSW health privacy law come from the planning prompt (§8), which records them as checked on 3 November 2026 from secondary sources.
**I did not re-check them against the primary texts**; read the primary text before quoting any of them. Edition and date details of the standards were not verified.

## 1. What the system is, and is not

| It is | It is not |
|---|---|
| A research rig that replays public, de-identified PhysioNet electrohysterogram (EHG) recordings through a development board, a gateway, a cloud store and an operator page, and measures delivery, latency and bit-exactness (hypotheses H1 to H4) | A wearable, a sensor with an analogue front end, a clinical device or a service for any patient |
| A demonstration of enterprise-style controls (mutual TLS, sign-in with roles, audit chain, consent gate, backups with a tested restore) on **public** data | Evidence that those controls would pass a clinical or privacy review |
| A transport, storage and pipeline-health layer | An analyser. No contraction analysis, classifier, risk score, alert about a person or prediction exists anywhere (the page and the API are tested for clinical wording) |

**Position on the TGA, as recorded in the prompt (not re-verified):** the clinical-decision-support exclusion does not cover software that processes a signal from another medical device, and the wellness exclusion does not cover monitoring of a serious condition. This prototype therefore **claims no exclusion**, makes no clinical claim,
and says plainly that a *clinical* version of an analysis layer would probably be a regulated medical device. Whether a transport-and-storage-only layer could use a communications or workflow exclusion is **unverified** and needs the full exclusion text.

**Data.** Only the public records may be used on the cloud VM. If Byron ever records real volunteers, the data become identifiable health information: ethics (HREC) approval, the Privacy Act and NSW HRIP Act duties (including cross-border disclosure, which is why the home region is Sydney) and security duties then apply to the whole chain.
**That is the line this prototype must not cross without ethics approval.** The "consent" in the registry is a data-model demonstration of an enforced rule, not a consent management system.

## 2. Software description (in the manner of IEC 62304's software description; no safety class is assigned)

| Item | Role | Code | Runs on |
|---|---|---|---|
| Node firmware | Replays a recording as packets over Bluetooth, keeps 256 packets for re-send | `firmware/ehg_node/` | nRF52840 DK |
| Python gateway | Decodes and checks packets, finds gaps and asks for re-sends, publishes over MQTT through a durable outbox | `gateway/` | Mac |
| Swift wire library | Third copy of the packet format (the phone app itself is not written) | `ios/EHGWire/` | tested on the Mac only |
| Broker and bridge | Mutual-TLS MQTT in; durable stream out; acknowledges only after the stream has stored the message | `backend/mosquitto/`, `backend/bridge/` | VM (never started) |
| Writers | Store each packet exactly once in PostgreSQL/TimescaleDB (and InfluxDB, the reference) | `backend/ts_writer/`, `backend/ingest/` | VM / Mac |
| Triage service | Pipeline-health rules, alert workflow with notes, audit chain, registry, API, page | `backend/triage/`, `viewer/triage.*` | VM (never started) |
| Identity and enrolment | Roles and sign-in; short-lived gateway certificates | `deploy/keycloak/`, `backend/enroll/`, `deploy/step-ca/` | VM (never started) |
| FHIR sync and facade | Copies windows to a reference FHIR server; read-only, consent-checked access | `backend/fhir_sync/`, `backend/triage/fhir_api.py` | VM (never started) |
| Operations | Rules, dashboard, backups, restore drill, infrastructure as code | `deploy/` | VM (never started) |

## 3. Components from outside (SOUP) and their licences

The full register, with what was and was not read, is [licences.md](licences.md). The ones that shape decisions: psycopg is LGPL-3.0 (used unmodified); TimescaleDB Community is under the Tiger Data licence, not OSI-approved (self-hosting for own use only);
Terraform is under the Business Source Licence; Loki and Grafana are AGPL-3.0 (unmodified, own use); the nRF Connect SDK `nrf` repository is under Nordic's 5-clause licence, not Apache-2.0. Many entries are still marked UNVERIFIED there, and so is every container image's licence text.
Versions are pinned (and images by digest); `docs/supply-chain.md` says what scanning exists and what does not (**no SBOM has been generated, no vulnerability scan run, no image signed**).

## 4. Hazard analysis (in the manner of ISO 14971; qualitative, no risk acceptance criteria)

"Harm" here means **a wrong conclusion about the pipeline or a measurement that a thesis would rely on**, since there is no patient. If the system were ever used with real data the harms would change and this table would have to be redone.

| # | Hazard | Possible consequence | Controls in the design | Verified by | Residual risk / gap |
|---|---|---|---|---|---|
| H-1 | A node stops sending and nobody notices (missed alert) | Gaps taken for a quiet node | `NODE_OFFLINE` rule with hysteresis; the monitor falls back to a liveness-only view if a snapshot fails; "no telemetry for 15 min" alert; silence is an alert (`absent()`) | `test_triage_rules`, `test_triage_stream`, `test_observability_rules` | The alert path to a phone is not wired (Alertmanager receiver is a stub); never run |
| H-2 | Stale or incomplete history shown as current | Trends read as complete | Trend answers say when the data do not span the range; the page shows the update time; unknown values are NaN, never zero | `test_triage_api`, `test_triage_metrics`, `test_triage_page` | Page not tried against real data |
| H-3 | A packet stored twice or dropped | Wrong delivery numbers (H2) | Message id `node:seq:t_inject` in the stream; `ON CONFLICT DO NOTHING` in the stores; MQTT acknowledged only after the stream acknowledges; unreadable messages kept under `EHG.BAD.*` | `test_bridge`, `test_ts_writer`, `test_outbox` | Parity with InfluxDB not yet run on a real server (needs Docker) |
| H-4 | Samples altered in transit or storage | Wrong signal, broken bit-exactness (H4) | CRC-16 on every packet; samples are raw integers end to end; three decoders share golden vectors; a FHIR export keeps raw counts | `test_packet`, `test_ios_wire_format` (and `swift test`), `test_fhir_export`, `test_fhir_sync` | The firmware C copy is checked against the Python constants only |
| H-5 | Data attributed to the wrong node or subject | Wrong person's recording shown or exported | Node id checked against the topic; ids restricted to a safe character set; the consent rule is checked on every read and export | `test_bridge`, `test_triage_api`, `test_fhir_facade`, `test_fhir_sync` | A gateway can still publish under any node id (documented gap) |
| H-6 | Clock error between device, gateway and cloud | Wrong latency | Clock map and drift correction in the gateway; the triage latency is labelled an approximation; the authoritative figure is `eval/latency.py` | `test_gateway`, `test_latency`, `test_triage_stream` | Cross-host offset to the VM is not measured (needs the VM) |
| H-7 | Broker, bus or database outage | Data loss | Durable gateway outbox; persistent MQTT session; the stream holds messages while the database is down; backups with a tested restore | `test_outbox`, `test_ts_writer`, `test_backup_scripts` | No failure drill has been run; the stream itself is not backed up; one VM, no high availability |
| H-8 | Unauthorised access or tampering | Disclosure of data; altered alert history | Signed tokens with explicit algorithms, role checks, mutual-TLS gateways with 7-day certificates and revocation; hash-chained audit log; strict CSP and front-door rules | `test_jwtauth`, `test_triage_api`, `test_enroll`, `test_triage_audit`, `test_caddy_config`, `test_triage_page` | Never run against Keycloak, step-ca or Caddy; the revocation list reaches the broker only through a cron job (`crl-sync.sh`) that has never run on the VM; the audit log is tamper-evident, not write-once |
| H-9 | A backup that cannot be restored | Loss after a failure | Checksums, a restore drill that refuses to overwrite, and a freshness alert | `test_backup_scripts`, `test_observability_rules` | TimescaleDB, restic and the VM are untested |
| H-10 | Real patient data put on the free cloud account | Privacy breach | The intended-purpose statement everywhere; only public records are loaded; this document | `test_triage_page` (wording), `test_docs` | A rule that depends on the operator |

## 5. Requirements and where they are tested

| Requirement | Tests |
|---|---|
| R-1 Packets are decoded bit-exactly and damaged ones refused, on every platform | `test_packet`, `test_ios_wire_format` |
| R-2 Loss is found and recovered; what cannot be recovered is counted | `test_tracking`, `test_gateway`, `test_integrity` |
| R-3 A broker outage loses nothing the gateway had | `test_outbox`, `test_mqtt_tls_config` |
| R-4 Each packet is stored once, atomically, and failures are visible | `test_bridge`, `test_ts_writer`, `test_db_migrations`, `test_backend_client`, `test_parity` |
| R-5 Pipeline-health alerts open and clear with hysteresis and need a note to close | `test_triage_rules`, `test_triage_state`, `test_triage_stream` |
| R-6 Every action by a person is recorded in a chain that shows tampering | `test_triage_audit`, `test_triage_api` |
| R-7 Only the right role can do each thing; nothing about a node without active consent is shown or exported | `test_jwtauth`, `test_triage_api`, `test_fhir_facade`, `test_fhir_sync` |
| R-8 Gateways hold short-lived certificates that can be revoked | `test_enroll`, `test_realm_export` |
| R-9 The deployment files stay safe and consistent | `test_deploy_assets`, `test_platform_compose`, `test_caddy_config`, `test_ci_config` |
| R-10 Service levels are defined, and a lost signal is itself an alert | `test_observability_rules`, `test_triage_metrics` |
| R-11 Backups restore and the restore is proven | `test_backup_scripts` |
| R-12 The interface documents match the code | `test_api_docs`, `test_docs`, `test_handoff` |

`tests/test_regulatory_doc.py` checks that every test file named in this section exists.

## 6. Security life cycle (in the manner of IEC 81001-5-1)

- **Threat model and controls:** [security.md](security.md) (a section per boundary and per automated review round, each finding with its test).
- **Components list:** `uv.lock` pins every Python package; images are pinned by digest; Dependabot is configured (it has not run, nothing has been pushed). **A software bill of materials has not been produced:** `docs/supply-chain.md` has the command.
- **Signed artefacts:** none. Firmware uses MCUboot with Nordic's **development** key; replacing it is Unit 13 and needs the board.
- **Vulnerability handling:** an automated review ran after every unit; each finding was fixed with a test or recorded as a documented gap. There is no disclosure address, no patch SLA and no monitored advisory feed.
- **Secrets:** none in the repository (tests check the realm file, the compose files and the examples); the real ones are SOPS-encrypted with a key Byron holds.

## 7. What can credibly be claimed, and what cannot

**Can:** designed with reference to the standards above; reads and writes FHIR R5 Observation (SampledData) and Device resources with a reference FHIR server (written, tested against a fake server only); demonstrates mutual TLS, role-based sign-in, an audit chain, a consent gate and backups with a tested restore, on public data.

**Cannot:** any exemption from or compliance with TGA rules; clinical safety; validated alerting; high availability, disaster recovery objectives or an SLA; compliance with the Australian Privacy Principles or the NSW HRIP Act for real data; HL7 AU Core conformance; that anything running in a cloud has actually been started.
