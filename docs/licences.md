# Licence register

Every component the enterprise-style evolution ([production-topology.md](production-topology.md)) uses or considers, with its licence, edition and
why it is acceptable. Written 2026-11-03 as part of Unit 1.

**How to read the Status column.** *Confirmed locally* means I read the licence text or installed package metadata on this machine on 2026-11-03.
*UNVERIFIED* means the licence comes from the planning prompt ([claude-code-prompt-thesis-b-hardening.md](claude-code-prompt-thesis-b-hardening.md) §5c) and nothing in this repository or on this machine confirms it.
Each UNVERIFIED row is checked in the unit that first uses the component, and this file is updated then. Images are to be pinned by version (and by digest where practical) when they enter a compose file.

**This repository's own licence is still pending** (awaiting the supervisor; see the [README](../README.md)). This register does not decide it; [what the dependencies allow](#which-licence-this-repository-could-take) is below.
The [reuse ledger](#reuse-ledger) lists what this repository builds itself and what existing tool could replace each part.

## Already in use

| Component | Licence | Used for | Why acceptable | Status |
|---|---|---|---|---|
| Zephyr RTOS (in NCS v3.4.1) | Apache-2.0 | Firmware kernel and Bluetooth stack | Permissive; free to use | Confirmed locally (`~/ncs/v3.4.1/zephyr/LICENSE`) |
| MCUboot (in NCS v3.4.1) | Apache-2.0 | Bootloader for over-the-air update | Permissive; free to use | Confirmed locally (`~/ncs/v3.4.1/bootloader/mcuboot/LICENSE`) |
| nRF Connect SDK `nrf` repository | `LicenseRef-Nordic-5-Clause` (Nordic 5-clause, redistribution conditions apply) | Nordic-specific firmware support | Free for use with Nordic devices. **The prompt lists "nRF Connect SDK / MCUboot (Apache-2.0)"; the file on disk says otherwise, so the repository wins and the prompt's line is wrong for `nrf`.** Read the full text before any redistribution | Confirmed locally (`~/ncs/v3.4.1/nrf/LICENSE`, first lines only) |
| wfdb 4.3.1 | MIT | Reading PhysioNet records | Permissive | Confirmed locally (package metadata) |
| numpy 2.5.3 | BSD-3-Clause and others (bundled parts) | Array maths | Permissive | Confirmed locally (package metadata) |
| bleak 3.0.2 | MIT | Bluetooth central on the Mac | Permissive | Confirmed locally (package metadata) |
| paho-mqtt 2.1.0 | EPL-2.0 OR BSD-3-Clause | MQTT client | Either option is permissive | Confirmed locally (package metadata) |
| influxdb-client 1.50.0 | MIT | InfluxDB client | Permissive | Confirmed locally (package classifier) |
| matplotlib 3.11.2 | Matplotlib licence (PSF-based) | Plots in `eval/` | Permissive | Confirmed locally (package classifier) |
| websockets 17.1 | BSD-3-Clause | Live viewer | Permissive | Confirmed locally (package metadata) |
| pyserial 3.5 (dev) | BSD | Serial feeder | Permissive | Confirmed locally (package classifier) |
| pytest 9.1.1 (dev) | MIT | Tests | Permissive | Confirmed locally (package metadata) |
| PyYAML 6.0.3 (dev, added in Unit 2) | MIT | Parses compose and cloud-init files in tests | Permissive | Confirmed locally (package metadata and classifier) |
| OpenSSL 3.0.12 (command line, used by `tools/pki.sh` and the tests) | Not read; stated as Apache-2.0 for OpenSSL 3 in general | Makes the certificates | Free; a tool run locally, not shipped | UNVERIFIED |
| Mosquitto 2.1.2 (Homebrew build used for the local tests; same version as the `eclipse-mosquitto:2` image) | `EPL-2.0 OR BSD-3-Clause` | Real-broker tests; cloud broker | Free; the licence is read from the Docker image's own label `org.opencontainers.image.licenses` | Confirmed from the image label (2026-11-03); licence text not read |
| httpx 0.28.1 | BSD-3-Clause | HTTP client of the FHIR sync worker | Permissive | Confirmed locally (package metadata) |
| nats-py 2.16.0 | Apache-2.0 | NATS client | Permissive | Confirmed locally (package metadata) |
| psycopg 3.3.7 (+ psycopg-binary) | **LGPL-3.0-only** | PostgreSQL client | Free; used as an unmodified library, so the LGPL does not extend to this code. Keep it a normal dependency (do not vendor or modify it) | Confirmed locally (package metadata) |
| Alembic 1.20.1, SQLAlchemy 2.1.6, Mako 1.4.3 | MIT | Migrations | Permissive | Confirmed locally (package metadata) |
| prometheus-client 0.26.0 | Apache-2.0 AND BSD-2-Clause | Metrics endpoints | Permissive | Confirmed locally (package metadata) |
| python-hcl2 8.1.4 (dev, added in Unit 4) | MIT | Parses `.tf` files in tests | Permissive | Confirmed locally (package metadata and classifier) |
| fhir.resources 8.3.0, fhir-core 1.1.12 (dev, added in Unit 9) | BSD (OSI classifier in the package metadata) | Checks our FHIR resources against the R5 models in tests | Permissive | Confirmed locally (package metadata) |
| Terraform CLI 1.14.9 (installed here) | Business Source Licence 1.1 (per the prompt) | Validates and applies the IaC | Free for our own use | Version confirmed (`terraform version`); licence text NOT read: UNVERIFIED |
| `oracle/oci` Terraform provider 9.11.0 | Not read | Oracle resources | Free to use | UNVERIFIED |
| Eclipse Mosquitto (`eclipse-mosquitto:2`, the default run's broker) | `EPL-2.0 OR BSD-3-Clause` | Edge MQTT broker | Open source, free to self-host | Confirmed from the image label (2026-11-11) |
| InfluxDB 2.7 (OSS) | Not stated in the prompt | Reference time-series store | Free to self-host | UNVERIFIED: the `influxdb:2.7` image (v2.7.12) has no licence label and no licence file (checked 2026-11-11) |
| Grafana OSS 13.0.2 | AGPL-3.0 | Dashboards and alert rules | Free to self-host for own use; run unmodified as a separate container, so the AGPL does not reach this code | Confirmed from `/usr/share/grafana/LICENSE` in the image (2026-11-11) |

## Planned for the cloud profile

| Component | Licence | Edition / note | Why acceptable | Status |
|---|---|---|---|---|
| NATS Server (JetStream) | Apache-2.0 | Internal bus. Removed from the stack 11 Nov 2026; `backend/ingest/ingest.py` keeps an unused NATS input | Permissive | UNVERIFIED |
| PostgreSQL 16 | PostgreSQL licence | Enterprise store | Permissive | UNVERIFIED |
| **TimescaleDB Community edition** | **Tiger Data Licence (not OSI-approved)** | Needed for compression and continuous aggregates | Self-hosting for own use is allowed; offering it as a service is not. Confirm with `SHOW timescaledb.license;` in Unit 6 | UNVERIFIED |
| HAPI FHIR JPA server (image `hapiproject/hapi:v7.6.0`; its starter config was read from the GitHub tag `image/v7.6.0` on 2026-11-04) | Apache-2.0 (per the prompt; licence text not read) | Reference EHR | Permissive | UNVERIFIED |
| Keycloak (26.8.1 or later) | Apache-2.0 | Identity provider | Permissive | UNVERIFIED |
| Prometheus, Alertmanager | Apache-2.0 | Metrics and alert routing | Permissive | UNVERIFIED |
| Loki | AGPL-3.0 (the prompt says "check") | Logs | Self-hosted for own use; do not modify and offer as a service | UNVERIFIED |
| Grafana Alloy | Not stated in the prompt | Collector | Free to self-host | UNVERIFIED |
| Caddy | Apache-2.0 | Front door and automatic HTTPS | Permissive | UNVERIFIED |
| Smallstep step-ca | Apache-2.0 | Private CA | Permissive | UNVERIFIED |
| pgBackRest | MIT | Postgres backup | Permissive | UNVERIFIED |
| restic | Not stated in the prompt | Non-Postgres backups | Free | UNVERIFIED |
| SOPS | MPL-2.0 | Encrypted secrets in git | Weak copyleft on modified files only | UNVERIFIED |
| age | BSD-3-Clause | Key encryption for SOPS | Permissive | UNVERIFIED |
| Alembic, SQLAlchemy | MIT | Migrations | Permissive | UNVERIFIED |
| **Terraform CLI** | **Business Source Licence 1.1** | Record the version used; pin 1.6 or later only after reading the licence text | Free for own use; not for offering a competing service. OpenTofu is a drop-in fallback | UNVERIFIED |
| Trivy, Syft, cosign, gitleaks, tflint, shellcheck | Not stated in the prompt | Supply-chain and lint tooling | Free to run locally or in Actions | UNVERIFIED |
| CocoaMQTT | MIT | Swift MQTT client | Permissive | UNVERIFIED |
| Nordic iOS McuMgr library | Not stated; verify name and licence | Optional over-the-air update (Tier C) | Only if the licence allows | UNVERIFIED |

## Container images pinned in `deploy/compose` (Unit 4)

Each is pinned by version and digest; **all have a `linux/arm64` manifest** (checked 2026-11-03 through the registry API). Licences below are the prompt's or common knowledge unless stated: UNVERIFIED until the image labels or texts are read.

| Image | Version | Licence (status) |
|---|---|---|
| `eclipse-mosquitto` | 2.1.2-alpine (same digest as `2`) | EPL-2.0 OR BSD-3-Clause (read from the image label) |
| `caddy` | 2.11.7-alpine | Apache-2.0 (UNVERIFIED) |
| `timescale/timescaledb-ha` | pg16.15-ts2.31.0 | PostgreSQL licence + TimescaleDB Community edition under the Tiger Data licence (UNVERIFIED; confirm `SHOW timescaledb.license;` in Unit 6). The non-HA `timescale/timescaledb:2.31.0-pg16` also has an arm64 manifest |
| `prom/prometheus`, `prom/alertmanager`, `prom/node-exporter`, `prometheuscommunity/postgres-exporter` | v3.15.1, v0.34.2, v1.12.1, v0.20.1 | Apache-2.0 (UNVERIFIED) |
| `grafana/loki`, `grafana/grafana-oss`, `grafana/alloy` | 3.7.9, 13.0.2, v1.21.3 | AGPL-3.0 for Loki and Grafana per the prompt ("check"), Alloy Apache-2.0 (all UNVERIFIED) |
| `gcr.io/cadvisor/cadvisor` | v0.55.1 | Apache-2.0 (UNVERIFIED) |
| Not yet in the compose file | `hapiproject/hapi` v7.6.0 (v8.0.0 also exists, both arm64), `quay.io/keycloak/keycloak` 26.8.1, `smallstep/step-ca` 0.30.2, `restic/restic` 0.19.1 | all arm64-checked; licences UNVERIFIED |

## Considered and not used

| Component | Licence | Why not |
|---|---|---|
| EMQX 5.9 and later | Business Source Licence 1.1 (single node free; clustering paid) | Clustering is paid; kept as a design-only option ([decision 0012](decisions/0012-timescaledb-nats-data-platform.md)) |
| Redpanda | Business Source Licence | Not needed; Kafka-API scale-up path is design-only |
| Apache Kafka | Apache-2.0 | Too heavy next to two JVM services on 12 GB |
| HashiCorp Vault | Business Source Licence | Replaced by SOPS and age; OCI Vault optional |

## Reuse ledger

What this repository builds itself, and what existing tool could have done the job instead. Written 2026-11-11 after a build-versus-reuse audit.
**Verdict:** *Custom justified* = no existing tool does this job. *Reuse possible* = a tool exists, and the custom code is small or needed for a measurement. *Should have reused* = a tool exists and the custom code adds little.
**Status:** *Measured* = the thesis results (H1 to H4) were taken on it. *Run* = started locally, no thesis result depends on it. *Written* = code and unit tests only, never run as a service ([production-topology.md](production-topology.md)).
Line counts are approximate (2026-11-11).

| Component | What it does | Lines | Verdict | Existing alternative | Status |
|---|---|---|---|---|---|
| [`firmware/ehg_node/`](../firmware/ehg_node) | Replays a recording, packs frames, keeps a resend history | about 2,000 | Custom justified | None. Built on Zephyr and NCS; the CRC is Zephyr's `crc16_itu_t`, not hand-written | Measured |
| Wire format ([`gateway/packet.py`](../gateway/packet.py), `ehg_packet.h`) | 12-byte header, int16 samples, CRC-16 | 93 | Custom justified | None for raw multichannel EHG. Python CRC is the standard library's `binascii.crc_hqx` | Measured |
| [`gateway/`](../gateway) | BLE central, gap tracking and RESEND, MQTT publish | 820 | Custom justified | None. Built on Bleak and paho-mqtt | Measured |
| [`gateway/outbox.py`](../gateway/outbox.py) | SQLite store-and-forward across broker outages and restarts | 187 | Reuse possible | `persist-queue`; paho's own queue is in memory only and is lost on restart | Run (default run); not part of the measured run 3 |
| [`backend/ingest/`](../backend/ingest) | MQTT to InfluxDB, stamps receive and write-acknowledged times | 179 | Reuse possible | Telegraf `mqtt_consumer`. Kept because H1 needs the write-acknowledged timestamp per packet, which Telegraf does not record | Measured |
| [`tools/convert.py`](../tools/convert.py) | PhysioNet record to int16 `.npz` | small | Reuse possible | Reads records with `wfdb`; only the scaling is custom | Measured (input data) |
| [`eval/`](../eval) | H1 latency, H2 delivery, H4 bit-exact comparison | 509 | Custom justified | None | Measured |
| [`viewer/`](../viewer) raw viewer | WebSocket multichannel plot | about 280 | Reuse possible | Grafana Live. Kept small | Run |
| [`backend/signal_metrics/`](../backend/signal_metrics) | RMS, median frequency, flatline and saturation per channel | 132 | Reuse possible | Flux tasks could do RMS; median frequency needs an FFT | Run (default run) |
| Grafana alert rules (`backend/grafana/provisioning/alerting/`) | Node offline, loss, latency, flatline, saturation | config only | Reused | This is Grafana's own alerting | Run (one rule seen firing) |
| [`tools/fhir_export.py`](../tools/fhir_export.py) | Stored window to a FHIR R5 bundle | 114 | Reuse possible | `fhir.resources` models (used in the tests) | Run |
| Triage service and page (`backend/triage/`, `viewer/triage.*`) | Rules, alert state, audit log, API, worklist page | 1,464 + page | **Removed 11 Nov 2026** | Use Grafana alerting (already provisioned here), Prometheus rules and Alertmanager | Deleted |
| Enrolment API (`backend/enroll/`) | Enrolment API in front of the private CA | 460 | **Removed 11 Nov 2026** | Use the `step` CLI with step-ca's ACME provisioner or a one-time token | Deleted |
| Bridge, bus helper and writer (`backend/bridge/`, `backend/bus.py`, `backend/ts_writer/`) | MQTT to NATS, NATS to TimescaleDB | 481 | **Removed 11 Nov 2026** | Use Telegraf (`mqtt_consumer` input, PostgreSQL output), or NATS's own MQTT support | Deleted |
| Mosquitto exporter (`backend/mqtt_exporter.py`) | Mosquitto `$SYS` topics as Prometheus metrics | 77 | **Removed 11 Nov 2026** | Use `sapcc/mosquitto-exporter` | Deleted |
| Token helper (`backend/jwtauth.py`) | Token checks and roles for the two removed APIs | 126 | **Removed 11 Nov 2026** | It wrapped PyJWT; no caller was left | Deleted |
| [`backend/fhir_sync/`](../backend/fhir_sync) | Live write-back to a FHIR server | 215 | Reuse possible | Out of thesis scope; the batch export above is the demonstration | Written |
| [`ios/`](../ios) | Swift wire library, gateway core and iPhone app, with a hand-written MQTT client (`MQTT.swift`, 172 lines) | about 2,800 | Reuse possible | CocoaMQTT or MQTT NIO for the client | Written; not part of the measured results ([ios-gateway.md](ios-gateway.md)) |
| [`deploy/`](../deploy) | Terraform, cloud-init, compose, backups | about 2,500 | Reused | Standard tools throughout; no custom service | Written, never applied |

## Which licence this repository could take

No licence is chosen yet; the supervisor decides. This note only records what the dependencies allow (2026-11-11).

- **Apache-2.0 or MIT would both fit.** Every library the code imports is permissive (MIT, BSD, Apache-2.0, EPL-2.0 OR BSD-3-Clause), except psycopg.
- **psycopg is LGPL-3.0-only.** It is used as an unmodified, separately installed library by the Written services only, so it does not constrain this code's licence. Do not vendor or modify it.
- **No GPL or AGPL code is linked.** Grafana OSS is AGPL-3.0 and Mosquitto is EPL-2.0 OR BSD-3-Clause; both run as unmodified, separate containers and are not redistributed here.
- **Firmware:** the app's own sources can be Apache-2.0 like Zephyr. The NCS `nrf` repository is under Nordic's 5-clause licence, so a built firmware image is tied to Nordic devices. The repository ships source, not images.
- **Question for the supervisor:** "May I release the thesis code under Apache-2.0, matching Zephyr and the BioGAP firmware?"

## Services (terms, not licences)

Oracle Cloud Always Free, GitHub Actions, DuckDNS, Tailscale, ntfy and Apple's free Personal Team are services with terms that can change. Their limits are
recorded in [decision 0010](decisions/0010-oracle-always-free-cloud-target.md), [decision 0015](decisions/0015-native-ios-gateway-free-signing.md) and `docs/deployment.md`
(Unit 5), and are re-verified before each deployment.
