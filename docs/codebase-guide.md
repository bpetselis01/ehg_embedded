# Codebase guide

Every source file, grouped by component, with what it does. Start with the [README](../README.md).

## Firmware

| File | What it does |
|---|---|
| [`firmware/build.sh`](../firmware/build.sh) | Build (and optionally flash) the EHG node. |
| [`firmware/ehg_node/boards/nrf52840dk_nrf52840.overlay`](../firmware/ehg_node/boards/nrf52840dk_nrf52840.overlay) | Serial port for the J-Link VCOM, shared by the log and the sample stream (tools/feed.py). |
| [`firmware/ehg_node/prj.conf`](../firmware/ehg_node/prj.conf) | Turns on, and sizes, the Zephyr + Bluetooth features the firmware uses. |
| [`firmware/ehg_node/security-button.conf`](../firmware/ehg_node/security-button.conf) | Overlay for the "button pairing" build (firmware/build.sh --pairing-button): an ENCRYPTED link, and new pairings only for 60 s after... |
| [`firmware/ehg_node/security.conf`](../firmware/ehg_node/security.conf) | Overlay for the "secure" build (firmware/build.sh --secure): the link needs LE Secure Connections passkey pairing. |
| [`firmware/ehg_node/src/ehg_packet.h`](../firmware/ehg_node/src/ehg_packet.h) | ehg_packet.h - byte layout of 1 radio packet. Copy of gateway/packet.py: change both together. |
| [`firmware/ehg_node/src/ehg_service.c`](../firmware/ehg_node/src/ehg_service.c) | ehg_service.c - packs frames into numbered packets, sends them over Bluetooth, re-sends lost ones. |
| [`firmware/ehg_node/src/ehg_service.h`](../firmware/ehg_node/src/ehg_service.h) | ehg_service.h - what other files may call in ehg_service.c, plus the Bluetooth service ID. |
| [`firmware/ehg_node/src/main.c`](../firmware/ehg_node/src/main.c) | main.c - start-up + Bluetooth connection. The program starts here. |
| [`firmware/ehg_node/src/replay.c`](../firmware/ehg_node/src/replay.c) | replay.c - the sensor stand-in: takes frames the laptop streams in over the serial port and |
| [`firmware/ehg_node/src/replay.h`](../firmware/ehg_node/src/replay.h) | replay.h - what other files may call in replay.c (the fake sensor). |
| [`firmware/ehg_node/sysbuild.conf`](../firmware/ehg_node/sysbuild.conf) | Sysbuild settings: what gets built next to our app. |
| [`firmware/ehg_node/sysbuild/mcuboot.overlay`](../firmware/ehg_node/sysbuild/mcuboot.overlay) | MCUboot (the bootloader, sysbuild.conf) is a separate program with its own devicetree: give its |
| [`firmware/gen_signing_key.sh`](../firmware/gen_signing_key.sh) | USAGE: EHG_MCUBOOT_KEY=$HOME/.ehg-keys/mcuboot-p256.pem firmware/gen_signing_key.sh |

## Gateway

| File | What it does |
|---|---|
| [`gateway/gateway.py`](../gateway/gateway.py) | BLE (Bleak) central -> MQTT gateway for EHG nodes. |
| [`gateway/outbox.py`](../gateway/outbox.py) | Durable store-and-forward queue for the gateway's MQTT messages (decision 0009). |
| [`gateway/packet.py`](../gateway/packet.py) | EHG wire format v2 (v1 + CRC-16 trailer). Mirror of firmware/ehg_node/src/ehg_packet.h. Change both together. |
| [`gateway/tracking.py`](../gateway/tracking.py) | Pure gateway logic: packet-loss tracking + board-to-laptop clock mapping. No I/O. |

## Backend

| File | What it does |
|---|---|
| [`backend/db/migrate.py`](../backend/db/migrate.py) | Apply or undo the database migrations (Alembic, hand-written SQL). |
| [`backend/db/migrations/env.py`](../backend/db/migrations/env.py) | Alembic environment: connects with the URL given by migrate.py (or EHG_DB_URL) and runs each migration in a transaction. |
| [`backend/db/migrations/versions/0001_base_schema.py`](../backend/db/migrations/versions/0001_base_schema.py) | Base schema: ehg_raw, ehg_pkt, ehg_health as plain PostgreSQL tables (works on any PostgreSQL 14 or later). |
| [`backend/db/migrations/versions/0002_triage_registry.py`](../backend/db/migrations/versions/0002_triage_registry.py) | Triage and registry schemas: alerts, their history, the hash-chained audit log, and the device/subject registry (plain PostgreSQL). |
| [`backend/db/migrations/versions/0003_timescale.py`](../backend/db/migrations/versions/0003_timescale.py) | TimescaleDB: hypertables, compression, retention and the health roll-ups (needs the timescaledb extension; fails loudly without it). |
| [`backend/docker-compose.cloud.yml`](../backend/docker-compose.cloud.yml) | EHG backend, cloud profile: the same 4 containers with MQTT over TLS 1.3 and client certificates. |
| [`backend/docker-compose.yml`](../backend/docker-compose.yml) | EHG backend: MQTT broker -> ingest -> InfluxDB -> Grafana. |
| [`backend/fhir_sync/worker.py`](../backend/fhir_sync/worker.py) | FHIR sync worker: copies stored EHG windows into a FHIR R5 server (HAPI) as Device + Observation(SampledData), once per window, only... |
| [`backend/grafana/provisioning/alerting/ehg-health.yml`](../backend/grafana/provisioning/alerting/ehg-health.yml) | Pipeline-health alerts shown on the EHG overview dashboard. |
| [`backend/grafana/provisioning/dashboards/files.yml`](../backend/grafana/provisioning/dashboards/files.yml) | Tells Grafana to load dashboards from files at start-up, instead of by hand. |
| [`backend/grafana/provisioning/datasources/influx.yml`](../backend/grafana/provisioning/datasources/influx.yml) | Connects Grafana to InfluxDB automatically at start-up. |
| [`backend/ingest/ingest.py`](../backend/ingest/ingest.py) | MQTT -> InfluxDB ingestion service. |
| [`backend/mosquitto/mosquitto.conf`](../backend/mosquitto/mosquitto.conf) | Local development broker. Ports are bound to 127.0.0.1 in docker-compose.yml. |
| [`backend/mosquitto/mosquitto.tls.conf`](../backend/mosquitto/mosquitto.tls.conf) | Cloud-profile broker. Used only through backend/docker-compose.cloud.yml; the local mosquitto.conf is untouched. |
| [`backend/mqtt_env.py`](../backend/mqtt_env.py) | MQTT connection settings from environment variables, shared by the bridge and the triage worker. |
| [`backend/signal_metrics/worker.py`](../backend/signal_metrics/worker.py) | Signal descriptors per channel for the Grafana dashboard: RMS, median frequency, flatline and saturation. |

## Viewer

| File | What it does |
|---|---|
| [`viewer/index.html`](../viewer/index.html) | Live raw-EHG view: one scrolling trace per channel, fed by viewer/server.py over a WebSocket. |
| [`viewer/server.py`](../viewer/server.py) | Raw EHG waveform viewer: MQTT -> WebSocket bridge + static page. |

## Evaluation

| File | What it does |
|---|---|
| [`eval/backend_client.py`](../eval/backend_client.py) | Shared database queries for the evaluation scripts, for either store: InfluxDB 2.7 (the reference, default) or PostgreSQL/TimescaleDB. |
| [`eval/integrity.py`](../eval/integrity.py) | H2 (delivery) and H4 (bit-exact fidelity): compare what reached InfluxDB with the source file. |
| [`eval/latency.py`](../eval/latency.py) | H1 - end-to-end latency: sample injected in firmware -> queryable in InfluxDB. |
| [`eval/run_check.py`](../eval/run_check.py) | Pass/fail health of a run, live beside a demo or once at the end of the hardware test. |

## Tools

| File | What it does |
|---|---|
| [`tools/convert.py`](../tools/convert.py) | Convert PhysioNet WFDB recordings -> 1.npz file per recording. |
| [`tools/demo.sh`](../tools/demo.sh) | Start the whole pipeline for a live demo, with one window per hop so packets can be watched moving. |
| [`tools/export_record_meta.py`](../tools/export_record_meta.py) | Writes the per-record signal metadata the FHIR sync worker needs, so the worker can run where the converted recordings are not. |
| [`tools/feed.py`](../tools/feed.py) | Stream a converted recording into the board over the J-Link serial port (digital injection). |
| [`tools/fhir_export.py`](../tools/fhir_export.py) | Export a stored EHG window as an HL7 FHIR R5 Bundle (Device + Observation with SampledData). |
| [`tools/gen_acl.py`](../tools/gen_acl.py) | Writes the Mosquitto ACL that pins every gateway to the nodes it serves. |
| [`tools/gen_packet_vectors.py`](../tools/gen_packet_vectors.py) | Generate the golden vectors that guard every copy of the packet decoder (Python, C firmware header, Swift) and of the gap tracker and... |
| [`tools/make_replay.py`](../tools/make_replay.py) | Bake one converted recording into the firmware as a C header. |
| [`tools/pki.sh`](../tools/pki.sh) | Make the certificates the TLS broker needs: an offline root, an intermediate that issues, the broker's own certificate and one client... |
| [`tools/run.sh`](../tools/run.sh) | Choose which firmware and software to run. |
| [`tools/watch_db.py`](../tools/watch_db.py) | Live view of packets landing in InfluxDB, for demos (the last hop of the pipeline). |

## iOS

| File | What it does |
|---|---|
| [`ios/EHGGateway/Sources/BleLink.swift`](../ios/EHGGateway/Sources/BleLink.swift) | The Bluetooth side of the gateway: finds boards named EHG-*, connects, subscribes to the data characteristic, starts sampling, passes... |
| [`ios/EHGGateway/Sources/ContentView.swift`](../ios/EHGGateway/Sources/ContentView.swift) | The status screen: is the phone connected to the board and to the cloud, how many packets arrived, what is missing, what is waiting on... |
| [`ios/EHGGateway/Sources/EHGGatewayApp.swift`](../ios/EHGGateway/Sources/EHGGatewayApp.swift) | Entry point of the iPhone gateway app. |
| [`ios/EHGGateway/Sources/GatewayModel.swift`](../ios/EHGGateway/Sources/GatewayModel.swift) | Ties the Bluetooth link, the per-node sessions, the outbox and the MQTT client together and publishes the numbers the screen shows. |
| [`ios/EHGGateway/project.yml`](../ios/EHGGateway/project.yml) | SIGNING: left to you. Open the generated project, pick your Personal Team under Signing & Capabilities and change the bundle identifier... |
| [`ios/EHGWire/Package.swift`](../ios/EHGWire/Package.swift) | swift-tools-version: 5.9 |
| [`ios/EHGWire/Sources/EHGGatewayCore/Admission.swift`](../ios/EHGWire/Sources/EHGGatewayCore/Admission.swift) | Decides which Bluetooth devices the gateway may treat as a node. |
| [`ios/EHGWire/Sources/EHGGatewayCore/Certificate.swift`](../ios/EHGWire/Sources/EHGGatewayCore/Certificate.swift) | Reads the notAfter date out of a DER X.509 certificate. |
| [`ios/EHGWire/Sources/EHGGatewayCore/Enrolment.swift`](../ios/EHGWire/Sources/EHGGatewayCore/Enrolment.swift) | What the phone needs to get its own client certificate without a laptop: (1) the Keycloak DEVICE grant (the phone shows a code, you... |
| [`ios/EHGWire/Sources/EHGGatewayCore/MQTT.swift`](../ios/EHGWire/Sources/EHGGatewayCore/MQTT.swift) | A small MQTT 3.1.1 client for the phone: TLS 1.3 with a client certificate (mTLS), persistent session, QoS 1 publishes, last will,... |
| [`ios/EHGWire/Sources/EHGGatewayCore/Message.swift`](../ios/EHGWire/Sources/EHGGatewayCore/Message.swift) | The JSON the gateway publishes to ehg/<node>/pkt for each packet, and the clock rule that goes with it. |
| [`ios/EHGWire/Sources/EHGGatewayCore/NodeSession.swift`](../ios/EHGWire/Sources/EHGGatewayCore/NodeSession.swift) | Everything the gateway does for ONE board, in the order gateway/gateway.py:Node.on_bytes does it: decode, count a bad packet, teach the... |
| [`ios/EHGWire/Sources/EHGGatewayCore/Outbox.swift`](../ios/EHGWire/Sources/EHGGatewayCore/Outbox.swift) | Durable store-and-forward queue for the phone's MQTT messages. |
| [`ios/EHGWire/Sources/EHGWire/Packet.swift`](../ios/EHGWire/Sources/EHGWire/Packet.swift) | EHG wire format v2 (v1 + CRC-16 trailer) for the iPhone gateway. |
| [`ios/EHGWire/Sources/EHGWire/Tracking.swift`](../ios/EHGWire/Sources/EHGWire/Tracking.swift) | Packet-loss tracking and board-to-phone clock mapping for the iPhone gateway. |
| [`ios/EHGWire/Tests/EHGGatewayCoreTests/GatewayCoreTests.swift`](../ios/EHGWire/Tests/EHGGatewayCoreTests/GatewayCoreTests.swift) | / Records what the outbox hands over; tokens are consecutive integers. |
| [`ios/EHGWire/Tests/EHGGatewayCoreTests/MQTTIntegrationTests.swift`](../ios/EHGWire/Tests/EHGGatewayCoreTests/MQTTIntegrationTests.swift) | / Runs only when tests/test_ios_mqtt.py has started a real Mosquitto and exported its settings; otherwise skipped. |
| [`ios/EHGWire/Tests/EHGWireTests/PacketTests.swift`](../ios/EHGWire/Tests/EHGWireTests/PacketTests.swift) | Decode every golden vector from tests/fixtures/packet_vectors.json (generated from gateway/packet.py) and demand identical values, a... |

## Deployment

| File | What it does |
|---|---|
| [`deploy/.sops.yaml`](../deploy/.sops.yaml) | Tells SOPS which age key may decrypt the files in deploy/secrets/. |
| [`deploy/backup/backup.sh`](../deploy/backup/backup.sh) | PURPOSE: One consistent backup of the EHG databases: roles, every database as a pg_dump custom-format file, and a checksum list.... |
| [`deploy/backup/restore_drill.sh`](../deploy/backup/restore_drill.sh) | PURPOSE: Prove that a backup restores: verify its checksums, recreate roles and databases on a CLEAN PostgreSQL, and time the whole... |
| [`deploy/compose/docker-compose.cloud.yml`](../deploy/compose/docker-compose.cloud.yml) | EHG cloud core: the services that can run today, on the Oracle Always Free VM (2 OCPU / 12 GB arm64). |
| [`deploy/fix-key-permissions.sh`](../deploy/fix-key-permissions.sh) | USAGE: sudo deploy/fix-key-permissions.sh [--pki /opt/ehg/pki] [--step-ca /opt/ehg/step-ca] [--owner ehg] |
| [`deploy/hapi/application.yaml`](../deploy/hapi/application.yaml) | Configuration of the reference EHR, HAPI FHIR JPA server (image hapiproject/hapi:v7.6.0), as a FHIR R5 server on the private Docker network. |
| [`deploy/make-sbom.sh`](../deploy/make-sbom.sh) | USAGE: deploy/make-sbom.sh [OUTDIR] (default docs/sbom; needs Docker, and the two images built: `docker compose -f... |
| [`deploy/observability/alertmanager.ntfy.example.yml`](../deploy/observability/alertmanager.ntfy.example.yml) | The alert routing to use ONCE a notification channel exists. |
| [`deploy/observability/alertmanager.yml`](../deploy/observability/alertmanager.yml) | Alertmanager with one receiver that does nothing. |
| [`deploy/observability/grafana/dashboards.yml`](../deploy/observability/grafana/dashboards.yml) | Tells Grafana to load every dashboard JSON file in /var/lib/grafana-dashboards (mounted from deploy/observability/grafana/dashboards/). |
| [`deploy/observability/grafana/datasources.yml`](../deploy/observability/grafana/datasources.yml) | Grafana data sources provisioned from code: Prometheus (default) and Loki. |
| [`deploy/observability/loki.yml`](../deploy/observability/loki.yml) | Loki log store for the container logs that Alloy ships: single process, files on the data volume, 7 days kept. |
| [`deploy/observability/prometheus.yml`](../deploy/observability/prometheus.yml) | Prometheus: what to scrape, the SLO recording rules and the alert rules (rules/), and where alerts go (Alertmanager). |
| [`deploy/observability/rules/alerts.yml`](../deploy/observability/rules/alerts.yml) | Alert rules for the cloud core. |
| [`deploy/observability/rules/slo.yml`](../deploy/observability/rules/slo.yml) | Recording rules for the cloud core. |
| [`deploy/observability/tests/rules_test.yml`](../deploy/observability/tests/rules_test.yml) | RUN: docker run --rm --entrypoint promtool -v "$PWD/deploy/observability:/o:ro" prom/prometheus:v3.15.1 test rules... |
| [`deploy/postgres/10-init-databases.sh`](../deploy/postgres/10-init-databases.sh) | First start of PostgreSQL only: create the databases and login roles the cloud services use. |
| [`deploy/scan-images.sh`](../deploy/scan-images.sh) | USAGE: deploy/scan-images.sh (needs Docker; pulls trivy 0.76.1 by digest; ~10 minutes the first time, the vulnerability database is... |
| [`deploy/secrets/secrets.example.yaml`](../deploy/secrets/secrets.example.yaml) | The list of secrets the cloud stack needs, with obviously fake values. |
| [`deploy/step-ca/init-offline.sh`](../deploy/step-ca/init-offline.sh) | USAGE: deploy/step-ca/init-offline.sh OUTDIR (OUTDIR must not exist; e.g. ~/ehg-ca) |
| [`deploy/terraform/main.tf`](../deploy/terraform/main.tf) | One Oracle Always Free VM (Ampere A1, 2 OCPU / 12 GB) with its network, a data volume, a backup bucket, a "VM down" alarm and a cost guard. |
| [`deploy/terraform/modules/compute/main.tf`](../deploy/terraform/modules/compute/main.tf) | The one VM (Ampere A1, 2 OCPU, 12 GB, arm64, Ubuntu 24.04) plus a 150 GB data volume. |
| [`deploy/terraform/modules/compute/outputs.tf`](../deploy/terraform/modules/compute/outputs.tf) | Module outputs |
| [`deploy/terraform/modules/compute/variables.tf`](../deploy/terraform/modules/compute/variables.tf) | Module input variables |
| [`deploy/terraform/modules/compute/versions.tf`](../deploy/terraform/modules/compute/versions.tf) | Declares which provider this module uses and the minimum Terraform, so the module stands on its own (tflint:... |
| [`deploy/terraform/modules/monitoring/main.tf`](../deploy/terraform/modules/monitoring/main.tf) | Two out-of-band safety nets that do not depend on the VM being alive: a "VM down" alarm and a cost guard. |
| [`deploy/terraform/modules/monitoring/outputs.tf`](../deploy/terraform/modules/monitoring/outputs.tf) | Module outputs |
| [`deploy/terraform/modules/monitoring/variables.tf`](../deploy/terraform/modules/monitoring/variables.tf) | Module input variables |
| [`deploy/terraform/modules/monitoring/versions.tf`](../deploy/terraform/modules/monitoring/versions.tf) | Declares which provider this module uses and the minimum Terraform, so the module stands on its own (tflint:... |
| [`deploy/terraform/modules/network/main.tf`](../deploy/terraform/modules/network/main.tf) | The VM's private network: one VCN, one public subnet, an internet gateway and a security list that opens only what the design needs. |
| [`deploy/terraform/modules/network/outputs.tf`](../deploy/terraform/modules/network/outputs.tf) | Module outputs |
| [`deploy/terraform/modules/network/variables.tf`](../deploy/terraform/modules/network/variables.tf) | Module input variables |
| [`deploy/terraform/modules/network/versions.tf`](../deploy/terraform/modules/network/versions.tf) | Declares which provider this module uses and the minimum Terraform, so the module stands on its own (tflint:... |
| [`deploy/terraform/modules/storage/main.tf`](../deploy/terraform/modules/storage/main.tf) | A private Object Storage bucket for encrypted backups. |
| [`deploy/terraform/modules/storage/outputs.tf`](../deploy/terraform/modules/storage/outputs.tf) | Module outputs |
| [`deploy/terraform/modules/storage/variables.tf`](../deploy/terraform/modules/storage/variables.tf) | Module input variables |
| [`deploy/terraform/modules/storage/versions.tf`](../deploy/terraform/modules/storage/versions.tf) | Declares which provider this module uses and the minimum Terraform, so the module stands on its own (tflint:... |
| [`deploy/terraform/outputs.tf`](../deploy/terraform/outputs.tf) | The few values you need after `terraform apply`. |
| [`deploy/terraform/variables.tf`](../deploy/terraform/variables.tf) | Everything you must set before `terraform plan`. |
| [`deploy/terraform/versions.tf`](../deploy/terraform/versions.tf) | Pins the Terraform CLI and the Oracle Cloud provider so every run uses the same code. |
| [`deploy/update.sh`](../deploy/update.sh) | Update the EHG cloud stack to a tagged release, check it came up healthy, and roll back by itself if it did not. |

## Tests

| File | What it does |
|---|---|
| [`tests/conftest.py`](../tests/conftest.py) | Shared pytest fixtures: a throwaway local PostgreSQL (no Docker) for the tests that need real SQL. |
| [`tests/test_backend_client.py`](../tests/test_backend_client.py) | Checks eval/backend_client.py: importable without backend/.env, the same return shapes from InfluxDB and from PostgreSQL, a bad backend... |
| [`tests/test_backup_scripts.py`](../tests/test_backup_scripts.py) | Checks deploy/backup/backup.sh and restore_drill.sh against two throwaway local PostgreSQL clusters (no Docker): a real dump, a real... |
| [`tests/test_caddy_config.py`](../tests/test_caddy_config.py) | Checks the Caddyfile (Unit 11): the route list is exactly the designed one, nothing internal is proxied, every response gets the... |
| [`tests/test_ci_config.py`](../tests/test_ci_config.py) | Checks the CI and supply-chain files (Unit 14): workflows are valid YAML, use only the pinned actions the repository already uses, run... |
| [`tests/test_convert.py`](../tests/test_convert.py) | Checks tools/convert.py: numbers kept exactly, |
| [`tests/test_db_migrations.py`](../tests/test_db_migrations.py) | Checks the Alembic migrations on a throwaway PostgreSQL: 0001 and 0002 build the tables with the right keys, go back to nothing, 0003... |
| [`tests/test_deploy_assets.py`](../tests/test_deploy_assets.py) | Checks the deployment code in deploy/ (Unit 4) without any cloud account, Docker daemon or network: Terraform is well-formed and sized... |
| [`tests/test_docs.py`](../tests/test_docs.py) | Checks the docs and the duplicated facts still match the code, so stale documentation fails the |
| [`tests/test_feed.py`](../tests/test_feed.py) | Checks tools/feed.py: messages survive the 0x00-delimited COBS framing, carry a CRC, frames keep |
| [`tests/test_fhir_export.py`](../tests/test_fhir_export.py) | Checks tools/fhir_export.py: the FHIR R5 SampledData keeps the raw counts unchanged |
| [`tests/test_fhir_sync.py`](../tests/test_fhir_sync.py) | Checks backend/fhir_sync/worker.py against a FAKE FHIR server that implements the conditional-create / conditional-update rules of a... |
| [`tests/test_firmware_security.py`](../tests/test_firmware_security.py) | Static guard for Unit 13 (LESC pairing, own signing key). The firmware itself is only tested on the board, so this keeps the pieces that... |
| [`tests/test_fix_key_permissions.py`](../tests/test_fix_key_permissions.py) | deploy/fix-key-permissions.sh: private keys are readable by exactly the container user that needs them. chown and chmod are replaced by... |
| [`tests/test_gateway.py`](../tests/test_gateway.py) | Checks gateway/gateway.py:Node across a reconnect: the board keeps sampling while the link is down, |
| [`tests/test_gen_acl.py`](../tests/test_gen_acl.py) | Checks tools/gen_acl.py: the ACL it writes is least privilege per gateway, refuses ids that could widen a grant, and rewrites the file... |
| [`tests/test_gitleaks_config.py`](../tests/test_gitleaks_config.py) | The secret-scan allowlist must stay narrow. gitleaks combines `paths` and `regexes` with OR unless condition = "AND", which made a first... |
| [`tests/test_grafana_alerts.py`](../tests/test_grafana_alerts.py) | Checks the provisioned Grafana alert rules: fixed thresholds, the local datasource only, no clinical wording, nothing that Grafana would... |
| [`tests/test_handoff.py`](../tests/test_handoff.py) | Checks the session-limit resume pin, stays usable: when the file exists it has every heading |
| [`tests/test_hapi_integration.py`](../tests/test_hapi_integration.py) | Opt-in check against a REAL HAPI FHIR R5 server (needs Docker; set EHG_INTEGRATION=1). Never run in the default suite and NEVER run so... |
| [`tests/test_hardware_default_run.py`](../tests/test_hardware_default_run.py) | The default run on the real board, end to end, judged by eval/run_check.py. |
| [`tests/test_integrity.py`](../tests/test_integrity.py) | Checks eval/integrity.py:reconcile on a tiny fake record: perfect delivery, a missing |
| [`tests/test_ios_mqtt.py`](../tests/test_ios_mqtt.py) | The iPhone gateway's MQTT client against a REAL Mosquitto (the repository's TLS config and ACL): TLS 1.3 with a client certificate, QoS... |
| [`tests/test_ios_wire_format.py`](../tests/test_ios_wire_format.py) | Guards the third copy of the wire format (Swift, ios/EHGWire): the golden vectors are exactly what gateway/packet.py produces today,... |
| [`tests/test_latency.py`](../tests/test_latency.py) | Checks eval/latency.py: the drift fit recovers a clock running 50 ppm slow over 6 h, the |
| [`tests/test_make_replay.py`](../tests/test_make_replay.py) | Checks tools/make_replay.py writes every sample (including the int16 extremes) into |
| [`tests/test_mqtt_tls_config.py`](../tests/test_mqtt_tls_config.py) | Checks the "cloud profile" transport (decision 0008): the broker config and ACL say what they must, the default stack is untouched, |
| [`tests/test_observability_rules.py`](../tests/test_observability_rules.py) | Checks the Prometheus rule files, since promtool is not installed here: structure, that every metric a rule uses is defined somewhere in... |
| [`tests/test_outbox.py`](../tests/test_outbox.py) | Checks the gateway's durable outbox (decision 0009): a message is written to disk BEFORE it is sent, deleted only when the broker's |
| [`tests/test_packet.py`](../tests/test_packet.py) | Checks gateway/packet.py: pack -> parse returns the same packet, and short or |
| [`tests/test_platform_compose.py`](../tests/test_platform_compose.py) | Checks the platform image (migration job and FHIR sync worker): unprivileged, no secrets, everything its services import is in it. Also... |
| [`tests/test_promtool_rules.py`](../tests/test_promtool_rules.py) | Runs the promtool unit tests of the SLO and alert rules (deploy/observability/tests/rules_test.yml) in the pinned Prometheus image. |
| [`tests/test_realm_export.py`](../tests/test_realm_export.py) | Checks the Keycloak realm file (decision 0013): valid JSON, no secrets or users, the two remaining clients are configured the safe way... |
| [`tests/test_regulatory_doc.py`](../tests/test_regulatory_doc.py) | Keeps docs/regulatory.md honest: every test file it names exists, the intended-purpose statement is exact, and no forbidden claim... |
| [`tests/test_run_check.py`](../tests/test_run_check.py) | eval/run_check.py:evaluate on built rows, so the pass/fail thresholds are tested without a board or InfluxDB. |
| [`tests/test_run_selector.py`](../tests/test_run_selector.py) | Checks tools/run.sh and tools/demo.sh through their dry runs: each run starts what its label says, and no run can do the operations that... |
| [`tests/test_signal_metrics.py`](../tests/test_signal_metrics.py) | Checks backend/signal_metrics/worker.py against signals whose answers are known, and the Grafana panels that read its output. |
| [`tests/test_tracking.py`](../tests/test_tracking.py) | Checks gateway/tracking.py: gap detection, resend filling a gap, duplicates, |
