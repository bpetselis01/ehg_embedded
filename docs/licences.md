# Licence register

Every component the enterprise-style evolution ([production-topology.md](production-topology.md)) uses or considers, with its licence, edition and
why it is acceptable. Written 2026-11-03 as part of Unit 1.

**How to read the Status column.** *Confirmed locally* means I read the licence text or installed package metadata on this machine on 2026-11-03.
*UNVERIFIED* means the licence comes from the planning prompt ([claude-code-prompt-thesis-b-hardening.md](claude-code-prompt-thesis-b-hardening.md) §5c) and nothing in this repository or on this machine confirms it.
Each UNVERIFIED row is checked in the unit that first uses the component, and this file is updated then. Images are to be pinned by version (and by digest where practical) when they enter a compose file.

**This repository's own licence is still pending** (awaiting the supervisor; see the [README](../README.md)). This register does not decide it.

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
| Mosquitto 2.1.2 (Homebrew build used for the local tests) | Not read (EPL/EDL per the prompt) | Real-broker tests | Free to run locally | UNVERIFIED |
| Eclipse Mosquitto | EPL/EDL (as stated in the prompt) | Edge MQTT broker | Open source, free to self-host | UNVERIFIED |
| InfluxDB 2.7 (OSS) | Not stated in the prompt | Reference time-series store | Free to self-host | UNVERIFIED |
| Grafana OSS | Apache-2.0 or AGPL-3.0 (the prompt says "check") | Dashboards | Free to self-host for own use; AGPL terms matter only if modified and offered to others | UNVERIFIED |

## Planned for the cloud profile

| Component | Licence | Edition / note | Why acceptable | Status |
|---|---|---|---|---|
| NATS Server (JetStream) | Apache-2.0 | Internal bus | Permissive | UNVERIFIED |
| PostgreSQL 16 | PostgreSQL licence | Enterprise store | Permissive | UNVERIFIED |
| **TimescaleDB Community edition** | **Tiger Data Licence (not OSI-approved)** | Needed for compression and continuous aggregates | Self-hosting for own use is allowed; offering it as a service is not. Confirm with `SHOW timescaledb.license;` in Unit 6 | UNVERIFIED |
| HAPI FHIR JPA server | Apache-2.0 | Reference EHR | Permissive | UNVERIFIED |
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
| fhir.resources 8.3.0 | Not stated in the prompt | FHIR structure checks in tests | Free | UNVERIFIED |
| **Terraform CLI** | **Business Source Licence 1.1** | Record the version used; pin 1.6 or later only after reading the licence text | Free for own use; not for offering a competing service. OpenTofu is a drop-in fallback | UNVERIFIED |
| Trivy, Syft, cosign, gitleaks, tflint, shellcheck | Not stated in the prompt | Supply-chain and lint tooling | Free to run locally or in Actions | UNVERIFIED |
| CocoaMQTT | MIT | Swift MQTT client | Permissive | UNVERIFIED |
| Nordic iOS McuMgr library | Not stated; verify name and licence | Optional over-the-air update (Tier C) | Only if the licence allows | UNVERIFIED |

## Considered and not used

| Component | Licence | Why not |
|---|---|---|
| EMQX 5.9 and later | Business Source Licence 1.1 (single node free; clustering paid) | Clustering is paid; kept as a design-only option ([decision 0012](decisions/0012-timescaledb-nats-data-platform.md)) |
| Redpanda | Business Source Licence | Not needed; Kafka-API scale-up path is design-only |
| Apache Kafka | Apache-2.0 | Too heavy next to two JVM services on 12 GB |
| HashiCorp Vault | Business Source Licence | Replaced by SOPS and age; OCI Vault optional |

## Services (terms, not licences)

Oracle Cloud Always Free, GitHub Actions, DuckDNS, Tailscale, ntfy and Apple's free Personal Team are services with terms that can change. Their limits are
recorded in [decision 0010](decisions/0010-oracle-always-free-cloud-target.md), [decision 0015](decisions/0015-native-ios-gateway-free-signing.md) and `docs/deployment.md`
(Unit 5), and are re-verified before each deployment.
