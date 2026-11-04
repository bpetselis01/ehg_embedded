# Codebase guide: how the EHG pipeline fits together

This is a plain-language map of the code. Every code file also starts with a short header covering
**Purpose / Input / Output / How / Role in thesis / Before → After**, and each function has a one-line note. Read this guide
first for the big picture, then open any file.

## Other docs

Current reference docs describe the code as it is now and are kept in step with it (`tests/test_docs.py` checks links,
the file index and the duplicated facts). Historical records are dated and describe what was planned or found at the time.

| Doc | Read it for | Status |
|---|---|---|
| [README.md](../README.md) | setup, the commands to run everything, reproducing the results | current |
| [data/tpehgdb.md](data/tpehgdb.md), [tpehgt](data/tpehgt.md), [ehgdb](data/ehgdb.md), [icehg](data/icehg.md) | what is inside the data folders (`.hea`, `.dat`, `.npz`, `manifest.json`), with example records | current |
| [firmware.md](firmware.md) | the board's code: role of each file, threads, settings, serial log, `build/` output, glossary | current |
| [interface-spec.md](interface-spec.md) | exact formats: `.npz`, packet bytes, MQTT message, database schema, FHIR export | current |
| [fault-injection.md](fault-injection.md) | the `--faults` options and what each one simulates | current |
| [troubleshooting.md](troubleshooting.md) | known failures: symptom, cause, fix | current |
| [decisions/](decisions/) | why the main design choices were made (one short record each; 0007 to 0017 accepted at Checkpoint 1, 2026-11-03; most are not built yet) | current |
| [security.md](security.md) | threat model and controls table (status per control); what was tested for the TLS cloud profile and what was not | current (grows per unit) |
| [deployment.md](deployment.md) | click-by-click runbook for the Oracle VM (you run it), the recorded decisions, and a log of what you actually saw | plan; log empty until you run it |
| [supply-chain.md](supply-chain.md) | what guards the code and images (in place vs planned), local scanner commands, vulnerability handling | current |
| [production-topology.md](production-topology.md) | the planned enterprise-style chain, hop by hop, with what is built, planned or design-only | plan (2026-11-03) |
| [licences.md](licences.md) | licence, edition and status (confirmed or UNVERIFIED) of every component used or considered | current |
| [claude-code-prompt-thesis-b-hardening.md](claude-code-prompt-thesis-b-hardening.md) | the full specification for the enterprise-style evolution (units, checkpoints, rules) | plan (2026-11-03) |
| [handoff.md](handoff.md) | the resume pin between working sessions (one file, always overwritten) | working note |
| [progress.md](progress.md) | status log, results per run, blockers, next steps | dated log |
| [research/nrf-ble-environment.md](research/nrf-ble-environment.md) | nRF / Bluetooth / macOS setup notes with sources | historical (2026-09/10) |
| [design/codebase-design.md](design/codebase-design.md) | the original design behind this layout | historical (2026-09-29) |
| [plans/](plans/), [sessions/](sessions/) | the plan and the Q&A record for each piece of work | historical |
| [../papers/INDEX.md](../papers/INDEX.md) | the papers cited in the report | current |

## The one-sentence version

A real pregnancy EHG recording is played back by a small board as if it were a live sensor, sent over
Bluetooth to a laptop, then passed on to a database and dashboards. Every sample carries a number and a timestamp,
so we can measure **how fast** it arrived (H1), **whether** it arrived (H2), and **whether it is unchanged** (H4).

## The journey of one sample

```mermaid
flowchart TD
    subgraph PREP["1. Prepare data (laptop, offline)"]
        A["PhysioNet files"] -->|tools/convert.py| B["data/converted/*.npz<br/>+ manifest.json"]
        B -->|tools/feed.py, serial| C["board queue"]
    end

    subgraph BOARD["2. nRF52840 board (firmware/)"]
        D["replay.c<br/>fake sensor"] -->|ring buffer| E["ehg_service.c<br/>packets + resend history"]
    end

    subgraph LAPTOP["3. Gateway (laptop)"]
        F["gateway.py<br/>+ packet.py, tracking.py"]
    end

    subgraph BACKEND["4. Backend (Docker)"]
        G["Mosquitto broker"]
        H["ingest.py"]
        I[("InfluxDB")]
        J["Grafana dashboard"]
        K["viewer/server.py<br/>live traces"]
    end

    subgraph EVAL["5. Measure"]
        L["eval/latency.py (H1)<br/>eval/integrity.py (H2, H4)"]
    end

    C -->|compiled in| D
    E -->|"Bluetooth (BLE)"| F
    F -.->|RESEND requests| E
    F -->|"MQTT ehg/&lt;node&gt;/pkt"| G
    G --> H
    G --> K
    H --> I
    I --> J
    I --> L
    B -->|answer key| L
```

Step by step:

1. **Prepare the data** ([`tools/convert.py`](../tools/convert.py)). Turns downloaded recordings into simple `.npz` files of untouched
   whole numbers, plus a `manifest.json` that gives each recording a number (`record_id`).
2. **Stream one recording into the board** ([`tools/feed.py`](../tools/feed.py)). Sends the samples over the serial port a
   couple of seconds ahead; the firmware is built once ([`firmware/build.sh`](../firmware/build.sh)) and holds no recording.
   Alternative for stand-alone runs: `firmware/build.sh flash --record <npz>` compiles one recording in
   ([`tools/make_replay.py`](../tools/make_replay.py)), so no laptop feed is needed ([decision 0003](decisions/0003-built-in-recording-build-time-option.md)).
3. **Play it like a sensor** ([`firmware/ehg_node/src/replay.c`](../firmware/ehg_node/src/replay.c)). Releases one frame at a time at the original rate
   (for example 20 per second) and stamps each frame with the board's clock. A *frame* is one sample from every channel at one instant.
4. **Pack and send** ([`firmware/ehg_node/src/ehg_service.c`](../firmware/ehg_node/src/ehg_service.c)). Groups 10 frames into one numbered packet,
   keeps a copy, and sends the packet over Bluetooth. If the laptop says a packet went missing, it sends the copy again.
   [`main.c`](../firmware/ehg_node/src/main.c) handles start-up and making the Bluetooth connection.
5. **Receive on the laptop** ([`gateway/gateway.py`](../gateway/gateway.py)). Decodes each packet ([`packet.py`](../gateway/packet.py)) and spots missing
   packet numbers ([`tracking.py`](../gateway/tracking.py)), then asks for those again. It converts the board's clock to real time and publishes the
   packet as a JSON message over MQTT. `--simulate` runs the same steps with no board.
6. **Store** ([`backend/ingest/ingest.py`](../backend/ingest/ingest.py)). Writes each frame to InfluxDB and records the time the
   database confirmed the write. That confirmation is the finish line for latency.
7. **Look at it**: the Grafana dashboard (overview, latency, packet rate) and [`viewer/`](../viewer) (live raw waves).
8. **Measure**: [`eval/latency.py`](../eval/latency.py) (H1) and [`eval/integrity.py`](../eval/integrity.py) (H2 and bit-exact H4). Both read the database
   through [`eval/backend_client.py`](../eval/backend_client.py) and compare against the files from step 1.
9. **Hand off** ([`tools/fhir_export.py`](../tools/fhir_export.py)): a stored window as an HL7 FHIR R5 Bundle (raw counts + mV scaling) for clinical systems.

## Where each hypothesis lives

| Hypothesis | What it asks | Produced by | Measured by |
|---|---|---|---|
| H1 latency | 95 % of samples stored within 2 s (stretch: 1 s) | timestamps at [`replay.c`](../firmware/ehg_node/src/replay.c) → [`gateway.py`](../gateway/gateway.py) → [`ingest.py`](../backend/ingest/ingest.py) | [`eval/latency.py`](../eval/latency.py) |
| H2 delivery | more than 99 % arrive, and every loss can be identified | packet numbers + history/resend in [`ehg_service.c`](../firmware/ehg_node/src/ehg_service.c), gap tracking in [`tracking.py`](../gateway/tracking.py), broker queue | [`eval/integrity.py`](../eval/integrity.py) |
| H3 many nodes | at least 8 boards at once | unique names ([`main.c`](../firmware/ehg_node/src/main.c)), `--nodes N` / `--simulate N` ([`gateway.py`](../gateway/gateway.py)) | gateway stats (needs more hardware) |
| H4 fidelity | stored numbers identical to the source | raw numbers end to end ([`convert.py`](../tools/convert.py) → … → [`ingest.py`](../backend/ingest/ingest.py)) | [`eval/integrity.py`](../eval/integrity.py) (mismatches) |

## Timestamps along the way (H1)

| Name | Taken where | Meaning |
|---|---|---|
| `t_inject` | [`replay.c`](../firmware/ehg_node/src/replay.c) | board clock tick when the packet's first frame was "sampled" |
| `t_inject_host` | [`gateway.py`](../gateway/gateway.py) (via [`tracking.py:ClockMap`](../gateway/tracking.py)) | the same moment, converted to laptop time |
| `t_gw_rx` | [`gateway.py`](../gateway/gateway.py) | packet arrived at the laptop |
| `t_mqtt_pub` | [`gateway.py`](../gateway/gateway.py) | packet handed to the MQTT broker |
| `t_ingest_rx` | [`ingest.py`](../backend/ingest/ingest.py) | ingest service received it |
| `t_db_ack` | [`ingest.py`](../backend/ingest/ingest.py) | database confirmed the samples are stored (finish line) |

## File index

| File | One-line role |
|---|---|
| [`tools/convert.py`](../tools/convert.py) | downloaded recordings → `.npz` + `manifest.json` (the answer key) |
| [`tools/feed.py`](../tools/feed.py) | streams one `.npz` into the board over the serial port (digital injection); `--sync-only` = clock pings for a built-in board |
| [`tools/make_replay.py`](../tools/make_replay.py) | one `.npz` → `replay_data.h`, compiled in by `firmware/build.sh --record` (built-in mode, stand-alone runs) |
| [`tools/fhir_export.py`](../tools/fhir_export.py) | stored window → HL7 FHIR R5 Bundle (clinical hand-off) |
| [`tools/watch_db.py`](../tools/watch_db.py) | live list of packets as InfluxDB stores them, with end-to-end time (demos) |
| [`tools/demo.sh`](../tools/demo.sh) | starts the whole pipeline in 6 tiled windows, one per hop (demos) |
| [`firmware/build.sh`](../firmware/build.sh) | build and flash the board (works around spaces in this folder's path). Overview of all firmware: [firmware.md](firmware.md) |
| [`firmware/ehg_node/CMakeLists.txt`](../firmware/ehg_node/CMakeLists.txt) | which C files to compile |
| [`tools/gen_acl.py`](../tools/gen_acl.py) | writes the Mosquitto ACL that pins each gateway to its own nodes (`deploy/acl/gateways.json` or the registry); tests in `tests/test_gen_acl.py` and, with a real broker, `tests/test_mqtt_tls_config.py` |
| [`firmware/ehg_node/Kconfig`](../firmware/ehg_node/Kconfig) | our tunable settings (frames per packet, buffer sizes, `EHG_REQUIRE_PAIRING`) |
| [`firmware/ehg_node/security.conf`](../firmware/ehg_node/security.conf) | overlay of the SECURE build (`build.sh --secure`): LESC passkey pairing, bonds in flash, protected OTA service |
| [`firmware/gen_signing_key.sh`](../firmware/gen_signing_key.sh) | makes the ECDSA P-256 MCUboot signing key OUTSIDE the repository (`$EHG_MCUBOOT_KEY`) |
| [`tests/test_firmware_security.py`](../tests/test_firmware_security.py) | static guard: default build stays open, the secure overlay keeps its protections, no key in git |
| [`firmware/ehg_node/prj.conf`](../firmware/ehg_node/prj.conf) | Bluetooth and logging settings |
| [`firmware/ehg_node/src/main.c`](../firmware/ehg_node/src/main.c) | start-up, advertising, connection events, stats every 10 s |
| `firmware/ehg_node/src/replay.c/.h` | fake sensor: timed frame release into the ring buffer |
| `firmware/ehg_node/src/ehg_service.c/.h` | Bluetooth service, packet building, history, resend |
| [`firmware/ehg_node/src/ehg_packet.h`](../firmware/ehg_node/src/ehg_packet.h) | packet byte layout (copy 1 of 2) |
| [`gateway/packet.py`](../gateway/packet.py) | packet byte layout (copy 2 of 2) |
| [`gateway/tracking.py`](../gateway/tracking.py) | missing-packet tracker and clock converter |
| [`gateway/gateway.py`](../gateway/gateway.py) | Bluetooth (or simulated) → MQTT bridge |
| [`gateway/outbox.py`](../gateway/outbox.py) | on-disk queue, on by default (`--no-outbox` to disable): a packet is deleted only when the broker confirms it |
| [`backend/docker-compose.yml`](../backend/docker-compose.yml) | starts broker, database, ingest and Grafana |
| [`backend/docker-compose.cloud.yml`](../backend/docker-compose.cloud.yml) | optional override: broker on 8883 with TLS 1.3 and client certificates, ingest presents a certificate (no 1883) |
| [`backend/mosquitto/mosquitto.tls.conf`](../backend/mosquitto/mosquitto.tls.conf), [`acl`](../backend/mosquitto/acl) | the TLS broker settings and the per-identity topic permissions used by the override |
| [`tools/pki.sh`](../tools/pki.sh) | makes the private CA, broker and client certificates and the revocation list (`init`, `client`, `revoke`, `crl`) |
| [`deploy/README.md`](../deploy/README.md) | the cloud core as code: Terraform, cloud-init, compose, secrets; what is checked and what is not |
| [`deploy/update.sh`](../deploy/update.sh) | idempotent update of the VM to a tagged release with health check and rollback (`--dry-run` shows the steps) |
| [`deploy/postgres/10-init-databases.sh`](../deploy/postgres/10-init-databases.sh) | first start of PostgreSQL: creates the databases and login roles |
| [`tests/test_deploy_assets.py`](../tests/test_deploy_assets.py) | checks Terraform, cloud-init, compose, `update.sh` and that no secret or state can reach git |
| [`tests/test_mqtt_tls_config.py`](../tests/test_mqtt_tls_config.py) | checks the TLS config, ACL, compose merge, PKI and (opt-in) a real local Mosquitto |
| [`backend/bus.py`](../backend/bus.py) | the NATS JetStream layout: stream, subjects, message ids, consumer settings |
| [`backend/mqtt_env.py`](../backend/mqtt_env.py) | MQTT connection settings from environment variables, for services built from the repo root |
| [`backend/bridge/bridge.py`](../backend/bridge/bridge.py) | MQTT -> JetStream; acknowledges the broker only after the stream has the message |
| [`backend/ts_writer/writer.py`](../backend/ts_writer/writer.py) | JetStream -> PostgreSQL/TimescaleDB, idempotent, atomic batches, reports "degraded" when the database is down |
| [`backend/db/migrate.py`](../backend/db/migrate.py) | applies or undoes the database migrations (Alembic) |
| [`backend/db/migrations/env.py`](../backend/db/migrations/env.py), [`versions/0001_base_schema.py`](../backend/db/migrations/versions/0001_base_schema.py), [`versions/0002_triage_registry.py`](../backend/db/migrations/versions/0002_triage_registry.py), [`versions/0003_timescale.py`](../backend/db/migrations/versions/0003_timescale.py) | Alembic environment; plain time-series tables; alerts, append-only audit log and device/subject registry; TimescaleDB hypertables, compression, retention, roll-ups |
| [`backend/enroll/app.py`](../backend/enroll/app.py) | the enrolment API: token + certificate request in, short-lived client certificate out; revocation; public CRL |
| [`backend/enroll/csr.py`](../backend/enroll/csr.py) | vets certificate requests (EC P-256, valid signature, no wildcard or CA request) and re-checks what the CA issued |
| [`backend/enroll/stepca_client.py`](../backend/enroll/stepca_client.py) | talks to step-ca with one-time tokens: sign and revoke |
| [`backend/enroll/cli.py`](../backend/enroll/cli.py) | `enrol-mac`, `list`, `revoke` from the command line (token via environment, key made locally) |
| [`deploy/step-ca/README.md`](../deploy/step-ca/README.md), [`deploy/keycloak/README.md`](../deploy/keycloak/README.md) | what the CA template and the realm file assume, and what is not verified |
| [`deploy/keycloak/realm-ehg.json`](../deploy/keycloak/realm-ehg.json) | the Keycloak realm as code: roles, clients (PKCE, device grant), TOTP for staff; no secrets, no users |
| [`backend/triage/stream.py`](../backend/triage/stream.py) | per-node measurement from the packet stream, the once-a-second rule evaluation, health rows, and the service entry point |
| [`backend/triage/app.py`](../backend/triage/app.py) | the triage HTTP API: worklist, trends, alert workflow, registry, audit, config; role-checked and audited |
| [`backend/triage/registry.py`](../backend/triage/registry.py) | gateways, nodes, pseudonymous subjects with consent, and the health history behind the trends |
| [`backend/triage/audit.py`](../backend/triage/audit.py) | hash-chained append-only audit log and its verifier (`--verify`); tamper-evident, not write-once |
| [`backend/triage/state.py`](../backend/triage/state.py) | alert lifecycle in PostgreSQL (open, acknowledged, escalated, resolved), required notes, auto-resolve, audit in the same transaction |
| [`viewer/triage.html`](../viewer/triage.html), [`viewer/triage.css`](../viewer/triage.css), [`viewer/triage.js`](../viewer/triage.js) | the triage page: sign-in (OIDC code flow with PKCE), worklist, node detail (trend, alerts, EHR), alert actions; served by the triage API at `/` |
| [`backend/fhir_sync/worker.py`](../backend/fhir_sync/worker.py) | copies complete stored windows into the reference FHIR server (HAPI) as conditional, idempotent transactions, only for nodes with active consent |
| [`tools/export_api_docs.py`](../tools/export_api_docs.py) | writes `docs/api/openapi-triage.json` and `openapi-enroll.json` from the FastAPI apps; `--check` says if they are stale |
| [`docs/api/asyncapi.yaml`](api/asyncapi.yaml) | the MQTT topics, payload keys and NATS subjects (checked against the code by `tests/test_api_docs.py`) |
| [`docs/regulatory.md`](regulatory.md), [`docs/slo.md`](slo.md), [`docs/dr.md`](dr.md) | regulatory framing and hazard table (no compliance claim), service-level objectives, backups and failure drills |
| [`tools/gen_packet_vectors.py`](../tools/gen_packet_vectors.py) | writes the golden wire-format, gap-tracker and clock-map vectors (`tests/fixtures/packet_vectors.json`) from `gateway/packet.py`; `--check` says if they are stale |
| [`ios/EHGWire/`](../ios/EHGWire/Package.swift) | Swift package: wire v2 decoder and encoder (`Sources/EHGWire/Packet.swift`), `GapTracker` and `ClockMap` ports (`Tracking.swift`) and tests that replay the golden vectors; command line only, no app, no Xcode project |
| [`backend/enroll/service.py`](../backend/enroll/service.py) | starts the enrolment API from environment variables; refuses unsafe settings; `/crl` asks the CA (never serves an empty list) |
| [`deploy/crl-sync.sh`](../deploy/crl-sync.sh) | cron job on the VM: fetches the CRL, verifies it (signature, expiry, not older), installs it into the broker's file and signals Mosquitto |
| [`backend/triage/slis.py`](../backend/triage/slis.py) | fleet-level gauges for the service-level objectives and the bus consumers' backlog (Prometheus) |
| [`deploy/backup/backup.sh`](../deploy/backup/backup.sh), [`deploy/backup/restore_drill.sh`](../deploy/backup/restore_drill.sh) | database backup with checksums, audit-chain head and a freshness metric; the timed restore drill on a clean server |
| [`deploy/observability/rules/slo.yml`](../deploy/observability/rules/slo.yml), [`alerts.yml`](../deploy/observability/rules/alerts.yml) | SLO recording rules and 12 alert rules (never evaluated by Prometheus) |
| [`deploy/observability/grafana/dashboards/pipeline-overview.json`](../deploy/observability/grafana/dashboards/pipeline-overview.json) | the one provisioned dashboard (read-only, as code) |
| [`backend/triage/fhir_api.py`](../backend/triage/fhir_api.py) | the read-only FHIR facade (`/fhir/*`): node-scoped, consent-checked, audited; the browser never reaches HAPI |
| [`deploy/hapi/application.yaml`](../deploy/hapi/application.yaml) | HAPI FHIR R5 configuration (no CORS, no Swagger, no mass delete); never started |
| [`backend/jwtauth.py`](../backend/jwtauth.py) | JWT validation (explicit algorithms, required claims), role mapping and the dev mode (loopback only) |
| [`backend/triage/rules.py`](../backend/triage/rules.py) | pipeline-health alert rules, hysteresis, severity score and worklist order (pure functions; nothing clinical) |
| [`backend/platform.Dockerfile`](../backend/platform.Dockerfile), [`backend/docker-compose.platform.yml`](../backend/docker-compose.platform.yml) | the platform services' image and the compose override (profile `platform`) |
| [`eval/parity.py`](../eval/parity.py) | compares InfluxDB and TimescaleDB for the same run; fails if they differ |
| [`tests/conftest.py`](../tests/conftest.py) | a throwaway local PostgreSQL for the tests that need real SQL |
| [`backend/.env.example`](../backend/.env.example) | shared names and passwords (copy to `.env`) |
| [`backend/mosquitto/mosquitto.conf`](../backend/mosquitto/mosquitto.conf) | broker settings (keeps a queue on disk) |
| [`backend/ingest/ingest.py`](../backend/ingest/ingest.py) + `Dockerfile` | MQTT → InfluxDB writer |
| `backend/grafana/provisioning/*.yml` | auto-connect Grafana to InfluxDB and load the dashboard |
| [`backend/grafana/dashboards/ehg-overview.json`](../backend/grafana/dashboards/ehg-overview.json) | the dashboard (see below) |
| [`viewer/server.py`](../viewer/server.py) + [`index.html`](../viewer/index.html) | live raw-wave page |
| [`eval/backend_client.py`](../eval/backend_client.py) | shared database queries |
| [`eval/latency.py`](../eval/latency.py) | H1 numbers and CDF plot |
| [`eval/integrity.py`](../eval/integrity.py) | H2 delivery and H4 bit-exact numbers |
| `tests/*.py` | automatic checks for the pure-logic parts, and [`test_docs.py`](../tests/test_docs.py): the docs and the two wire-format copies still match the code (`uv run pytest -q`, also on every push: [`.github/workflows/tests.yml`](../.github/workflows/tests.yml)) |

## The Grafana dashboard ([`ehg-overview.json`](../backend/grafana/dashboards/ehg-overview.json))

JSON files can't hold comments, so here is what the dashboard contains. A **node** drop-down at the top picks which boards to show. It has four panels:

1. **Raw EHG (ADC units)**: the stored samples (`ehg_raw`, fields `c0…cN`), one line per channel.
2. **End-to-end latency per packet**: `t_db_ack − t_inject_host` for each non-resent packet. This is a quick live
   view of H1; [`eval/latency.py`](../eval/latency.py) gives the corrected numbers.
3. **Packets per second**: should stay flat at 2/s for TPEHG (20 Hz ÷ 10 frames). Dips mean loss or stalls.
4. **Retransmitted packets per minute**: how often loss recovery is working (H2).

## The two copies of the packet format

[`firmware/ehg_node/src/ehg_packet.h`](../firmware/ehg_node/src/ehg_packet.h) (C, on the board) and [`gateway/packet.py`](../gateway/packet.py) (Python, on the laptop) describe the
same 20-byte header. If one changes, change the other and [`docs/interface-spec.md`](interface-spec.md) with it. [`tests/test_packet.py`](../tests/test_packet.py)
checks that the Python side is self-consistent, and the C side checks its size at build time.

## Glossary

- **ADC units**: the raw whole numbers a sensor's analogue-to-digital converter produces. To get millivolts,
  compute (value − baseline) ÷ gain. We keep the raw numbers so we can compare them exactly.
- **BLE (Bluetooth Low Energy)**: the low-power radio link between board and laptop.
- **GATT service / characteristic**: Bluetooth's way of naming data slots on a device. Ours has a "data" slot that the board
  pushes to (*notify*) and a "control" slot that the laptop writes commands to.
- **MTU**: the largest single message the Bluetooth link allows. It caps how many frames fit in one packet.
- **Firmware**: the program that runs on the board. **Zephyr / NCS** is the small operating system and Nordic's toolkit for it.
- **Ring buffer**: a fixed-size waiting queue. When it is full, new items are counted as dropped, never hidden.
- **seq**: packet number. Gaps in seq mean lost packets.
- **frame_idx**: frame number. It maps straight back to a row in the source recording.
- **Tick**: one beat of the board's clock (32 768 per second).
- **MQTT / broker / topic / QoS 1**: a publish-subscribe "post office". The broker (Mosquitto) passes messages on
  a topic to everyone subscribed. QoS 1 retries until delivery is confirmed, so a message may arrive twice, and
  ingest removes the duplicates.
- **InfluxDB / Flux**: a database built for time-stamped data, and its query language.
- **Docker container**: a packaged, isolated program. `docker compose` starts several of them together.
- **CDF**: a curve showing what fraction of samples arrived within a given time.
- **Digital injection**: feeding stored data into the board in software (no analogue circuit), so the "right
  answer" is known exactly.
