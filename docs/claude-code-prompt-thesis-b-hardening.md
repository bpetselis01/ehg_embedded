# Claude Code prompt: evolve the Thesis B EHG pipeline from prototype topology to an enterprise-style remote-monitoring platform (free services only)

You are working in Byron Petselis's UNSW honours thesis repo (BIOM4951, Thesis B): an open, measured, reproducible wireless telemetry pipeline for electrohysterography (EHG).

**Mission.** Take the system from its *prototype topology* (a laptop gateway, an anonymous local broker, one InfluxDB, Grafana) to an *enterprise-style remote patient monitoring (RPM) architecture*: **Sensor Node → BLE → Mobile Gateway → Cellular/MQTT → Cloud Core → EHR/Dashboard**. Build every hop for real wherever a free service allows it. Where something cannot be real on free tiers (multi-region high availability, an actual LTE-M modem, WORM storage), write an honest design document and say so; never imply it exists. The validated results H1–H4 must not be disturbed.

**The only hard constraint is that every service is free.** Time is not the constraint: Byron has 12+ weeks (Thesis B is due 30 April 2027). Prefer the more enterprise-grade option when it costs only effort. Do **full implementations**, not stubs or mock-ups.

> **IMPORTANT: session-limit protocol.** Claude has a 5-hour usage window. When you reach about **95 % of it**, stop starting new work, bring everything to a safe state, commit, and write the pin in `docs/handoff.md` exactly as in section 10b, so that you (or a fresh session) can resume from the exact step after the limit resets. **At the start of every session, read `docs/handoff.md` first if it exists.**

Work in phases. Stop at each **CHECKPOINT** and ask Byron (AskUserQuestion) before continuing. Never batch phases.

---

## 0. Read this first, in this order

0. **`docs/handoff.md`, if it exists.** It is the pin from the previous session; follow section 10b to resume.
1. `README.md` (layout, commands) and `docs/progress.md` (status, blockers, recorded results).
2. `docs/codebase-guide.md` (how it fits together, file index) and `docs/interface-spec.md` (wire format, MQTT JSON, InfluxDB schema, FHIR export).
3. `docs/decisions/0001`–`0006`, especially `0004-end-to-end-loss-recovery.md` and `0006-local-mosquitto-influx27-grafana.md`.
4. `docs/research/nrf-ble-environment.md` (verified Kconfig/API notes) and `docs/firmware.md`.
5. The code you will touch: `gateway/gateway.py`, `gateway/packet.py`, `gateway/tracking.py`, `backend/ingest/ingest.py`, `backend/docker-compose.yml`, `backend/mosquitto/mosquitto.conf`, `viewer/server.py`, `tools/fhir_export.py`, `eval/latency.py`, `eval/integrity.py`, `eval/backend_client.py`, `tests/test_docs.py`, `tests/test_gateway.py`, `tests/test_fhir_export.py`, `pyproject.toml`, `.github/workflows/tests.yml`.

If anything in this prompt disagrees with the repo, **the repo wins**. Say so and adapt; do not "fix" the repo to match this prompt.

---

## 1. Repo invariants (breaking any of these is a failure)

These come from the repo's own `CLAUDE.md`, its tests and CI. `CLAUDE.md` is gitignored, so they are repeated here.

1. **Wire format v2 does not change.** Today it has two copies, `firmware/ehg_node/src/ehg_packet.h` and `gateway/packet.py`, plus `docs/interface-spec.md`; `tests/test_docs.py` fails if they disagree. This work adds a **third copy** (the Swift decoder in the iOS app, Unit 15). It must be covered by golden test vectors and a drift test, and `docs/interface-spec.md` must say "change all copies together".
2. **Samples stay raw int16 ADC units end to end** (bit-exactness is hypotheses H2/H4). No filtering, scaling or synthetic signals on the live path. Do **not** replace the replayed EHG with synthetic ECG/HR/SpO2/BP.
3. **The validated results come from InfluxDB 2.7 + Flux** (decision 0006). It stays as the **reference store for the local stack**. The cloud platform adds PostgreSQL/TimescaleDB as the enterprise store. Migration is only allowed with a **parity proof** (Unit 6): identical row sets, bit-exact samples, identical `eval/` results from either store. Never delete or rewrite the Influx path or the recorded H1–H4 numbers.
4. **The default local experience must be unchanged.** `cd backend && docker compose up -d` must still start exactly the same 4 containers with the same ports. `uv run python -m gateway.gateway [--simulate N]` must behave exactly as today unless a new flag is passed. Every new capability is behind a flag, a compose profile or an override file.
5. **No AI attribution in commits.** CI (`.github/workflows/tests.yml`) fails if any commit message contains `Co-Authored-By: ...claude/anthropic` or `Generated with [Claude`. Override your default commit trailer. Byron is the sole author.
6. **Firmware must be built via `firmware/build.sh`.** NCS sysbuild breaks on this folder's spaces, so the script uses the `~/thesisb` symlink, and `west` is not on PATH (the script shows the `nrfutil toolchain-manager launch` invocation).
7. **Hardware:** nRF52840 DK serial 1050208294; UART `/dev/cu.usbmodem0010502082941` at 460800 baud. Only one process can hold the port (VS Code's nRF Terminal included).
8. **Docker on the Mac:** Grafana is on port **3300** (VS Code holds 3000–3003). Docker Desktop takes minutes to start containers. **Never restart Docker Desktop**: another project (Supabase) runs in it. Avoid Supabase's ports. New local ports: 8883 (MQTTS), 8780 (triage API).
9. **Nordic docs return 403 to fetches.** Grep `~/ncs/v3.4.1` for Kconfig symbols and APIs. Verify every Zephyr/NCS symbol named below by grep before using it.
10. **Secrets never enter git.** Private keys, passwords, tokens, role maps, Terraform state/variables, databases and `.env` files are gitignored. Add the patterns (`backend/pki/`, `*.key`, `*.pem`, `*.p12`, `gateway/outbox*.db`, `deploy/**/.env*`, `deploy/**/*.tfstate*`, `deploy/**/terraform.tfvars`, `deploy/**/secrets/`, `ios/**/Secrets.xcconfig`) to `.gitignore` in the same unit that creates them.
11. **Free services only.** No paid service. No service that needs a card unless Byron explicitly agrees at the relevant gate (Oracle sign-up needs one for verification; ask first). Source-available licences (BSL, Tiger Data licence) are acceptable only if recorded in the licence register (Unit 1).
12. **You never create, modify or query cloud resources and never ask for cloud credentials.** You produce code, IaC, runbooks and tests; Byron runs `terraform apply`, the update script and the like.
13. **UK English** in prose and docs.
14. **Session-limit protocol (section 10b) is mandatory.** Never leave the repo, Docker, a database migration, a firmware flash or a measurement run in a half-finished state when the usage window runs out.

### Repo conventions the tests enforce

- Every code file starts with the header block used throughout the repo (**PURPOSE / INPUT / OUTPUT / HOW / ROLE IN THESIS / BEFORE → AFTER**, with concrete `e.g.` examples) and every function has a one-line docstring. Copy the style of `gateway/gateway.py`. Apply the same discipline to Swift files, shell scripts, Terraform and cloud-init (comment header).
- Every new `.py`/`.sh` under `tools/ eval/ gateway/ viewer/ backend/ firmware/` must have its **file name** in the file index of `docs/codebase-guide.md` (`test_file_index_complete`). Extend that test's directory list to `deploy/` and `ios/` (Swift) in the unit that creates them.
- Every CLI uses `argparse.ArgumentParser(description=__doc__, ...)` (`test_cli_help_is_module_docstring`).
- Every Markdown link must resolve; every `docs/...md` path mentioned in code must exist.
- Every ADR in `docs/decisions/NNNN-*.md` has `## Status`, `## Context`, `## Decision`, `## Consequences` and a **Sources** line.
- Dependencies are added with `uv add ...` and **`uv.lock` is committed**, because CI runs `uv sync --frozen`. `uv run pytest -q` must pass on a fresh checkout **without** Docker, hardware, network, Xcode or `data/raw`. Tests that need those skip themselves (`EHG_INTEGRATION=1` opts in).
- New decisions get an ADR; status goes in `docs/progress.md` (dated entry, newest first); each unit that adds a user-visible command also updates `README.md` and `docs/troubleshooting.md`.

---

## 2. The original research versus this repo (verified), and what changed since

The earlier research describes a generic cardiac RPM stack and a prototype that is "laptop → USB → dongle → BLE → laptop → local dashboard". This repo is more advanced and different. Each row is a claim and the decision for this work. Rows marked **(revised)** were rejected in the first version of this plan and are now in scope because Byron asked for a full enterprise build.

| Research claim | Verdict | Decision |
|---|---|---|
| Repo has `/sensor`, `/gateway`, `/backend`, `/frontend`; prototype is a USB-injected BLE loop | **Wrong.** Real layout: `firmware/ehg_node` (Zephyr), `gateway/` (Bleak → MQTT QoS 1), `backend/` (Mosquitto, ingest, InfluxDB 2.7, Grafana), `viewer/`, `eval/`, `tools/` | Keep. Add `deploy/` (IaC) and `ios/` (native gateway). |
| Sensor should emit ECG, HR, SpO2, BP | **Not applicable.** The node replays public **EHG** (TPEHG 3 ch × 20 Hz; Icelandic 16 ch × 200 Hz stretch) | Keep EHG. Alert on pipeline/signal health, not vitals. SpO2<90 / HR>120 / SBP>140 are dropped. |
| TimescaleDB for storage **(revised)** | Now in scope: the enterprise store. Must not break `eval/backend_client.py` (Flux), Grafana provisioning, decision 0006 or the recorded results | Add PostgreSQL + TimescaleDB (Community edition, for compression and continuous aggregates). InfluxDB stays as the local reference store; **dual-write with a parity proof** (Unit 6). |
| Node.js/Next.js frontend, `fhirTransformer.ts` | Adds a second toolchain the repo and CI do not have | Python (FastAPI) services + vanilla-JS page in `viewer/` (Byron's choice). FHIR is Python. |
| FHIR "STU3 / R4" | Repo already emits **FHIR R5** SampledData (`interval`/`intervalUnit` are R5 fields; R4 had `period`) | Stay on R5 end to end. HAPI is configured `fhir_version: R5` (supported setting; **verify** SampledData and Observation search work on the pinned version, Unit 9). AU Core is R4-based, so state "no AU Core conformance claimed". |
| AWS IoT Core as broker | **Not free long-term**: 12-month tier or $200 / 6-month credits. One node sends ~172,800 messages/day, so 500,000 lasts ~3 days | Rejected. Self-hosted Mosquitto + NATS. |
| Enterprise broker cluster / Kafka **(revised)** | EMQX 5.9+ is BSL (single node only free); Redpanda is BSL; Kafka KRaft is too heavy next to two JVMs on 12 GB | **Mosquitto at the edge + NATS JetStream as the internal bus** (at-least-once, durable consumers, replay). Clustering/HA is a design document only. |
| "Store-and-forward SQLite queue" in the gateway | **Real gap.** In-memory unbounded paho queue only | Build it for both gateways (Unit 3 Python, Unit 15 Swift). |
| mTLS 1.3, X.509 device certs | **Real gap.** `mosquitto.conf` is anonymous plain 1883 | Build it, plus a certificate lifecycle (enrolment, short-lived certs, revocation) in Unit 7. |
| BLE LESC passkey pairing | **Real gap.** No SMP/pairing in `prj.conf` | Build it behind a Kconfig option (Unit 13). Needs the DK. |
| FOTA signed ECDSA P-256 | Partly present: MCUboot + MCUmgr/SMP exist, but `sysbuild.conf` uses MCUboot's **public development key** | Own signing key (Unit 13) kept out of the repo; OTA via the iOS app is an optional unit (Unit 15d). Fleet rollout is design-only. |
| BrakTooth mitigation | **Not applicable.** BrakTooth targets Bluetooth Classic; the nRF52840 here is BLE-only. SweynTooth was vendor-SDK-specific and patched | Mention in `docs/security.md` as "considered, not applicable / patched". Do not claim mitigation work. |
| "Hardware AES-CCM via CryptoCell-310" for BLE | Imprecise. Link-layer encryption is done by the controller using the radio's CCM peripheral | State it accurately in `docs/security.md`. |
| HIPAA / GDPR / FDA SaMD / 21 CFR Part 11 compliance | Wrong jurisdiction for a Sydney project; data are public, de-identified PhysioNet records | Australian framing (section 8). US/EU only as comparators. Never claim "compliant". |
| Mock EHR notes and medication lists in the clinician UI | Fabricated clinical records | Dropped. The context panel shows real data from the **self-hosted HAPI FHIR server** (Device, Observation, Flag, Provenance, AuditEvent). |
| WORM audit logs | Not achievable on free tiers (needs object-lock storage) | Hash-chained, append-only, tamper-**evident** log in Postgres, mirrored to FHIR `AuditEvent` and shipped to Loki. Documented as not WORM. |
| OIDC + MFA identity federation **(revised)** | Now in scope | **Keycloak** (self-hosted, production mode) with TOTP MFA, roles, PKCE for the UI, Device Authorization Grant for the iPhone, client credentials for services. Replaces the oauth2-proxy/GitHub idea. |
| SQLCipher for the gateway queue | Awkward wheels on macOS; not needed for a thesis rig | Python gateway: plain SQLite (WAL) mode 0600 + FileVault. iOS gateway: SQLite file with iOS Data Protection class `completeUntilFirstUserAuthentication`. Documented gap versus SQLCipher. |
| Enterprise "worklist ranks patients by severity" | Good idea, wrong signal source | Rank **nodes** by a transparent severity score from pipeline/signal health (section 6). |
| Multi-region HA / DR | Not possible on one free VM | **Design document + a tested backup/restore drill** (Unit 12). State RPO/RTO as measured. |

---

## 3. Decisions Byron has already made (do not re-ask)

1. **Scope: a full enterprise-style evolution**, not a light hardening. All planned components are real implementations: HAPI FHIR server with EHR write-back; Keycloak identity provider; PostgreSQL/TimescaleDB with a NATS stream stage; infrastructure-as-code plus observability; a **native Swift/CoreBluetooth iPhone gateway**.
2. **Cloud: Oracle Cloud Always Free only** (one 2 OCPU / 12 GB arm64 VM). Azure/AWS/Google are not used. A cross-cloud DR drill (restoring onto an Azure free VM) is recorded as **future work** only.
3. **UI: vanilla JS in `viewer/`** (no build step, no CDN, no framework).
4. **Alerts rank on pipeline and signal health only** (delivery, loss, latency, node offline, flatline, saturation). **No contraction index. No classifier hook.** Zicheng Shang owns the analogue front end and classification; do not drift into his lane, and make no diagnostic or predictive claims anywhere.
5. **Mobile gateway: iPhone + Mac with Xcode, free Apple account** (Personal Team). Swift + CoreBluetooth. Consequence to document everywhere: the app signature expires after **7 days** and must be re-installed from Xcode; no TestFlight or App Store. Treat the iOS gateway as a research/bench gateway.
6. **Tooling preference: industry-standard software that is free to use** (section 5d). Infrastructure is **Terraform + cloud-init + Docker Compose** (no Ansible, no OpenTofu unless the Terraform licence becomes a problem).
7. **Time:** 12+ weeks. Thesis B submission target **30 April 2027**. H3 (≥ 8 boards) and the 6 h soak are not yet measured; licence is "pending" (leave it alone).

Byron has **not** yet created an Oracle account, has no domain, and has Xcode on the Mac. Plan setup steps accordingly (Unit 5 runbook).

---

## 4. Target architecture (today → target)

```
 nRF52840 node ──BLE (LESC, wire v2+CRC, RESEND)──▶ iPhone gateway (Swift)  ──cellular──┐
                                                    Mac gateway (Python, reference) ─────┤
                                                                                         ▼
                                       Mosquitto  :8883 mTLS (+ :443 WSS fallback)  [Oracle A1 VM, Caddy front door]
                                                         │ bridge worker (at-least-once)
                                                         ▼
                                            NATS JetStream stream EHG.PKT
                       ┌─────────────┬──────────────┼───────────────┬───────────────────┐
                       ▼             ▼              ▼               ▼                   ▼
                 ts-writer      influx-writer   triage worker   fhir-sync worker   (replay / new consumers)
                 (Timescale)    (reference,     (rules, alerts)  (HAPI write-back)
                       │         optional)            │               │
                       ▼                              ▼               ▼
              PostgreSQL 16 + TimescaleDB ◀── alerts, audit, registry   HAPI FHIR R5 (EHR)
                       │                              │               ▲
                       └────────── REST + live FHIR facade ───────────┘
                                         │
              Caddy (TLS) ── Keycloak (OIDC, MFA, roles) ── triage page / Grafana
              Prometheus · Loki · Grafana · exporters · OCI Monitoring (out-of-band)
```

| Tier | Today | Target (this work) |
|---|---|---|
| **1 Sensor node** | nRF52840 DK, Zephyr; EHG replay; BLE GATT notify; MCUboot + SMP OTA with dev key | Same, plus optional LESC passkey pairing + `_AUTHEN` GATT permissions and own MCUboot key (Unit 13) |
| **2 Gateways** | `gateway/gateway.py` (Bleak, CRC, gap tracker, RESEND, clock map, in-memory MQTT queue) | Python gateway + mTLS + SQLite outbox + LWT status (Units 2, 3). **Native iOS gateway** with the same behaviour (Unit 15) |
| **3 Cloud core** | Mosquitto anonymous 1883, `ingest.py` → InfluxDB 2.7, Grafana | mTLS Mosquitto + ACLs, NATS JetStream, TimescaleDB, device registry + certificate enrolment, Keycloak, triage service, HAPI FHIR + write-back, observability stack, IaC, backups (Units 4–12) |
| **4 Clinician/operator UI** | Grafana + `viewer/` raw WebSocket traces | Triage page (severity worklist, sparklines, alert workflow, FHIR/EHR panel) behind Keycloak SSO (Units 10, 11) |

Data-flow boundaries (keep this table in `docs/production-topology.md`):

| Boundary | Protocol | Authn | Confidentiality | Failure behaviour |
|---|---|---|---|---|
| Node → gateway | BLE GATT notify, wire v2 + CRC-16, RESEND | Optional LESC passkey bond (Unit 13) | Optional link-layer AES-CCM | Board keeps 256-packet history; gaps re-asked up to 5× |
| Gateway → broker | MQTT QoS 1 JSON `ehg/<node>/pkt` | mTLS client cert (CN = gateway id, short-lived, from Unit 7 enrolment) | TLS ≥ 1.3, port 8883; WSS/443 fallback | Durable outbox; backoff 1 s → 60 s with jitter |
| Broker → bus | MQTT QoS 1 persistent session → JetStream | mTLS (service identity) | docker network + TLS | Bridge worker acks only after JetStream ack |
| Bus → stores/services | JetStream durable pull consumers, explicit ack | NATS credentials (nkeys or token) | docker-internal | Redelivery; idempotent writes keyed `(node, seq, t_inject)` |
| Services → Postgres / HAPI | SQL / FHIR REST | DB roles; OAuth2 client-credentials JWT | docker-internal | Degrade visibly, never silently |
| Browser/iPhone → UI/API | HTTPS via Caddy | Keycloak OIDC (PKCE, TOTP MFA) | Let's Encrypt cert | Read-only for `viewer` role |

### 4b. The target chain, hop by hop

Reproduce this table in `docs/production-topology.md` and the README. Never describe a "stand-in" or "design-only" hop as if it were done.

| # | Production hop | Realised as | Status after this work |
|---|---|---|---|
| 1 | Sensor node | nRF52840 **DK** replaying public EHG. Not a wearable; no analogue front end (Zicheng's scope) | Existing + pairing/signing (Unit 13) |
| 2 | BLE link | Zephyr GATT notify, wire v2 + CRC + RESEND | Existing + LESC flag |
| 3 | Mobile gateway | **Native Swift iOS app** (Unit 15) with Python/Mac gateway as the reference | Built; free-account signing expires weekly; background behaviour measured not assumed |
| 4 | Cellular + MQTT | iPhone cellular data, MQTT over TLS 1.3 mTLS (or WSS/443 fallback) | **Phone cellular measured (H1-cellular)**. Real LTE-M/NB-IoT modem **not built** (hardware + SIM, not free) |
| 5 | Cloud core | Oracle A1 VM: Mosquitto, NATS, TimescaleDB, triage, HAPI, Keycloak, observability | Built (Units 4–12) |
| 6a | Clinician dashboard | Triage page + Grafana + raw viewer | Built |
| 6b | **EHR interoperability** | Self-hosted **HAPI FHIR R5** server receiving Device/Patient(pseudonymous)/Observation(SampledData)/Flag/Provenance/AuditEvent from the fhir-sync worker; triage page reads it back | Built. It is a *reference EHR* (HAPI), not a hospital EHR; no HL7v2/vendor integration |

Design-only (write each as an ADR/section with prerequisites, never as done): multi-AZ/multi-region HA and broker clustering; cross-cloud DR on an Azure free VM; real LTE-M/NB-IoT; App Store/TestFlight distribution (needs the paid Apple programme); fleet-wide staged OTA rollout; WORM audit storage; AU Core conformance.

---

## 5. Free-service budget (verified 3 Nov 2026; **re-verify before deploying**)

| Service | Verified allowance | Verdict |
|---|---|---|
| **Oracle Always Free, Ampere A1** | **2 OCPU / 12 GB** arm64 (1,500 OCPU-h + 9,000 GB-h/month; halved from 4/24 in June 2026 without announcement); 200 GB block storage (boot + block; min boot 47–50 GB); 10 TB/month egress; 20 GB object storage + 50k requests/month; Resource Manager (Terraform) 100 stacks; Monitoring and Notifications free; Vault software keys + 150 secrets; 5 CAs/150 certificates; home region only. Sign-up needs a **phone number and a credit card** (verification; prepaid/virtual rejected) | **Chosen.** No SLA; Oracle may change or reclaim. Idle rule: reclaimed only if p95 CPU, network **and** memory (A1) are all < 20% over 7 days. This stack holds ~8 GB resident (> 20% of 12 GB), so it should not be classed idle. Do **not** add fake load. Stay Always-Free-only (no PAYG). Everything must be rebuildable from IaC + backups |
| Oracle AMD micro (2 × 1/8 OCPU, 1 GB) | Free, 50 Mbps | Optional: external uptime probe / backup mirror (Unit 12). Not required |
| **Self-hosted on the A1 VM** | No usage caps | Mosquitto 2.1, NATS 2.15 JetStream, PostgreSQL 16 + TimescaleDB (Community/TSL edition), HAPI FHIR JPA (Apache 2.0), Keycloak 26.8.1+ (Apache 2.0), Prometheus, Loki, Grafana OSS, Caddy, Alloy |
| EMQX | 5.9+ is BSL 1.1 (single node free; clustering paid) | Not used. Mention as the clustered-broker design option |
| Redpanda / Kafka | BSL (trial-gated features) / too heavy | Not used |
| AWS IoT Core, Tiger Cloud, InfluxDB Cloud Free, Grafana Cloud Free, HiveMQ Cloud | Time/credit-limited, tiny caps, or unconfirmed | Rejected (see Appendix B) |
| Hostname/HTTPS | **Caddy** + Let's Encrypt (HTTP-01 needs a DNS name → VM, ports 80/443). **DuckDNS** free names (status not fully verified; flagged by some security vendors as phishing-abused, a reachability risk). Cloudflare free **cannot carry raw MQTT/TLS on 8883** and terminates TLS (so no client-cert passthrough). Tailscale Personal: free, non-commercial, good for admin access only | Byron has no domain. Ask at Unit 5 (default: DuckDNS + Caddy HTTP-01; fallback: a free dynamic-DNS alternative; Tailscale for admin SSH/Grafana). MQTT is exposed on **8883 direct** on the OCI public IP. Always provide a **WSS on 443** path, because some mobile networks block non-standard ports (carrier behaviour **unverified**) |
| Apple Personal Team (free) | 3 apps/device, 3 devices, 10 App IDs, **7-day** profiles; no TestFlight/App Store/push; Background Modes believed allowed (verify) | Chosen for the iOS gateway as a bench gateway |
| CI/CD | GitHub Actions: unlimited minutes on public repos; private repos 2,000 min/month (macOS runners count 10×, Windows 2×) and 500 MB shared storage; no payment method = blocked, not billed. CodeQL/GHCR private features **unverified** | Ask Byron whether the repo may be public (assessment/IP). If private, drop CodeQL and keep Trivy/Syft/cosign locally or in Actions |
| Terraform | The Terraform CLI is under the Business Source License: free for own use (not for offering a competing service). HCP Terraform Free: 500 managed resources, 1 concurrent run. OCI Resource Manager (managed Terraform) is Always Free | Use **Terraform** (industry-standard IaC) with the `oracle/oci` provider; state in **OCI Object Storage** (S3-compatible backend) or OCI Resource Manager. Record the BSL in the licence register. OpenTofu is a drop-in fallback if the licence is ever a problem |
| Alert notifications | OCI Notifications (1M HTTPS/month), `ntfy.sh`, Telegram bot | Out-of-band "VM down" alert via OCI Monitoring; no sensitive content in messages |

### 5b. RAM and CPU budget on the 2 OCPU / 12 GB arm64 VM (engineering estimates; **measure and adjust**)

| Service | Cap | Budget |
|---|---|---|
| Ubuntu 24.04 + Docker + journald | — | 600 MB |
| Caddy | — | 60 MB |
| Mosquitto 2.1 | persistence on | 60 MB |
| NATS JetStream | `max_mem_store 256MB`, `GOMEMLIMIT 384MiB`, file storage | 400 MB |
| Bridge, ts-writer, triage worker, fhir-sync (Python or Go, small) | per-process limits | 500 MB |
| PostgreSQL 16 + TimescaleDB (hosts `ehg`, `hapi`, `keycloak` databases) | `shared_buffers 1GB`, container limit 2 GB | 2,000 MB |
| HAPI FHIR JPA | `-Xmx1536m`, limit 2.3 GB | 2,300 MB |
| Keycloak (production mode) | limit 1.36 GB (vendor sizing guide) | 1,360 MB |
| step-ca (private CA) | — | 100 MB |
| Prometheus (15 d) / Alertmanager / Loki (7 d) / Grafana / Alloy + exporters | retention caps | 1,250 MB |
| Backup/restore jobs (transient) | — | 250 MB |
| **Total** | | **≈ 8.9 GB, headroom ≈ 3.1 GB** |

- **InfluxDB is not in the cloud budget** (it adds 0.5–1 GB). It remains in the local stack as the reference store; the influx-writer consumer is optional in the cloud profile and only used for the parity proof (Unit 6).
- CPU is the tighter limit. Stagger Timescale compression and backup jobs; expect JVM cold starts of 1–2 minutes (**unverified**). Cap every JVM and NATS memory store explicitly (NATS defaults to 75% of system RAM).
- Data volume: one node at 3 ch × 20 Hz ≈ 2.3 KB/s; the Icelandic 16 ch × 200 Hz stretch ≈ 47 KB/s before compression. Timescale compression plus retention policies protect the 200 GB disk. Set retention (default 90 d raw) and document growth.
- Record real `docker stats` numbers in `docs/progress.md` after Unit 5 and Unit 12.

### 5d. Industry-standard choices (the rule: pick the de-facto standard that is free; deviate only with a recorded reason)

| Need | Choice | Why it is the standard | Deviation / note |
|---|---|---|---|
| Infrastructure as code | **Terraform** (+ `oracle/oci` provider), `tflint`, `trivy config` | Most widely used IaC tool | BSL licence recorded; OpenTofu fallback |
| First-boot configuration | **cloud-init** + Docker Compose | Standard on every cloud image | Ansible dropped: single VM, no drift management |
| Secrets in git | **SOPS + age** | Common GitOps pattern for encrypted secrets | HashiCorp Vault not used (BSL, heavy); OCI Vault optional for the CA key |
| Identity provider | **Keycloak** (OIDC/OAuth2, PKCE, TOTP) | Leading open-source IdP | — |
| Private CA | **Smallstep step-ca** | Widely used open-source private CA | Replaces custom signing code; EJBCA/Vault PKI too heavy |
| EHR / FHIR server | **HAPI FHIR** | Reference open-source FHIR server | No built-in security; R5 verified in Unit 9 |
| Message broker (edge) | **Mosquitto** (Eclipse) | Most deployed open-source MQTT broker | EMQX (BSL) / HiveMQ are the clustered enterprise options: design-only |
| Internal bus | **NATS JetStream** | CNCF project, light, at-least-once | **Kafka** is the industry standard for streaming but needs two JVMs' worth of RAM; Redpanda (Kafka API) is BSL. Documented as the scale-up path (ADR 0012) |
| Database | **PostgreSQL 16 + TimescaleDB** | PostgreSQL is the standard; Timescale is the standard Postgres time-series extension | Community (TSL) licence recorded |
| Migrations | **Alembic** | Standard for SQLAlchemy/Python | — |
| Postgres backup/PITR | **pgBackRest** | The standard Postgres backup tool | `restic` for non-Postgres data |
| Reverse proxy / TLS | **Caddy** (automatic ACME) | Simplest standards-compliant ACME client | If SNI-passthrough of MQTT on 443 is needed, use **nginx `stream`** (standard) rather than a Caddy plugin |
| Metrics / logs / dashboards | **Prometheus + Alertmanager + Loki + Grafana**, collectors via **Grafana Alloy** (OpenTelemetry Collector distribution) | CNCF/industry standard stack | Traces (Tempo/OTel) design-only |
| API contracts | **OpenAPI** (generated by FastAPI) for REST, **AsyncAPI** for MQTT topics and payloads | Industry specs for HTTP and event APIs | Specs live in `docs/api/`; tests assert they parse and match the code |
| Architecture docs | **C4-style diagrams in Mermaid** (render on GitHub) + ADRs | Common documentation practice | — |
| Supply chain | **Syft** (SBOM, SPDX/CycloneDX), **Trivy**, **cosign (Sigstore)**, **Dependabot**, **gitleaks** | Standard open-source tooling | CodeQL only if the repo is public |
| Security checklists | **OWASP ASVS (level 1–2 subset)** and **OWASP API Security Top 10** mapped in `docs/security.md` | Standard application-security baselines | Cite only the items you actually checked |
| Reliability practice | **SLIs/SLOs and error budgets** (SRE practice), blameless post-incident notes for each failure drill | Industry standard | No SLA claimed |
| Healthcare standards | **HL7 FHIR R5**, **OAuth2/OIDC**, IEC 62304 / ISO 14971 / IEC 81001-5-1 (reference only) | The relevant standards | AU Core not claimed |

### 5c. Licence register (Unit 1 writes `docs/licences.md`)

List every component with licence, edition and why it is acceptable: Mosquitto (EPL/EDL), NATS (Apache-2.0), PostgreSQL (PostgreSQL licence), **TimescaleDB Community edition (Tiger Data Licence, not OSI; compression and continuous aggregates require it; self-hosting for own use is allowed, offering it as a service is not; check with `SHOW timescaledb.license;`)**, HAPI FHIR (Apache-2.0), Keycloak (Apache-2.0), Prometheus/Loki/Grafana (Apache-2.0/AGPL-3.0, check), Caddy (Apache-2.0), **Terraform CLI (BSL 1.1, free for own use; record the version, pin ≥ 1.6 only after reading the licence text)**, step-ca / Smallstep (Apache-2.0), Alertmanager (Apache-2.0), pgBackRest (MIT), SOPS (MPL-2.0) and age (BSD-3), Alembic/SQLAlchemy (MIT), CocoaMQTT (MIT), nRF Connect SDK / MCUboot (Apache-2.0), EMQX 5.9+ (BSL, not used). Mark any licence you cannot confirm from the repository itself as **UNVERIFIED**. Pin every image by version (and digest where practical).

---

## 6. Alert, severity, audit, device-registry and FHIR specification (used by Units 7, 8, 9, 10, 11)

**Principle:** alerts are *operational indicators about the pipeline and the signal path*. They are not clinical alerts. The authoritative H1/H2/H4 numbers still come from `eval/latency.py` and `eval/integrity.py`.

### Rules (all thresholds are config with these defaults)

| ID | Condition | Default severity |
|---|---|---|
| `NODE_OFFLINE` | No packet from a node for > max(5 s, 3 × packet interval). Packet interval = `n_frames / fs_hz` (0.5 s at 20 Hz × 10 frames) | Critical |
| `GATEWAY_OFFLINE` | Retained status `offline` (LWT) or heartbeat missing, so node silence is attributed to the gateway | Critical |
| `LOSS_UNRECOVERED` | A missing `seq` still absent after `MAX_TRIES × RETRY_S` (+ margin) in `gateway/gateway.py` (5 × 1 s) | Critical |
| `LOSS_RATE` | Rolling 60 s fraction of missing packets > 1 % (the H2 bound is > 99 % delivery) | Warning |
| `LATENCY_P95` | Rolling 60 s p95 of (triage-worker receive time − `t_inject_host`) > 2000 ms (H1 bound); > 5000 ms = Critical. Excludes `replayed` packets. An operational approximation only; disabled until the clock offset is configured when ingest runs on another host | Warning / Critical |
| `FLATLINE` | A channel has zero variance over the last 10 s (`10 × fs` frames) | Warning (all channels = Critical) |
| `SATURATION` | ≥ 5 % of samples in the last 10 s at the int16 (or 24-bit when `flags & 2`) rails | Warning |
| `SEQ_RESTART` | Gap tracker `streams` increments (board rebooted / numbering restarted) | Warning |

Anti-fatigue: a rule fires only after its condition holds for 3 consecutive evaluations (1 Hz) and clears after 10 consecutive healthy ones (hysteresis). One open alert per (node, rule). Reuse `gateway.tracking.GapTracker` for sequence logic if it fits (the service image is built with the repo root as context); otherwise write a minimal tracker and say why in the file header. Do not silently duplicate logic.

### Severity score and worklist

- Node score = max over its open alerts of weight (Critical 100, Warning 50) + 5 per additional distinct open rule (cap 20) + min(10, minutes the oldest unacknowledged alert has been open ÷ 6). No open alerts = **Stable (0)**.
- Band: score ≥ 100 **Critical**, 50–99 **Warning**, 0 **Stable**.
- Worklist order: score descending, then oldest unacknowledged alert first, then node id. The ordering function is pure and unit-tested; the UI only renders it.

### Alert lifecycle and audit

- States: `open → acknowledged → escalated → resolved`. A note is **required** to escalate or resolve. If a condition clears while `open` or `acknowledged`, record `condition_cleared_at` and auto-resolve with actor `system`; an `escalated` alert never auto-resolves.
- Every read of an alert detail, every transition, note, config change, export and **authentication event relayed from Keycloak** writes an **audit row**.
- Storage: PostgreSQL schema `triage` (`alerts`, `alert_events`, `audit_log`). `audit_log(id, ts, actor, role, action, target, detail_json, prev_hash, hash)` with `hash = SHA-256(prev_hash || canonical_json(row without hash))`. The application DB role has INSERT/SELECT only on `audit_log`, and triggers `RAISE EXCEPTION` on UPDATE/DELETE/TRUNCATE. A `verify` command (`python -m backend.triage.audit --verify`) recomputes the chain and exits non-zero on tampering. Each audit row is also (a) emitted as a FHIR `AuditEvent` to HAPI and (b) logged as JSON to stdout for Loki. Document honestly: **tamper-evident, not WORM**; someone with database superuser rights could rewrite the chain, so also ship the latest chain head hash to the off-VM backup (Unit 12) to detect that.
- Health history: the triage worker writes `ehg_health` (a Timescale hypertable; fields `delivery_pct`, `p95_latency_ms`, `rx`, `missing`, `unrecovered`, `flatline_channels`, `saturated_pct`, `score`) once a minute per node. A continuous aggregate provides hourly and daily rollups for the 7-day sparklines and the 24 h / 7 d / 30 d / 90 d ranges. If data do not span a range, the UI says so.

### Time-series schema (TimescaleDB; created by migrations in Unit 6)

- Hypertable `ehg_raw(time timestamptz, node text, ch int, seq int, v smallint/int, …)` mirroring the Influx measurement field-for-field (see `docs/interface-spec.md`), primary key `(node, seq, ch, time)` so replays are idempotent via `ON CONFLICT DO NOTHING`; `ehg_pkt` likewise. Compression after 1 day, retention 90 days (config). Use **Alembic** (the standard Python migration tool) with hand-written SQL operations for the Timescale-specific parts (hypertables, compression, continuous aggregates), in `backend/db/migrations/`, applied by `backend/db/migrate.py` and tested up/down against a throwaway Postgres container (skipped without Docker).
- Samples stay raw integers. Never store converted units in these tables.

### Device registry and patient/subject model (Postgres schema `registry`)

- `gateway(id, kind[python|ios], cert_serial, enrolled_at, revoked_at, last_seen, app_version)`, `node(id, fw_version, record, fs_hz, channels, assigned_gateway, last_seen)`, `subject(id, pseudonym, source_dataset, consent_status)`. A `subject` is a **pseudonymous research subject label** for a PhysioNet record (for example `tpehg1007`); no names, dates of birth or demographics. Mark the FHIR `Patient` resource with a `meta.tag` of `urn:thesisb:synthetic-pseudonymous` and a text note.
- FHIR `Consent` is modelled and stored; the FHIR facade and triage API **refuse reads for a subject without an active consent record** (a simple enforced rule, with tests). This is a data-model demonstration, not a legal consent platform.
- Registry changes are audited like alert changes.

### REST API (triage service; all JSON; roles in section 7)

`GET /api/worklist` · `GET /api/nodes/{id}` · `GET /api/nodes/{id}/trend?range=24h|7d|30d|90d` · `GET /api/alerts?state=` · `POST /api/alerts/{id}/ack|escalate|resolve|note` (body `{note}`) · `GET /api/registry/gateways|nodes|subjects` · `POST /api/registry/gateways/{id}/revoke` (admin) · `GET /api/audit?limit=` · `GET /api/audit/verify` · `GET /healthz` · `GET /readyz` · `GET /metrics` (Prometheus, internal network only).

### FHIR (R5) and EHR write-back

- **EHR = self-hosted HAPI FHIR JPA server** (Apache 2.0, arm64 image `hapiproject/hapi`, Postgres backend, `fhir_version: R5`). HAPI ships **no security**: it listens on the docker network only; Caddy exposes it as `/fhir` only after a Keycloak-JWT check (Unit 9).
- **fhir-sync worker** (JetStream durable consumer) writes: `Patient` (pseudonymous subject), `Device` (node and gateway), `Observation` with `valueSampledData` per 60-second window (`interval`/`intervalUnit` set, `offsets` absent, raw integer counts, missing frames as `E`), `Flag` for open alerts (pipeline status, not clinical), `Provenance` linking each Observation to the gateway and node, and `AuditEvent` rows from the audit log. Writes are **conditional updates / `If-None-Exist`** keyed on a business identifier so redelivery is idempotent. Reuse `tools/fhir_export.bundle()` unchanged (keep its signature; `tests/test_fhir_export.py` must still pass).
- Keep the local code system `urn:thesisb:ehg` (no standard EHG code was found; do not invent LOINC/SNOMED codes). Source metadata (gain/baseline/channels/fs) comes from `data/converted/<db>/<record>.npz` via `tools.convert.source_file` (mount read-only, `CONVERTED_DIR`).
- **Verification before building:** confirm on the pinned HAPI version that R5 `Observation` with `SampledData`, conditional create, `Flag`, `Provenance` and `AuditEvent` work and are searchable. If R5 support has a blocker, stop and ask Byron whether to fall back to R4 (`SampledData.period` instead of `interval`) behind an adapter; do not silently downgrade.
- `fhir.resources==8.3.0` (pinned) validates **structure** in tests. State clearly it is not full conformance. Document an optional manual run of the official HL7 FHIR Validator; do not claim conformance beyond what was run.
- The triage service keeps a thin **live read-only facade** (`GET /fhir/metadata`, `Device`, `Observation?device=&date=` with a 10-minute window cap) for the UI panel, proxying HAPI with the user's token and consent checks.

---

## 7. Identity, certificate lifecycle and security specification (feeds `docs/security.md`)

STRIDE-style threat model per boundary (section 4 table), then a **controls table with a status column** (Implemented / Optional flag / Documented gap).

| Control | Where | Status after this work |
|---|---|---|
| Wire CRC-16 + seq + RESEND | BLE | Implemented (existing) |
| LESC passkey pairing, `_AUTHEN` perms, bonding | Firmware (Unit 13) | Optional flag `CONFIG_EHG_REQUIRE_PAIRING` |
| MCUboot with own ECDSA P-256 key | Firmware (Unit 13) | Optional; key outside repo |
| MQTT TLS ≥ 1.3, mTLS, one identity per component, topic ACLs | Mosquitto (Unit 2) | Cloud profile |
| **Short-lived (7-day) gateway certificates, CSR enrolment via Keycloak, revocation** | Enrolment API + registry (Unit 7) | Implemented |
| OIDC SSO, TOTP MFA, RBAC (`viewer`, `operator`, `admin`), PKCE | Keycloak + Caddy + services (Unit 7) | Implemented (Keycloak realm as code) |
| Service-to-service auth (OAuth2 client credentials, JWT validation) | Triage, HAPI facade (Units 7–9) | Implemented |
| Hash-chained audit log + FHIR `AuditEvent` + Loki | Triage service | Tamper-evident (not WORM) |
| Secrets management | SOPS + age-encrypted secrets in git, age key held off the VM and repo; Docker secrets at runtime; OCI Vault (free software keys) optional for the CA and backup keys | Implemented |
| Encryption at rest | VM disk (provider-side); backups encrypted with a key held off the VM | Partial; documented |
| Gateway outbox encryption | Python: file mode 0600 + FileVault. iOS: Data Protection class + Keychain-held key | Documented gap vs SQLCipher |
| Container hardening (non-root, read-only rootfs where possible, dropped capabilities), image scan | Compose + Trivy (Unit 14) | Implemented |
| Dependency and image provenance (SBOM, signing) | Syft + cosign (Unit 14) | Implemented |
| APPROTECT/readback protection, secure element | Board | Documented gap (replay rig) |
| Regulatory (TGA, Privacy Act, HRIPA, IEC 62304/14971/81001-5-1) | Docs (Unit 17) | See section 8 |

### Identity design (Keycloak)

- Realm `ehg` defined **as code** (`deploy/keycloak/realm-ehg.json` imported at start; no secrets in the file; client secrets injected from env). Run in **production mode** (`start`, Postgres, proxy headers behind Caddy), stay on 26.8.1 or later (an earlier device-grant bug let locked-out accounts obtain tokens).
- Clients: `triage-ui` (public, authorisation code + **PKCE**, no implicit flow), `ios-gateway` (public, **Device Authorization Grant** RFC 8628, scope `gateway:enrol`), `triage-api`, `fhir-sync`, `enroll-api` (confidential, client credentials / resource servers). Roles: `viewer`, `operator`, `admin`, plus `gateway` for enrolled phones. Require TOTP for `operator` and `admin`.
- Services validate JWTs (signature via JWKS, `iss`, `aud`, `exp`, role claims). Test: expired, wrong audience, wrong issuer, `alg=none` and tampered tokens are rejected.
- `AUTH_MODE=dev` (fixed dev users, loud "DEV MODE" banner, bound to 127.0.0.1 only) lets everything run and test without Keycloak.

| Action | viewer | operator | admin |
|---|---|---|---|
| Read worklist, trends, FHIR | ✔ | ✔ | ✔ |
| Acknowledge, add note, escalate, resolve | | ✔ | ✔ |
| Read audit log, run verify, change thresholds, revoke gateway, manage registry | | | ✔ |

### Certificate lifecycle (device identity)

Keycloak does not issue X.509 client certificates, so add a small **enrolment API** (`backend/enroll/`, FastAPI):
1. The phone (or the Mac gateway via a CLI) obtains a Keycloak access token (device grant for the phone; client credentials or device grant for the Mac).
2. It generates a key pair **on the device** (iOS Keychain) and sends a **CSR** plus the token to `POST /enroll`.
3. The API checks the token and role, checks the registry (not revoked), then has **step-ca** sign a **7-day** client certificate under an **intermediate CA** (CN = gateway id; ECDSA P-256; SAN/EKU for client auth). The root CA key is kept offline (Byron's Mac); the intermediate key lives on the VM (or OCI Vault).
4. Registry records the serial. Renewal repeats the flow before expiry (the 7-day life also matches the free Apple signing window).
5. **Revocation:** an admin revokes in the registry; the enrolment API regenerates a CRL; Mosquitto reloads it (`crlfile`; **verify** the option and the reload behaviour on 2.1), and short lifetimes bound exposure. Test: a revoked serial cannot connect.
6. `tools/pki.sh` (Unit 2) remains the bootstrap path for the root, intermediate and the service identities.

### Verified technical notes

**BLE pairing on macOS/iOS (high confidence on facts, medium on exact trigger behaviour):**
- Bleak on macOS **cannot pair, unpair or supply a passkey**: `BleakClient.pair()` raises `NotImplementedError` and `pair=True` is ignored. macOS starts SMP pairing itself when a protected GATT access is rejected, and shows a system dialog where the user types the passkey displayed by the peripheral. The first protected GATT call can block until the user finishes, so use a timeout ≥ 60 s plus one retry. iOS behaves the same way for CoreBluetooth apps (system pairing dialog); **verify** on the device.
- Firmware: register `bt_conn_auth_cb` with **only** `passkey_display` and `cancel` (IO capability DisplayOnly). Protect values **and CCCDs** with `BT_GATT_PERM_READ_AUTHEN` / `BT_GATT_PERM_WRITE_AUTHEN`. Do not protect service/characteristic discovery. Do not call `bt_conn_set_security()` from the peripheral on connect (Apple's accessory guidelines say not to request pairing until an ATT request is rejected with Insufficient Authentication). Use a random passkey and print it on the UART log. Avoid `CONFIG_BT_FIXED_PASSKEY` together with SC-only (Zephyr issue #49599). `_ENCRYPT`-only permissions may trigger Just Works and then fail an `_AUTHEN` check, so use `_AUTHEN`. `BT_GATT_PERM_*_LESC` alone does no permission checking (Zephyr advisory GHSA-p6f3-f63q-5mc2). Likely Kconfig (grep to confirm): `CONFIG_BT_SMP=y`, `CONFIG_BT_SMP_SC_ONLY=y`, `CONFIG_BT_SETTINGS=y` with flash storage for bonds.
- `CONFIG_BT_SMP_SC_ONLY` forces security level 4; no macOS-specific confirmation was found. Treat as to-be-tested.
- Stale bonds: after a reflash or bond erase, the Mac must "Forget This Device" (System Settings → Bluetooth); on iPhone, Settings → Bluetooth → Forget. Symptom: `CBATTErrorDomain Code=15 "Encryption is insufficient"` with no dialog. Put this in `docs/troubleshooting.md`.

**Mosquitto 2.x TLS (high confidence):** `tls_version tlsv1.3` is a **minimum** in 2.x; `require_certificate true`; `use_identity_as_username true` (client-cert CN becomes the username); `allow_anonymous false`; `acl_file` with `user`/`topic` and `pattern` lines using `%u`. The `acl_file` option is discouraged in the newest man pages in favour of a plugin but works. JWT/OIDC auth on Mosquitto needs a third-party plugin (**unverified**), so certificates are the MQTT credential and Keycloak governs enrolment. Verify `crlfile`, WebSocket listeners and `require_certificate` against the current `mosquitto.conf(5)` before use.

**paho-mqtt 2.x:** `tls_set()` has no "TLS 1.3 only" switch; use an `ssl.SSLContext` with `minimum_version = ssl.TLSVersion.TLSv1_3`, `load_cert_chain(...)`, then `client.tls_set_context(ctx)`. Broker certificate SAN must match the hostname. Check `ssl.HAS_TLSv1_3` on the Mac's Python.

**Reference config (adapt names; topics are `ehg/<node>/pkt`):**

```conf
# backend/mosquitto/mosquitto.tls.conf  (cloud profile; the local mosquitto.conf is untouched)
listener 8883
protocol mqtt
cafile   /mosquitto/pki/ca-chain.crt
certfile /mosquitto/pki/broker.crt
keyfile  /mosquitto/pki/broker.key
crlfile  /mosquitto/pki/ca.crl        # verify option name/behaviour
tls_version tlsv1.3
require_certificate true
use_identity_as_username true
allow_anonymous false
acl_file /mosquitto/config/acl
persistence true
persistence_location /mosquitto/data/
max_queued_messages 100000
```

```conf
# backend/mosquitto/acl   (CN = identity)
user ehg-bridge
topic read ehg/+/pkt
topic read ehg/_gw/+/status
user ehg-viewer
topic read ehg/+/pkt
# a gateway may publish packets for any node it serves, and only its own status topic
pattern write ehg/_gw/%u/status
user ehg-gw-01
topic write ehg/+/pkt
```

The iPhone connects to `:8883` with its enrolled certificate. If a carrier blocks 8883 (**unverified**; test on the real network), fall back to a **WSS on 443** listener that Caddy passes through at the TCP layer (SNI routing, `caddy-l4`) or a separate hostname. Verify that approach works with client certificates; if it does not, record the finding rather than weakening authentication.

---

## 8. Regulatory and privacy framing (for `docs/security.md` and `docs/regulatory.md`; verify each citation against a primary source before writing it, and omit anything you cannot verify)

- **Intended-purpose statement** (put it in `docs/security.md`, the README and the UI footer): *"Research prototype for measuring wireless telemetry performance. Not for clinical decision-making. Not supplied to any person for patient care. Makes no diagnostic or predictive claims."* The TGA decides whether software is a medical device by **intended purpose**, so keep UI and docs free of diagnostic or predictive language.
- **TGA position (verified 3 Nov 2026):** the CDSS exclusion does **not** cover software intended to process or analyse a signal from another medical device, so any future EHG contraction analysis or alerting would not qualify for it (the rules tightened further from 1 Nov 2026). The wellness exclusion does not cover monitoring of a serious condition. Therefore: this prototype claims **no** exclusion, makes no clinical claim, and a transport/storage-only layer might qualify as a communications or workflow exclusion (**unverified**; needs the full exclusion text). Say plainly that a clinical version of the analysis layer would likely be a regulated medical device.
- **Standards: "designed with reference to", never "compliant with":** IEC 62304 (software life-cycle, safety class reasoning, SOUP list), ISO 14971:2019 (hazard analysis: missed alert, stale data, data loss, duplicate data, wrong-patient/mislabelled node), IEC 81001-5-1 (health software security life cycle: threat model, SBOM, signed artefacts, vulnerability handling). Edition/date details were **not** verified; confirm before citing.
- **Data:** the PhysioNet TPEHG/TPEHGT/ICEHG/EHGDB records are de-identified public data, outside the Privacy Act 1988 and the NSW HRIP Act 2002 as long as nobody attempts re-identification. TPEHGDB is Open Data Commons Attribution (attribution required); ICEHG is ODbL (share-alike for derived databases). Confirm the exact licence for each dataset used. **Use only these records (or ethics-approved data) on the cloud VM.** Real patient data on a free, unsupported cloud account is not defensible.
- **If Byron ever records real volunteers with the nRF52840,** the data become identifiable health information: HREC approval, HRIPA/APP obligations (including APP 8 cross-border disclosure; choose an Australian OCI home region) and data-security duties then apply to the whole chain. State plainly that this is the line the prototype must not cross without ethics approval.
- **Interoperability references:** HL7 FHIR R5; HL7 AU Core (R4-based, not claimed); the FHIR Subscriptions backport; IHE and PCHAlliance/Continua RPM guidance as comparators for the device → gateway → cloud → EHR interfaces. Cite only what you have opened.
- **What the prototype can credibly claim:** designed with reference to the standards above; produces FHIR R5 Observation/SampledData and AuditEvent/Provenance and writes them to a reference FHIR server; demonstrates end-to-end security controls (mTLS, OIDC with MFA, audit, encrypted backups with tested restores) on synthetic or public data. **What it cannot claim:** TGA exemption or compliance, clinical safety, validated alerting, HA/DR/SLA, APP/HRIPA compliance for real data, AU Core conformance.
- US/EU frameworks appear only as comparators. Cite from `papers/INDEX.md` where relevant (for example refs 17, 42, 52, 57); do not invent citations. `docs/progress.md` records that several references were previously mis-attributed.

---

## 9. Phased implementation plan

Units run in dependency order. **Tier A** = must do, **Tier B** = should do, **Tier C** = could do (the cut line if time runs short; Byron has 12+ weeks but the 30 April 2027 deadline is fixed). Docs updates listed at the end of a unit are mandatory but do not count against its file budget. Commit locally per unit on branch `feat/enterprise-topology` (never on `main`, never push), message style `feat(gateway): ...` with **no AI trailer**.

```
U0 → U1 ─┬→ U2 ─┬→ U3
         │      └→ U4 → U5 → U6 ─┬→ U7 ─┐
         │                       ├→ U8 ─┼→ U9 → U10 → U11
         │                       └──────┘
         └→ (docs only)    U5 → U12 ;  U2 → U13 (hardware) ;  U1 → U14
         U7, U11, U13 → U15 (iOS) → U16 (measurements) → U17 (docs, regulatory, verification)
```

### PHASE 0: Baseline and guardrails

**Unit 0 (Tier A): Freeze the baseline.**
- Check `git status` is clean (stop and ask if not). Create branch `feat/enterprise-topology` from the current commit and tag the starting point `baseline-pre-enterprise`.
- Run `uv run pytest -q`; record the result.
- Ask Byron (AskUserQuestion) whether the **6 h TPEHG soak** (H2) and the first **H3** work are done. Recommend doing the soak on the tagged commit **before** Unit 3 merges, because later units must not contaminate it. H3 and the soak are the thesis-critical measurements; schedule them early (Unit 16 repeats them on the cloud chain).
- Create `docs/handoff.md` from the template in section 10b (status "no work started") and add `tests/test_handoff.py`: if `docs/handoff.md` exists it must contain the required headings and a valid `Resume from` unit id.
- If Docker and `data/converted` are available, run the no-hardware row of README "Reproducing the results" (`--simulate 1 --sim-loss 0.01`, then `eval/integrity.py` and `eval/latency.py`) and write the numbers into a dated `docs/progress.md` entry "baseline before enterprise work". If Docker is not up, say so and do not start or restart Docker Desktop.

### PHASE 1: Decisions and topology docs (docs only)

**Unit 1 (Tier A): ADRs, topology map, licence register.** Files: `docs/decisions/0007`–`0017`, `docs/production-topology.md`, `docs/licences.md`.
- ADRs: `0007-enterprise-evolution-keep-influx-as-reference`; `0008-mqtt-mtls-and-topic-acls`; `0009-gateway-durable-outbox`; `0010-oracle-always-free-cloud-target` (exit criteria if the VM is reclaimed or limits change again; Azure DR drill as future work); `0011-triage-service-pipeline-health-not-clinical`; `0012-timescaledb-nats-data-platform` (why Mosquitto + NATS, why not EMQX/Redpanda/Kafka, TSL licence note, dual-write parity); `0013-keycloak-and-certificate-lifecycle`; `0014-hapi-fhir-r5-reference-ehr` (R5 choice, HAPI no-security caveat, AU Core not claimed); `0015-native-ios-gateway-free-signing`; `0016-observability-and-slos`; `0017-backup-dr-and-design-only-ha`.
- `docs/production-topology.md`: the architecture diagram, the 4-tier map, data-flow boundary table (section 4), the six-hop chain table with design-only items (section 4b), the research's comparison (Appendix A) rewritten against this repo with an Implemented / Added / Design-only column, and the **enterprise capability checklist**: device registry/provisioning, OTA, multi-subject data model, consent, audit, alert management, HA, DR (RPO/RTO), observability, SLOs, IaC, supply chain, each mapped to its free implementation and its honest status.
- `docs/licences.md` per section 5c. Link everything from `docs/codebase-guide.md` and `README.md`. Tests `test_decision_records_have_sections`, `test_relative_links_resolve` pass.

**CHECKPOINT 1.** Summarise the ADRs and capability checklist; ask Byron to approve before any code.

### PHASE 2: Transport hardening (works locally first)

**Unit 2 (Tier A): MQTT TLS 1.3, certificates, ACLs (local "cloud profile").**
- Files: `tools/pki.sh`, `backend/mosquitto/mosquitto.tls.conf`, `backend/mosquitto/acl`, `backend/docker-compose.cloud.yml` (override), `gateway/gateway.py` (flags), `backend/ingest/ingest.py` (env), `viewer/server.py` (flags), `tests/test_mqtt_tls_config.py`.
- `tools/pki.sh`: OpenSSL; offline ECDSA P-256 **root**, an **intermediate** for issuing, broker cert with SANs from `--host` (plus `localhost`, `127.0.0.1`), service client certs by CN (`ehg-gw-01`, `ehg-bridge`, `ehg-viewer`); writes to `backend/pki/` (gitignored), `chmod 600` keys, never prints keys; `--help` prints the header.
- Gateway: add `--mqtt-port`, `--mqtt-tls`, `--ca`, `--cert`, `--key`; build the `SSLContext` per section 7. Ingest and viewer: `MQTT_PORT`, `MQTT_TLS`, `MQTT_CA`, `MQTT_CERT`, `MQTT_KEY` (defaults reproduce today's plain 1883 behaviour exactly).
- Override adds the 8883 listener, mounts `backend/pki` and the ACL read-only, and **does not publish 1883 to the host**. Default `docker-compose.yml` is untouched.
- Tests (no Docker): parse the TLS config and assert `tlsv1.3`, `require_certificate true`, `allow_anonymous false`, `use_identity_as_username true`; the ACL has no `#` grants and every service identity is least-privilege; the override does not publish 1883; gateway defaults leave TLS off. Integration (opt-in `EHG_INTEGRATION=1`): `--simulate 1` over TLS then `eval/integrity.py` shows 100 %. Negative tests: no client cert rejected; wrong CN cannot publish to another gateway's status topic; TLS 1.2-only client rejected.
- Docs: start `docs/security.md` (threat model + controls table), README "cloud profile locally", troubleshooting (SAN mismatch, `ssl.HAS_TLSv1_3`).

**Unit 3 (Tier A): Durable gateway outbox.**
- Files: `gateway/outbox.py`, `gateway/gateway.py`, `backend/ingest/ingest.py`, `eval/latency.py`, `tests/test_outbox.py`, `tests/test_gateway.py`.
- SQLite WAL (`synchronous=NORMAL`), mode 0600, `gateway/outbox.db`. Schema: `id INTEGER PRIMARY KEY AUTOINCREMENT, node_id, seq, t_inject, topic, payload, created, attempts`.
- `publish()` writes the row **first**, then publishes if connected; a row is deleted only on PUBACK (paho v2 `on_publish`). Drain oldest-first, ≤ 100 in flight. Exponential backoff 1 s → 60 s with jitter (`reconnect_delay_set`). Pending rows replay before new traffic after a restart.
- Replayed messages keep their original `t_gw_rx`, get a fresh `t_mqtt_pub`, and carry `"replayed": true` (ingest stores `m.get("replayed", False)`; `eval/latency.py --exclude-replayed`, default reports both). Prove a duplicate replay changes nothing in `ehg_raw`.
- Disk cap `--outbox-max-mb` (default 512): drop oldest, log loudly, count `outbox_dropped`, keep the gap identifiable. Never block the BLE callback. `--outbox PATH` defaults **off**.
- Retained status `ehg/_gw/<gateway_id>/status` (`online`) with a Last Will (`offline`).
- Tests: broker-down → rows accumulate → broker-up → delivered in order exactly once (fake publisher with injectable failures); kill-and-restart replays; cap policy; backoff schedule with a fake clock; lost-PUBACK duplicate is harmless; `Node.on_bytes` timing unchanged when off. Benchmark output (not a CI assert) goes into `docs/progress.md`. Integration (opt-in): stop the broker for 60 s mid-run; `eval/integrity.py` reports 100 % with `missing ranges: []`.

**CHECKPOINT 2.** Full test suite; show Byron the outbox overhead and outage-recovery result; ask whether to enable the outbox by default.

### PHASE 3: Cloud foundation (infrastructure as code)

**Unit 4 (Tier A): Infrastructure as code (Terraform + cloud-init + Docker Compose).** Files under `deploy/`: `deploy/terraform/` (Terraform `>= 1.6` with the `oracle/oci` provider, version-pinned in `versions.tf`; modules `network`, `compute`, `storage`, `monitoring`: VCN, subnet, internet gateway, security list allowing only 22 from Byron's IP, 80, 443, 8883; one `VM.Standard.A1.Flex` 2 OCPU / 12 GB Ubuntu 24.04 aarch64 with a ~50 GB boot volume and a 150 GB block volume; object-storage bucket for backups; compartment quotas/budget as supported; a Notifications topic and Monitoring alarm for "VM down"), `deploy/terraform/backend.tf.example` (state in OCI Object Storage via its S3-compatible API, or OCI Resource Manager), `deploy/cloud-init/user-data.yaml.tftpl` (templated by Terraform and passed as VM `user_data`: unattended upgrades, SSH hardening, host firewall mirroring the security list, Docker engine + compose plugin, a non-root `ehg` service user, directory layout, node_exporter, fetch the repo at a pinned tag, `docker compose up -d`), `deploy/update.sh` (idempotent update over SSH: verify the tag signature or commit hash, pull, `docker compose pull`, `up -d`, health check, roll back to the previous tag on failure), `deploy/compose/docker-compose.cloud.yml` (the full stack with memory limits from section 5b, pinned image versions, healthchecks, restart policies, non-root users, read-only root filesystems where practical, no host-published ports except 80/443/8883), `deploy/README.md`, `tests/test_deploy_assets.py`. **Secrets:** encrypted with **SOPS + age** (`deploy/secrets/*.sops.yaml`; the age private key stays on Byron's Mac and is copied to the VM once, out of band), never plaintext in git; `.sops.yaml` is committed, keys are not.
- Before writing, fetch Oracle's current "Always Free Resources" page and confirm the section 5 numbers; report drift. **Verify every image has a `linux/arm64` manifest** (`docker buildx imagetools inspect`) and report any that do not (the non-HA `timescale/timescaledb` image arm64 was unconfirmed; prefer `timescale/timescaledb-ha`).
- Tests: `terraform fmt -check` and `terraform validate` (with `-backend=false`) plus `tflint` and `trivy config` when the binaries exist (skipped otherwise); the cloud-init template renders and parses as YAML; `deploy/update.sh` passes `bash -n` and `shellcheck`; `docker compose config` (skipped without Docker) shows no host-published 1883/5432/8086/3300/8780/8080; every service has a memory limit and the limits sum to ≤ 9.5 GB; no secret-looking strings in `deploy/` (also run `gitleaks`); only `*.sops.yaml` secrets are committed; Terraform state/tfvars patterns are gitignored.

**Unit 5 (Tier A, Byron executes): Oracle runbook and first deployment.** Files: `docs/deployment.md`, `deploy/runbook/` checklists. **You write the runbook; Byron runs it.**
- Click-by-click: create the Oracle account (phone + card verification; ask Byron's consent about the card; **do not** upgrade to Pay-As-You-Go); choose an **Australian home region** (Sydney or Melbourne; A1 capacity there is **unverified**, and "out of host capacity" errors are common, so document retry/other-AD advice; the home region is permanent); API key for Terraform; DNS name (ask Byron: Byron has no domain; default DuckDNS + Caddy HTTP-01, check duckdns.org is up first; alternatives noted in section 5); `terraform init/plan/apply` (Byron reviews the plan first); `tools/pki.sh --host <name>`; copy gateway cert/key/CA to the Mac securely; bring up the **edge subset** first (Caddy, Mosquitto mTLS) and prove a `--simulate 1` run from the Mac to the VM.
- Keep a **`docs/deployment.md` log** of what Byron actually saw (capacity, region, timings). Record `docker stats` for the edge subset.
- Disk guard and retention defaults per section 5b.

**CHECKPOINT 3.** Byron has a reachable VM and the Mac gateway publishes over mTLS from the internet. Confirm before the data platform goes on it.

### PHASE 4: Data platform

**Unit 6 (Tier A): NATS bus, TimescaleDB, writers and the parity proof.** Files: `backend/bridge/bridge.py` (MQTT → JetStream), `backend/ts_writer/writer.py`, `backend/ingest/ingest.py` (optional JetStream mode kept as the influx-writer), `backend/db/migrations/*.sql`, `backend/db/migrate.py`, `eval/backend_client.py` (backend abstraction: `--backend influx|timescale`), `eval/parity.py`, `tests/test_bridge.py`, `tests/test_ts_writer.py`, `tests/test_backend_client.py`, `tests/test_parity.py`, compose service definitions under profile `platform`.
- Bridge: persistent MQTT session QoS 1 subscriber; **acks MQTT only after the JetStream publish ack**; JetStream `Nats-Msg-Id` header set to `node:seq:t_inject` so the stream's duplicate window adds a second dedup layer (**verify** the feature on the pinned version). Stream `EHG` with file storage, limits and a memory cap; consumers are durable, pull-based, explicit-ack, with a max-deliver and a dead-letter subject that the triage worker surfaces.
- ts-writer: batch inserts with `ON CONFLICT DO NOTHING`; idempotent on redelivery; visible `degraded` state and metrics (lag, redeliveries, dead letters); backs off when Postgres is unavailable and NATS buffers.
- Migrations per section 6 (hypertables, compression policy, continuous aggregates, retention). Confirm `SHOW timescaledb.license;` reports the Community edition and record it in `docs/licences.md`.
- **Parity proof (`eval/parity.py`)**: run the same `--simulate 1 --sim-loss 0.01` stream into both stores; assert identical `(node, seq, ch)` sets, **bit-identical sample values**, identical missing ranges, and that `eval/integrity.py` and `eval/latency.py` give the same answers from either backend (latency within a stated tolerance, because ingest stamps differ). If anything differs, stop and report; do not switch the cloud profile to Timescale-only until parity passes. Report the exact commands and numbers in `docs/progress.md`.
- Tests: bridge ack ordering with fakes (kill between publish and ack must redeliver, never drop); duplicate redelivery writes nothing twice; poison message goes to dead letter; migration up/down on a throwaway Postgres (skipped without Docker).

**CHECKPOINT 4.** Show Byron the parity proof results and the data-platform memory use on the VM; get an explicit yes before the cloud profile uses TimescaleDB as its primary store.

### PHASE 5: Identity, services and EHR

**Unit 7 (Tier A): Keycloak, registry and certificate enrolment.** Files: `deploy/keycloak/realm-ehg.json`, `backend/enroll/app.py`, `deploy/step-ca/` (config), `backend/enroll/stepca_client.py`, `backend/db/migrations/*registry*.sql`, `tests/test_enroll.py`, `tests/test_realm_export.py`, compose services under profile `identity` (Keycloak, step-ca, enrolment API).
- Keycloak per section 7 (production mode on the shared Postgres, memory limit 1.36 GB, realm as code, TOTP for operator/admin, PKCE UI client, device-grant iOS client, resource servers). The realm JSON must contain no secrets; a test greps it.
- **Certificate authority: Smallstep `step-ca`** (open-source, Apache-2.0, widely used private CA; arm64 images exist, **verify**), not hand-rolled signing code. The enrolment API authenticates the caller with Keycloak, applies registry rules, then asks step-ca (JWK/provisioner token) to sign the CSR. Verify step-ca's templates (client-auth EKU, 7-day lifetime, CN from the registry), its revocation/CRL support on the pinned version, and whether its OIDC provisioner could consume Keycloak tokens directly (a simpler route); record the findings in ADR 0013.
- Enrolment API per the certificate lifecycle in section 7: CSR validation (key type/size, CN pattern, no wildcard SANs), role check, registry check, 7-day cert, intermediate key handling, CRL regeneration, rate limiting, audit rows. Tests: expired/wrong-audience/`alg=none` tokens rejected; revoked gateway cannot enrol; certificate has client-auth EKU only and the right lifetime; CN cannot be chosen by the caller (taken from the registry).
- Admin CLI: `python -m backend.enroll.cli enrol-mac`, `revoke`, `list` for the Python gateway. Integration (opt-in): a revoked serial cannot connect to Mosquitto (this depends on `crlfile`; if it does not reload live, document the restart procedure honestly).

**Unit 8 (Tier B): Rules engine, state, audit and triage service.** (a) `backend/triage/rules.py` + `tests/test_triage_rules.py` (section 6 rules, hysteresis, severity score and worklist as **pure functions**); (b) `backend/triage/state.py`, `backend/triage/audit.py` + tests (Postgres schema, lifecycle, hash chain, triggers, `--verify` CLI, tamper detection for edit/delete/reorder, concurrent writers); (c) `backend/triage/app.py` (FastAPI), `backend/triage/stream.py` (JetStream consumer + `ehg_health` writer), `backend/triage/auth.py` (JWT validation, `AUTH_MODE=dev|oidc`, RBAC matrix), `backend/triage/Dockerfile` (repo-root context, non-root, arm64), `tests/test_triage_api.py`, `tests/test_triage_auth.py`.
- Reuse `gateway.tracking.GapTracker` if it fits; otherwise write a minimal tracker and say why in the file header. Do not silently duplicate logic.
- Tests via FastAPI `TestClient`: every route × role, 404, 409 (illegal transition), 422 (missing note), degraded mode, trend range validation, spoofed headers and bad tokens rejected, no secrets in responses.
- Expose `/metrics` (alert counts, consumer lag, rule evaluation time) for Prometheus.

**Unit 9 (Tier A): HAPI FHIR reference EHR, write-back, consent and live facade.** Files: `backend/fhir_sync/worker.py`, `backend/triage/fhir_api.py`, `deploy/hapi/application.yaml`, `tests/test_fhir_sync.py`, `tests/test_fhir_api.py`, `pyproject.toml`/`uv.lock` (+ `fhir.resources==8.3.0`; **do not** use the `glichtner` R5-preview fork).
- Follow section 6: verify R5 + SampledData + conditional create on the pinned HAPI first (stop and ask if blocked). Memory-cap the JVM. Postgres backend. Network-internal only; Caddy route `/fhir` gated by Keycloak JWT. Document that HAPI has no built-in security and that this is a custom resource-server layer, not SMART-on-FHIR.
- Tests: Bundles/Observations parse with `fhir.resources`; `intervalUnit` present; `interval` set and `offsets` absent; raw counts round-trip exactly; missing frames are `E`; idempotent redelivery (conditional update); consent enforced (no active consent → 403); window cap; `OperationOutcome` on bad parameters. Integration (opt-in): write a window to HAPI, read it back through the facade, compare samples bit-exactly with the Timescale rows.

**CHECKPOINT 5.** Demo locally (compose profiles `platform`, `identity`, `triage`, `ehr`): `--simulate 3 --sim-loss 0.05`, stop one node to see `NODE_OFFLINE`, walk an alert through its lifecycle, verify the audit chain, read the Observation from HAPI. Ask Byron before continuing.

### PHASE 6: Clinician UI and front door

**Unit 10 (Tier B): Triage page.** Files: `viewer/triage.html`, `viewer/triage.js`, `viewer/triage.css`, `tests/test_triage_ui.py`. Served by the triage service at `/`. No framework, no CDN, no external fonts (strict CSP). Sign-in via Keycloak PKCE redirect; tokens in memory only (no `localStorage`).
- **Worklist:** one row per node: severity icon + text label + colour (never colour alone), node id, last seen, open-alert count, score, a **7-day inline SVG sparkline** of hourly `delivery_pct` with the 99 % line, current p95 latency. Sorted exactly as the API returns it.
- **Detail view:** 24 h / 7 d / 30 d / 90 d buttons; two SVG charts (delivery %, p95 latency) with **threshold bands** (99 % for H2, 2000 ms for H1); open alerts with an **alert modal** (Acknowledge, Escalate, Resolve, Add note; note required for escalate/resolve; focus trap, Esc to close, restores focus).
- **Context panel (tabs):** Node (id, record, fs, channels, last seen, firmware/packet version, owning gateway), **EHR** (live `Device` + `Observation` + `Flag` JSON from HAPI via the facade, copy button, consent state), Registry (gateways and certificate expiry), Audit (last 20 events). No fabricated clinical notes or medications.
- **States:** loading, empty ("no nodes yet: start the gateway"), error/degraded banner, stale-data indicator, "DEV MODE" banner, session-expired screen. `aria-live="polite"` for new alerts; full keyboard operation; ≥ 4.5:1 contrast; works at phone width with a 16 px gutter and no horizontal scroll; light/dark via `prefers-color-scheme`. Footer carries the intended-purpose statement.
- Tests (static): required element ids and ARIA attributes exist; no `http(s)://` resource URLs; no inline handlers; no `localStorage`; no diagnostic wording (grep "diagnos", "predict", "preterm risk"). Byron does a manual visual check; screenshots in `docs/img/`.

**Unit 11 (Tier A): Front door and end-to-end SSO.** Files: `deploy/caddy/Caddyfile`, `tests/test_caddy_config.py`. Caddy: automatic HTTPS, HTTP→HTTPS, HSTS and security headers, request-size and rate limits, routes for `/` (triage), `/fhir` (HAPI facade), `/auth` (Keycloak), `/grafana` (Grafana behind Keycloak OIDC generic OAuth). Nothing else is exposed. Tests parse the Caddyfile and assert the route list, headers and that Prometheus/Loki/Postgres/NATS/HAPI-direct are not routed.

**CHECKPOINT 6.** Byron deploys the full stack on the VM; record `docker stats` and compare with section 5b.

### PHASE 7: Reliability, observability, supply chain

**Unit 12 (Tier A): Observability, SLOs, backups and the restore drill.** Files: `deploy/observability/` (Prometheus rules, Loki and Alloy config, Grafana provisioning with dashboards-as-code), `deploy/backup/backup.sh`, `deploy/backup/restore_drill.sh`, `tests/test_observability_assets.py`, `docs/slo.md`, `docs/dr.md`.
- **Observability:** Prometheus scrapes node_exporter, cAdvisor, postgres_exporter, Mosquitto/NATS/Keycloak/HAPI/triage metrics; Loki receives structured container logs (Alloy). Grafana dashboards: pipeline overview, delivery/latency, consumer lag, alerts, resource use, backup freshness. Retention capped (Prometheus 15 d, Loki 7 d). Alert routing: Grafana alerting → ntfy/Telegram webhook, and the OCI Monitoring alarm for "VM down" as the out-of-band path.
- **SLOs** (`docs/slo.md`, defined as Prometheus recording rules, measured not contractual): ingest delivery ratio ≥ 99 %, end-to-end p95 latency < 2 s on the home path, consumer lag < 5 s, API availability, backup age < 26 h. No SLA claims.
- **Backups:** **pgBackRest** (the standard Postgres backup tool: full + incremental + WAL archiving for point-in-time recovery; verify it works with the TimescaleDB image) for Postgres (all three DBs), and `restic` for the NATS stream snapshot, the Keycloak realm export, config and the audit-chain head hash, **encrypted with a key that is not on the VM**, to OCI Object Storage (20 GB cap; mind it) and to the Mac. Declared **RPO ≈ 24 h**.
- **Restore drill (`restore_drill.sh`)**: rebuild the stack from Terraform + cloud-init + backup on a clean target (the Mac's Docker for the drill, since the Mac is arm64 too; do not touch the VM), run `eval/parity.py` and the audit `--verify` against the restored data, and **time it** to report a measured RTO. Tests for the scripts use `bash -n` and a dry-run mode.
- **Optional (Tier C):** use one free AMD micro VM as an external uptime probe and second backup mirror (Terraform module `deploy/terraform/probe/`); record its limits (1 GB, 50 Mbps).
- **HA design-only (`docs/dr.md`)**: what multi-AD, a clustered broker (EMQX BSL), Postgres replicas and cross-cloud restore (Azure free VM) would require, with honest limits. Not built.

**Unit 14 (Tier B): CI, supply chain and container hardening.** Files: `.github/workflows/tests.yml` (keep its authorship check), `.github/workflows/security.yml`, `.github/dependabot.yml`, `deploy/sbom/`, `docs/supply-chain.md`.
- Ask Byron whether the repo may be public (unlimited Actions minutes, CodeQL). If private, skip CodeQL, keep Dependabot/Trivy/Syft/cosign (**availability on private repos unverified**) and stay within 2,000 minutes (Linux runners only; never run macOS jobs automatically because they cost 10×).
- Jobs: pytest (existing), `terraform validate` + `tflint`, `shellcheck`, `gitleaks` secret scan, Trivy image, filesystem and IaC (`trivy config`) scans, Syft SBOM, cosign signing of images built for `linux/arm64` with `buildx` (push to GHCR if free for your visibility; **verify**), a check that every compose image is pinned. Document the licence/vulnerability-handling process (IEC 81001-5-1 style).

### PHASE 8: Edge security (**hardware-gated**)

**Unit 13 (Tier B): LESC pairing and own signing key.**
- **Stop and ask Byron** (AskUserQuestion) before building or flashing: he must be at the DK, the UART must be free, and flashing changes the board's state. Do not flash without a yes.
- Files: `firmware/ehg_node/Kconfig` (new `EHG_REQUIRE_PAIRING`, default n), `firmware/ehg_node/security.conf` (overlay), `firmware/ehg_node/src/main.c`, `firmware/ehg_node/src/ehg_service.c`, `firmware/build.sh` (`--secure`), `firmware/ehg_node/sysbuild.conf`, `gateway/gateway.py`.
- Default build stays byte-for-byte equivalent in behaviour. Grep `~/ncs/v3.4.1` to confirm every Kconfig/API symbol (SMP, SC-only, settings, `SB_CONFIG_BOOT_SIGNATURE_*`) before writing.
- Firmware per section 7. Mac gateway: never call `pair()`; wrap the first `start_notify`/control write in a ≥ 60 s timeout with one retry; print "enter the passkey shown on the board's UART log in the macOS dialog"; detect the stale-bond error and print the "Forget This Device" instruction.
- Signing: generate an ECDSA P-256 key with `imgtool` **outside the repo** (path from an env var, gitignored), point `SB_CONFIG_BOOT_SIGNATURE_KEY_FILE` at it only in the secure build. Warn Byron: images signed with the dev key will not be accepted by a bootloader built with the new key, so a J-Link reflash is needed, and losing the key means losing OTA. He must back it up.
- Measurement (Byron present): repeat run 3 (TPEHG tpehg1007, 7 min, `--faults drop=0.05,burst=3,corrupt=0.01,disc=120,seed=3`) with and without pairing. Expect 100 % delivery, 0 mismatches, no meaningful H1 change; **report any difference honestly**; add a new `docs/progress.md` row, **never overwrite run 3**.

### PHASE 9: Native iOS gateway

**Unit 15 (Tier A for 15a–15c, Tier C for 15d): Swift / CoreBluetooth gateway** in `ios/EHGGateway/` (Xcode project, SwiftUI). Byron has Xcode on the Mac and a free Apple account. **Do not run Xcode or install anything on the iPhone yourself; write code, tests and instructions; Byron builds and installs.**
- **15a Feasibility spike (do first; gate).** A minimal app that (1) scans for `EHG-` nodes, connects, subscribes to the data characteristic and logs packets with `bluetooth-central` background mode and **state restoration** (`CBCentralManagerOptionRestoreIdentifierKey`, `centralManager(_:willRestoreState:)`); (2) opens an MQTT connection with **CocoaMQTT** (MIT) over TLS 1.3 with a client identity. Questions to answer with evidence and write into `docs/ios-gateway-feasibility.md`: does a free Personal Team allow Background Modes, Keychain and local notifications; can the MQTT library present an **EC (P-256) or RSA identity from the Keychain** (its PEM path does not support encrypted or EC PEM keys; test a PKCS#12/`SecIdentity` route; Secure Enclave keys may not work as a TLS client identity); does 1 kHz-class traffic survive a **30-minute locked-screen** run; what iOS does after a background kill; whether 8883 is reachable on Byron's carrier (else test the WSS-443 route). **Gate:** if the spike cannot hold a locked-screen run or present a client certificate, stop and ask Byron which compromise to accept (for example WSS with a bearer token, or screen-on operation) and document it as a security deviation; do not hack around iOS limits.
- **15b App core.** Swift decoder for wire v2 (little-endian, CRC-16/CCITT-FALSE, reject bad CRC), the gap-tracking/RESEND and clock-mapping logic ported from `gateway/tracking.py` (same constants: retry 1 s, max 5 tries), publishing **exactly the same JSON** to `ehg/<node>/pkt` QoS 1, a durable SQLite **outbox** (Data Protection class `completeUntilFirstUserAuthentication`, backoff 1 s → 60 s, replay marked `"replayed": true`, disk cap), LWT status topic, on-screen status (connection, packet counts, missing/resent, last PUBACK, certificate expiry, signature-days-left reminder), a log export. Target: iOS version of Byron's phone (ask).
- **Wire-format drift guard (third copy):** `tests/fixtures/packet_vectors.json` holds golden vectors generated by `gateway/packet.py` (including a bad-CRC and a 24-bit case); the Swift unit tests (`ios/EHGGatewayTests/`) decode the same vectors; `tests/test_ios_wire_format.py` statically checks the Swift constants (version, header layout, CRC polynomial/init, UUIDs, opcodes) against `gateway/packet.py`/`ehg_packet.h` in the style of `tests/test_docs.py`. Extend `docs/interface-spec.md` to three copies. Unit tests must also pass `xcodebuild test` on the Mac (Byron runs it); CI does not build iOS.
- **15c Enrolment.** Keychain key pair generated on the phone; Keycloak **device grant** login; CSR to the enrolment API (Unit 7); store the certificate and use it for mTLS; automatic renewal before the 7-day expiry; handle revocation visibly. `docs/ios-gateway.md` covers install, the weekly re-sign procedure (Xcode → Run on the device before day 7), the bond/"Forget This Device" steps and what happens at expiry.
- **15d OTA via the app (Tier C).** Use Nordic's iOS McuMgr library (**verify** name, licence and SMP/MCUboot compatibility) to upload an image signed with the Unit 13 key. Fleet rollout, staged deployment and rollback policy stay **design-only**.
- **Security notes to document:** free-signed build is a bench gateway; no TestFlight/App Store; no push; iOS may terminate the app and state restoration does not relaunch a force-quit app; the outbox protects against loss but not against a long outage while the app is not running.

**CHECKPOINT 7.** Byron has installed the iOS gateway and confirmed enrolment, a locked-screen run and the weekly re-sign procedure. Decide whether OTA (15d) is worth doing.

### PHASE 10: Measurement and drills

**Unit 16 (Tier A): Measurements on the cloud chain** (Byron present for anything with hardware). Files: `eval/clock_check.py`, `eval/latency.py` (`--ingest-clock-offset-ms`, `--exclude-replayed`), `eval/load_test.py`, `tests/test_clock_check.py`, `tests/test_load_test.py`, `docs/progress.md` templates.
- **Clock offset:** with ingest on the VM, `t_ingest_rx` and `t_db_ack` use the VM clock while `t_inject_host` uses the Mac's. `eval/clock_check.py` measures each host's offset against a common NTP source plus pasted `chronyc tracking` from the VM, writes `eval/results/clock_<host>.csv`, and `eval/latency.py` prints the stated uncertainty. Tests: offset arithmetic with fake NTP responses; sign convention.
- **H1-WAN:** Mac gateway → internet → Oracle VM (mTLS), same record, faults and duration as the local run. A TLS-vs-plain local comparison isolates transport overhead from WAN delay. **Separate supplementary rows; never overwrite the local H1 numbers (median 259 ms, p95 478 ms).**
- **H1-cellular:** the iPhone gateway on its own 4G/5G (Wi-Fi off), Mac gateway off. Record phone model, iOS version, carrier, signal, screen state, battery drain, how long the connection holds, and data use (about 20–25 MB per 6 h per node; warn Byron to check his plan). If 8883 is blocked, record that and test the 443 route. This is **not** an LTE-M/NB-IoT measurement and must not be described as one. If the iPhone is not ready, a Mac on a phone hotspot is the fallback and is labelled as such.
- **H3 scale (`eval/load_test.py`):** N simulated nodes (8, 16, 32…) through the cloud chain; report loss, p95 latency, consumer lag, CPU and memory on the VM (Prometheus), and the node count where an SLO breaks. This directly supports H3 (≥ 8 nodes); real boards are still needed for any claim about hardware, and the report must say which nodes were simulated.
- **6 h soak** on the cloud chain (H2 at WAN scale).
- **Failure drills (record each in `docs/dr.md` with the observed recovery):** broker killed; NATS killed; Postgres killed; HAPI down (fhir-sync backs off, no loss); VM reboot; gateway offline then replay; revoked certificate; expired certificate; Keycloak down (existing sessions and gateways keep working; enrolment and logins fail visibly).
- Report each result as measured, inferred or not verified.

### PHASE 11: Wrap-up

**Unit 17 (Tier A): Docs, regulatory pack and final verification.**
- Update `README.md` (layout, quickstarts for the profiles `cloud`, `platform`, `identity`, `triage`, `ehr`, new rows in "Reproducing the results"), `docs/codebase-guide.md` (every new file in the file index; updated journey diagram), `docs/interface-spec.md` (`replayed`, status topic, triage API, DB schemas, FHIR resources written, three wire-format copies), `docs/troubleshooting.md`, `docs/security.md` (final controls table), `docs/progress.md` (dated entry), `docs/production-topology.md` (final statuses).
- **API and architecture docs:** `docs/api/openapi.json` exported from the FastAPI apps (a test regenerates and diffs it), `docs/api/asyncapi.yaml` describing the MQTT topics, payloads, QoS and the `replayed`/status messages (a test checks it parses and that topic names match the code), C4-style context/container diagrams in Mermaid in `docs/production-topology.md`. `docs/security.md` maps the controls to the OWASP ASVS and API Top 10 items actually checked.
- **Regulatory/engineering pack in `docs/regulatory.md`:** intended-purpose statement, IEC 62304-style software description, SOUP/licence list (from `docs/licences.md`), ISO 14971-style hazard analysis (missed alert, stale data, duplicate data, wrong node/subject, clock error, outage), requirements ↔ tests traceability table, IEC 81001-5-1-style security notes (threat model, SBOM, patching), with "designed with reference to" wording only.
- Final verification: `uv run pytest -q` from a clean `uv sync --frozen`; `git log --format=%B | grep -inE 'co-authored-by|generated with'` is empty; `git status` shows no keys, `.db`, `.env`, `.tfstate` or `roles`; `docker compose config` for the default, `cloud`, `platform`, `identity`, `triage`, `ehr` combinations; default `docker compose up -d` still yields the original 4 containers; UI wording grep; every ADR has its four sections; the file index is complete.
- Write a short plain-English hand-off (section 10).

**CHECKPOINT 8.** Final review with Byron; list what remains a documented gap.

---

## 10. Working rules and definition of done

- **One question at a time, via AskUserQuestion.** Stop-and-ask gates: before touching the baseline; at every checkpoint; before any firmware build/flash; before the Oracle sign-up and the card; before choosing the hostname route; before enabling the outbox by default; before switching the cloud profile away from the Influx reference path; before any iOS compromise; whether the repo may be public; and whenever a verified fact in this prompt turns out to be wrong.
- **Prime directives:** zero silent failures (every failure mode is visible and named); every error has a name and a user-visible effect; every new data flow has a nil/empty/upstream-error path with a test; interactions have edge cases (double-click, navigate-away, stale state); observability is scope, not an afterthought; anything deferred is written down (ADR or `docs/progress.md`), not left as a vague intention.
- **Honesty over polish.** Report what was measured, what was inferred, and what was not verified. Do not call anything "compliant", "secure" or "production-ready". Say "tamper-evident", "optional flag", "design-only", "documented gap".
- **Do not run** destructive commands (`rm -rf`, `docker system prune`, volume deletion), restart Docker Desktop, push, or touch cloud resources.
- **Verify before relying:** items this plan marks **unverified** (Mosquitto `crlfile`/WSS behaviour, HAPI R5 SampledData support, JetStream duplicate window, iOS free-team capabilities and background behaviour, CocoaMQTT client identities, carrier port blocking, GHCR/CodeQL on private repos, Oracle region capacity and PAYG behaviour) must be checked and the result recorded in the relevant ADR before building on them.
- **Done means:** all Tier A units merged on the feature branch with tests green; Tier B/C either done or recorded in `docs/progress.md` as deferred with a reason; baseline numbers untouched; Byron has a runbook he can follow on his own; the final message lists (a) what changed, (b) what was measured, (c) what remains a documented gap or design-only, (d) the exact commands to reproduce each new result.

---

## 10b. Session-limit protocol and the resume pin (important)

Claude's 5-hour usage window can end in the middle of a unit. Because a half-applied change (a migration, a flash, a compose edit) is worse than no change, follow this protocol every session.

**Watch the limit.** Use whatever usage indicator the session provides (for example a usage command or status line, if available). If none is visible, ask Byron at the start of the session and at every unit boundary how much of the window is left, and checkpoint at every unit boundary regardless.

**Thresholds**
- **About 80 %:** do not start a new unit, a hardware step (flash, pairing, iPhone install), a long run (soak, load test, restore drill) or any change that needs more than a few minutes to finish cleanly. Finish the current small step and tests.
- **About 95 %: wrap up now.** Stop starting new work and do the wrap-up below. If you cannot see the usage, treat a warning from the platform as 95 %.

**Wrap-up (do these in order, keep each short)**
1. **Reach a safe state.** Finish or cleanly revert the in-flight edit. Never stop in the middle of: a database migration (run it to completion or roll it back), a firmware build/flash (do not start one past 80 %), a `docker compose` change (leave the stack in a state `docker compose config` accepts), a parity or measurement run (let it finish if it will complete in minutes, otherwise stop it and record that the run is invalid and must restart from scratch), or anything touching the baseline tag. Do not start, stop or restart Docker Desktop. Cloud steps are Byron's: list the exact command he still has to run, do not leave a half-applied `terraform` plan.
2. **Run the fast checks:** `uv run pytest -q` (record pass/fail and any skipped group). If red, either fix it if it is quick or record exactly which test fails and why.
3. **Commit.** Commit on `feat/enterprise-topology` with a normal message (use a `wip(unit-N):` prefix if the unit is unfinished; **no AI trailer**). Never leave important work only in the working tree. Never commit secrets (check `git status` first).
4. **Write the pin: overwrite `docs/handoff.md`** (commit it too) using this template, with real values, not placeholders:

```markdown
# Handoff pin (written <ISO date/time, Sydney>)

## Resume from
- Unit: <e.g. Unit 6, step "ts-writer idempotency tests">   <- exactly one place to restart
- Phase / checkpoint status: <e.g. Phase 4; Checkpoint 3 passed on <date>; Checkpoint 4 NOT yet asked>
- Branch / last commit: <branch> @ <short hash>  (<subject line>)

## State of the repo
- Tests: <pass/fail counts; failing test names>
- Uncommitted changes: <none, or list>  (should be none)
- Files created in the current unit: <list>   Files still to create: <list>
- Docker / compose: <stack up or down; which profiles; ports in use>
- Background processes or runs: <PID, command, log path, expected finish, or "none">
- Database / migration state: <revision applied>

## Decisions made this session (not yet in an ADR or progress.md)
- <bullets>

## Open questions waiting for Byron
- <exact question and the options already offered; or "none">

## Byron's pending manual steps
- <exact commands for him to run (terraform, Xcode install, flash, ...) and what output to paste back; or "none">

## Next three actions
1. <specific command or edit>
2. ...
3. ...

## Warnings / traps
- <anything that will bite the next session: stale bonds, ports, clock offsets, unverified facts still to check>
```

5. **Update `docs/progress.md`** with a dated entry (what was done, what was measured, what is unverified). The handoff pin says where to resume; progress.md records the history. Do not duplicate long text between them.
6. **Tell Byron in one short message:** where the pin is, the unit and step to resume from, and any manual action he must take before resuming.

**Resuming (first thing in a new session, including after the limit resets)**
1. Read `docs/handoff.md`, then `docs/progress.md` (latest entry).
2. Verify the pin: `git status` is clean, the branch and last commit match, `uv run pytest -q` result matches the pin. If anything differs (someone edited files, a run finished or failed), **say so and reconcile before continuing**; the repo and the real state win over the pin.
3. Re-check anything the pin lists under background processes (is it still running? did it finish? is its output valid?).
4. Answer or re-ask any "open questions waiting for Byron" first; do not guess.
5. Do the "Next three actions". At the next unit boundary update the pin even if the limit is far away.

**Pin hygiene:** one pin file, always overwritten, never a history of pins. When a unit is complete, the pin's "Resume from" moves to the next unit's first step. When the whole plan is complete, set it to "Plan complete; see docs/progress.md".

---

## Appendix A: the original research, preserved and mapped to this repo

**Reference model (generic production RPM).** *Tier 1* sensor node: biometric acquisition on an nRF52840-class SoC (Cortex-M4F 64 MHz, 1 MB flash, 256 KB RAM, CryptoCell-310), BLE peripheral with compact binary GATT characteristics, micro-ampere budgets, optional on-device preprocessing. *Tier 2* gateway: phone app or cellular/Wi-Fi hub acting as BLE central, decodes binary payloads to ISO-8601-stamped JSON, store-and-forward with encrypted local storage and exponential backoff, isolates the sensor from WAN security. *Tier 3* cloud: broker (AWS IoT Core / EMQX / Mosquitto) over MQTT/TLS, stream processing, time-series + relational stores, complex-event processing for alerts, FHIR integration bridge to EHRs. *Tier 4* clinician portal: severity-ranked triage worklist, 7-day inline sparklines, longitudinal charts (24 h / 7 d / 30 d / 90 d) with threshold bands, tracked alert workflow with immutable audit, EHR context panels.

**Prototype vs enterprise comparison, mapped to this repo:**

| Dimension | Prototype topology (this repo today) | Enterprise reference | After this work |
|---|---|---|---|
| Objective | Measure latency/delivery/fidelity of an EHG telemetry chain | Scalable remote patient care | Same objective; the production layers are added as measured, optional profiles |
| Data path | Replay → DK → BLE → Bleak laptop gateway → MQTT → ingest → InfluxDB → Grafana/viewer | Sensor → BLE → mobile gateway → cellular/MQTT → cloud → EHR/dashboard | Same path with a native iOS gateway on cellular, mTLS MQTT, JetStream, TimescaleDB, HAPI FHIR (EHR), triage page |
| Coverage | BLE ~10 m; gateway on a laptop | Cellular/Wi-Fi WAN | iPhone cellular + Oracle VM over the internet (LTE-M/NB-IoT modem not built) |
| Resilience | Board history + RESEND; in-memory MQTT queue; broker persistent session | Persistent store-and-forward, HA | Durable outbox (Python + iOS), JetStream redelivery, idempotent writes, backups + restore drill; HA design-only |
| Ingestion | Mosquitto anonymous 1883 | Clustered broker, mTLS | mTLS 8883 + ACLs + short-lived certs + CRL; clustering design-only |
| Storage | InfluxDB 2.7 | TimescaleDB + relational | TimescaleDB + Postgres (registry, alerts, audit) with parity to InfluxDB; Influx stays the reference |
| Identity & security | CRC only; dev MCUboot key | LESC, mTLS 1.3, X.509, OIDC, RBAC, WORM | LESC (flag), mTLS 1.3, X.509 lifecycle, Keycloak OIDC + MFA + RBAC, hash-chained audit (not WORM) |
| EHR | FHIR R5 batch export only | FHIR write-back to EHR | Live write-back into a self-hosted HAPI FHIR R5 server (reference EHR) |
| UI | Grafana + raw viewer | Severity worklist, sparklines, alert workflow, EHR panel | Triage page (pipeline health) + HAPI-backed EHR panel |
| Operations | Manual `docker compose` on a Mac | IaC, observability, DR, SLOs | Terraform + cloud-init, Prometheus/Alertmanager/Loki/Grafana, SLOs, measured RPO/RTO |
| Compliance | Unregulated prototype | SaMD, HIPAA, GDPR | Intended-purpose statement; AU framing; standards "designed with reference to"; no compliance claims |

**Original master-prompt requirements and their disposition:**

| Original requirement | Disposition |
|---|---|
| Refactor into 4 tiers; `docs/ARCHITECTURE.md` | `docs/production-topology.md` (Unit 1) |
| `docs/SECURITY.md` with LESC, mTLS 1.3, X.509, OAuth2/OIDC, RBAC, WORM | `docs/security.md` (Units 2, 7, 11, 13, 17); WORM stays a documented gap |
| `/sensor`: peripheral with GATT for ECG, HR, SpO2, BP; synthetic generator | **Dropped**: EHG replay stays; the firmware is already a BLE peripheral with the custom EHG service |
| `/gateway` Node/Python: bonded connections, decode to JSON, SQLite store-and-forward with backoff, MQTTS | Python gateway (Units 2, 3, 13) **and** native iOS gateway (Unit 15) |
| `/backend`: MQTT ingestion, TimescaleDB migrations, CEP engine, `fhirTransformer.ts` | Mosquitto + bridge + NATS (Unit 6), TimescaleDB migrations (Unit 6), rules engine (Unit 8), Python FHIR write-back to HAPI (Unit 9). TypeScript dropped |
| CEP: SpO2 < 90, HR > 120, SBP > 140 | Replaced by pipeline/signal-health rules (section 6) |
| FHIR STU3/R4 Observation mapping | Stay on R5 SampledData; live write-back and facade (Unit 9) |
| `/frontend` React/Next.js: ranked worklist, sparklines, trends, alert modal, EHR panel with mock notes/meds | Vanilla JS page (Unit 10); mock clinical notes dropped; EHR panel backed by real HAPI data |
| Updated `README.md`, `docker-compose.yml` for all tiers | README + compose profiles + IaC (Units 4, 11, 17) |
| Integration tests: end-to-end, offline buffering, FHIR compliance | Unit tests in CI; opt-in `EHG_INTEGRATION=1` end-to-end, outage-recovery, parity and failure drills; FHIR structural validation (conformance not claimed) |
| "Begin by rendering the complete file tree" | Not needed. Each unit lists its files; Unit 17 updates the file index |

**Security controls from the research and their status:** see section 7. Items marked "documented gap" or "design-only" are intentionally out of reach on free services or unnecessary for a replay rig, and each must be stated as such, not implied.

## Appendix B: sources for the facts used

Oracle Always Free resources (docs.oracle.com, page modified 2026-06-12) and Free Tier FAQ; InfoQ/Linuxiac/Site2US/fullmetalbrackets on the June 2026 A1 cut (secondary); AWS Free Tier "What's New" (July 2025: $200 credits, 6-month plan) and AWS IoT Core pricing; Google Cloud and Azure free-tier summaries (agentdeals.dev, secondary, 3 Nov 2026); EMQX licence FAQ and BSL announcement; Redpanda licensing docs; Tiger Data TimescaleDB editions docs; Keycloak releases, release notes and memory/CPU sizing guide; HAPI FHIR JPA server starter (`application.yaml`, README) and Docker Hub tags; NATS sizing and installation docs; Docker Hub / docker-library manifests for arm64 availability (hapiproject/hapi, keycloak, timescaledb-ha, eclipse-mosquitto, nats, caddy, prometheus, loki, grafana); Cloudflare Tunnel protocol docs; DuckDNS spec; Tailscale pricing (secondary); Apple "Compare memberships" and Core Bluetooth background-processing docs; CocoaMQTT README; GitHub Actions billing docs; Spacelift and HashiCorp on HCP Terraform Free and the BSL; Bleak macOS backend docs and issue #827; Apple Developer Forums threads 772541, 669229, 729388, 810872; Zephyr `bt_conn_auth_cb` docs, advisory GHSA-p6f3-f63q-5mc2, issue #49599; Mosquitto `mosquitto.conf(5)`; paho-mqtt 2.x docs; `fhir.resources` on PyPI; HL7 FHIR R5 "differences from R4"; TGA CDSS and general-wellness exclusion guidance (updated 16 Oct 2026 and 16 Mar 2026); OAIC "what is personal information"; NSW HRIP Act 2002 s 6 and NSW Health Privacy Manual; PhysioNet licence pages.

**Confidence notes.** The macOS/iOS pairing-trigger behaviour is inferred. Unverified (check and record before relying): Oracle PAYG–reclamation exemption and A1 capacity in Sydney/Melbourne; arm64 for the non-HA `timescale/timescaledb` image; HAPI memory guidance and R5 SampledData behaviour; Mosquitto `crlfile`, WebSocket and JWT-plugin behaviour; JetStream duplicate-window details; iOS free-team capability table, locked-screen BLE + MQTT behaviour, CocoaMQTT client-identity support and Secure Enclave identities; carrier blocking of port 8883; DuckDNS reliability; GHCR/CodeQL on private repos; licence texts for Trivy/Syft/cosign/step-ca/pgBackRest/MQTT-NIO; step-ca arm64 image, revocation and Keycloak-token support; pgBackRest with the TimescaleDB image; edition/date details of IEC 62304, IEC 81001-5-1, ISO 14971, AU Core and IHE/PCHAlliance documents; hyperscaler RPM reference architectures were not read.
