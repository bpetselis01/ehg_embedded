# EHG telemetry pipeline: Thesis B (BIOM4951)

**Intended purpose:** research prototype for measuring wireless telemetry performance. Not for clinical decision-making. Not supplied to any person for patient care. Makes no diagnostic or predictive claims. It uses public, de-identified recordings only.

An open, reproducible wireless telemetry and cloud monitoring pipeline for electrohysterography (EHG):

```
public EHG record ─▶ nRF52840 DK (Zephyr, digital injection) ─BLE GATT notify─▶ Bleak gateway ─MQTT QoS1─▶ ingest ─▶ InfluxDB 2.7 ─▶ Grafana
                                                                                                    └─────▶ WebSocket raw viewer
```

The validated chain above is what the thesis results (H1 to H4) were measured on. Around it sits an **enterprise-style evolution** (mutual-TLS MQTT, a reference FHIR server, sign-in, observability, backups, infrastructure as code, a Swift wire library),
written and unit-tested but **mostly never started** because Docker and a cloud account were not available: [`docs/production-topology.md`](docs/production-topology.md) marks every hop built, written-only or design-only.

**Reading path:** [codebase guide](docs/codebase-guide.md) (how it fits together) → [reproducing the results](#reproducing-the-results)
(each thesis number, with the output to expect) → [decisions](docs/decisions) (why it is built this way) →
[troubleshooting](docs/troubleshooting.md) (when something fails). Exact formats: [`docs/interface-spec.md`](docs/interface-spec.md).
Status, run results and open items: [`docs/progress.md`](docs/progress.md).

## Layout

| Path | What |
|---|---|
| [`tools/`](tools) | [`convert.py`](tools/convert.py) (WFDB → int16 `.npz`), [`feed.py`](tools/feed.py) (streams a `.npz` into the board over serial), [`make_replay.py`](tools/make_replay.py) (`.npz` → built-in firmware recording, via `build.sh --record`), [`fhir_export.py`](tools/fhir_export.py) (stored window → FHIR R5), [`watch_db.py`](tools/watch_db.py) + [`demo.sh`](tools/demo.sh) (live demo), [`pki.sh`](tools/pki.sh) (certificates for the TLS cloud profile) |
| [`firmware/ehg_node/`](firmware/ehg_node) | Zephyr app for `nrf52840dk/nrf52840`, NCS v3.4.1. [`firmware/build.sh`](firmware/build.sh) builds and flashes it |
| [`gateway/`](gateway) | Bleak central → MQTT; `--simulate N` software nodes use the same wire format |
| [`backend/`](backend) | docker compose: Mosquitto, InfluxDB 2.7, ingest, Grafana (provisioned dashboard); Python services: [`ingest/`](backend/ingest), [`signal_metrics/`](backend/signal_metrics), [`fhir_sync/`](backend/fhir_sync), [`db/`](backend/db) (migrations) |
| [`viewer/`](viewer) | WebSocket raw multichannel viewer |
| [`ios/EHGWire/`](ios/EHGWire) | Swift package: wire format, gateway core and a real-broker-tested MQTT client for the iPhone gateway (`swift test`); the app is [`ios/EHGGateway/`](ios/EHGGateway/project.yml), see [docs/ios-gateway.md](docs/ios-gateway.md) |
| [`eval/`](eval) | [`latency.py`](eval/latency.py) (H1), [`integrity.py`](eval/integrity.py) (H2 + H4 bit-exact) |
| [`papers/`](papers) | [`INDEX.md`](papers/INDEX.md) (tracked) + open-access PDFs (not tracked) |
| [`tests/`](tests) | pytest for the host code, plus [`test_docs.py`](tests/test_docs.py) (docs and wire-format copies match the code) |
| [`deploy/`](deploy) | the Oracle cloud core as code: Terraform, cloud-init, compose, `update.sh`, SOPS secrets (written, never applied; see [`deploy/README.md`](deploy/README.md)) |
| [`docs/api/`](docs/api) | AsyncAPI for the two MQTT topics |
| [`docs/slo.md`](docs/slo.md), [`docs/dr.md`](docs/dr.md), [`docs/security.md`](docs/security.md), [`docs/regulatory.md`](docs/regulatory.md) | service levels, backups and drills, threat model and controls, regulatory framing (no compliance claims) |
| [`docs/decisions/`](docs/decisions) | why the main design choices were made |
| [`docs/production-topology.md`](docs/production-topology.md) | the planned enterprise-style chain (sensor, BLE, mobile gateway, cellular, cloud, EHR), with each hop marked built, planned or design-only; [`docs/licences.md`](docs/licences.md) lists every component's licence |
| [`docs/research/`](docs/research) | nRF / BLE / macOS environment notes with sources |

### What was measured and what was not

| Status | Parts | Meaning |
|---|---|---|
| **Measured** | [`firmware/ehg_node/`](firmware/ehg_node), the wire format, [`gateway/`](gateway), [`backend/ingest/`](backend/ingest), Mosquitto, InfluxDB, [`eval/`](eval) | The thesis results (H1 to H4) were taken on these |
| **Run** | gateway outbox, [`backend/signal_metrics/`](backend/signal_metrics), Grafana dashboard and alert rules, raw viewer, [`tools/fhir_export.py`](tools/fhir_export.py) | Started locally and seen working; no thesis result depends on them |
| **Configured, not validated** | MCUboot and the over-the-air update service in the firmware | Built into every image; no over-the-air upload has been performed |
| **Written** | [`backend/fhir_sync/`](backend/fhir_sync), [`backend/db/`](backend/db), [`ios/`](ios), [`deploy/`](deploy) | Code and unit tests only; never run against real servers; a design artefact and future work, not a result |
| **Removed 11 Nov 2026** | the triage service and page, the enrolment API, the MQTT-to-NATS bridge and TimescaleDB writer, the Mosquitto metrics exporter | Custom code that a standard tool already covers; deleted, and the tool is named in the [reuse ledger](docs/licences.md#reuse-ledger) |

Each part's existing off-the-shelf alternative is in the [reuse ledger](docs/licences.md#reuse-ledger).

## Choosing a run

`tools/run.sh` shows a menu, and Enter starts the default run. `tools/run.sh <name>` skips the menu, `--list` prints this table and `--dry-run <name>` prints the commands without running them. It asks before flashing the board, and you open Docker Desktop yourself.

| Run | Status | What it is |
|---|---|---|
| `default` | Thesis A default | Thesis A MVP deployed locally: nRF52840, Mac gateway, Mosquitto, InfluxDB, Grafana and the live viewer, with the outbox, alerts and signal metrics |
| `measured` | Tested on hardware | The run 3 configuration: seeded faults, no outbox |
| `faults` | Tested on hardware | Default plus seeded faults, to see re-request working. This mix with the outbox has not been run |
| `built-in` | In development | Recording compiled into the board |
| `simulate` | In development | Software node, no board |
| `status` | In development | Gateway online/offline messages |
| `fhir` | In development | FHIR R5 export of the last 10 minutes |
| `passkey` | In development | LESC passkey pairing |
| `button` | In development | Button 2 pairing, only exercised with the iPhone |
| `tls` | In development | MQTT over TLS 1.3 with client certificates |
| `iphone` | In development | iPhone app replaces the Mac gateway |
| `cloud` | Planned | Hosted InfluxDB and Grafana, not built yet |
| `keycloak`, `hapi-fhir`, `observability`, `oracle` | Not runnable locally yet | Exist only in `deploy/` |

Only `measured` and `faults` claim hardware testing. Signal metrics are descriptive statistics, not clinical information. Contraction identification is not included.

## Quickstart

```sh
# 0. once: Python env + data (~4 GB, PhysioNet AWS mirror, no login needed)
uv sync
aws s3 sync --no-sign-request s3://physionet-open/tpehgdb/1.0.1/ data/raw/tpehgdb/
aws s3 sync --no-sign-request s3://physionet-open/tpehgt/1.0.0/ data/raw/tpehgt/
aws s3 sync --no-sign-request s3://physionet-open/ehgdb/1.0.0/ data/raw/ehgdb/
aws s3 sync --no-sign-request s3://physionet-open/icehg-ds/1.0.1/ data/raw/icehg-ds/
(cd data/raw/tpehgdb && sed -E 's/^([0-9a-f]{64}) +/\1  /' SHA256SUMS.txt | shasum -a 256 -c --quiet)   # silent = every file intact; repeat per folder
uv run python tools/convert.py tpehgdb        # also: tpehgt | icehg | ehgdb; --bits 24 for >16-bit sources

# 1. firmware: build + flash the DK once (no recording inside), then stream a recording into it
firmware/build.sh flash                       # uses ~/thesisb symlink: NCS can't build from paths with spaces
uv run python tools/feed.py data/converted/tpehgdb/tpehg1007.npz   # leave running; prints the board log
#    or stand-alone, no laptop feed: firmware/build.sh flash --record data/converted/tpehgdb/tpehg1007.npz
#    (recording compiled in; optional clock pings: tools/feed.py --sync-only --node EHG-93C0) — docs/firmware.md
#    optional: --faults drop=0.05,burst=3,corrupt=0.01,disc=120 (docs/fault-injection.md)
#    sampling starts when the gateway connects (START) or with DK Button 1; firmware updates over
#    Bluetooth: build/dfu_application.zip via the nRF Device Manager app (docs/firmware.md Stage F);
#    configured, not validated: no over-the-air upload has been performed

# 2. backend
[ -f backend/.env ] || cp backend/.env.example backend/.env   # never overwrite an existing .env: InfluxDB keeps the token it first started with
(cd backend && docker compose up -d)
#    Grafana http://localhost:3300 (user admin, GRAFANA_ADMIN_PASSWORD in backend/.env) → EHG overview. Influx http://localhost:8086

# 3. gateway (the macOS terminal app needs Bluetooth permission, see docs/progress.md)
uv run python -m gateway.gateway              # or: --simulate 3 --sim-loss 0.01 (no hardware)
uv run python viewer/server.py                # http://localhost:8765

# 4. evaluate
uv run python eval/latency.py --node EHG-93C0 --since 1h
uv run python eval/integrity.py --node EHG-93C0 --since 1h
uv run python tools/fhir_export.py --node EHG-93C0 --since 10m -o window.json   # FHIR R5 export
uv run python eval/run_check.py --watch 30     # live PASS/FAIL beside a running demo: arriving, delivery, bit-exact, p95, outbox

# tests (also run on every push: .github/workflows/tests.yml)
uv run pytest -q
# the default run on the real board, 5 min, judged by eval/run_check.py (Terminal.app only; CI skips it)
EHG_HIL=1 uv run pytest -m hardware -s
```

The DK serial port is VCOM0 (`/dev/cu.usbmodem0010502082941`, 460800 baud). It carries the recording in (from `tools/feed.py`) and the board's log out, so `feed.py` prints the log: link parameters and, every 10 s, `sent / resent / ring_overflow / starved / max_inject_late`. Only one program can open the port.

### Cloud profile locally (optional): MQTT over TLS 1.3 with client certificates

The default stack above is unchanged (plain MQTT on 1883, this computer only). The cloud profile is opt-in and needs OpenSSL 3 (`brew install openssl@3`); background in [docs/security.md](docs/security.md) and [decision 0008](docs/decisions/0008-mqtt-mtls-and-topic-acls.md).

```sh
tools/pki.sh init --host localhost            # root, intermediate, broker + client certificates in backend/pki (gitignored)
(cd backend && docker compose -f docker-compose.yml -f docker-compose.cloud.yml up -d)   # broker on 127.0.0.1:8883, no 1883
uv run python -m gateway.gateway --simulate 1 --mqtt-tls --ca backend/pki/ca-chain.crt \
    --cert backend/pki/clients/ehg-gw-01.crt --key backend/pki/clients/ehg-gw-01.key
EHG_INTEGRATION=1 uv run pytest tests/test_mqtt_tls_config.py -q   # also tests a real local broker (brew install mosquitto)
```

Each component has its own certificate: the ingest container uses `ehg-ingest` (set by the override), the viewer needs `MQTT_TLS=1 MQTT_CA=… MQTT_CERT=… MQTT_KEY=…` ([`viewer/server.py`](viewer/server.py)).
The containers of this profile have **not been started** yet (Docker was not running when this was written); the broker config and ACL were tested against a local Mosquitto 2.1.2 ([docs/security.md](docs/security.md)).

### Gateway outbox (on by default): survive broker outages and restarts

```sh
uv run python -m gateway.gateway --simulate 1 --gateway-id ehg-gw-01   # the outbox is gateway/outbox.db; --gateway-id adds retained online/offline status
uv run python -m gateway.gateway --simulate 1 --no-outbox              # the old behaviour: messages wait only in the MQTT client's memory
```

Every packet is written to the SQLite file first and deleted only when the broker confirms it; after an outage or a restart the waiting packets go out oldest first, marked `"replayed": true`
([decision 0009](docs/decisions/0009-gateway-durable-outbox.md), `eval/latency.py --exclude-replayed`). `--outbox-max-mb` (default 512) caps the file: over it the oldest unsent packets are dropped and named in the log.
One gateway process per outbox file (a second one stops with a message; give it `--outbox OTHER.db`). Left-over packets from an earlier run are sent at the next start, marked replayed; delete `gateway/outbox.db` first if you do not want them.
It costs time on the gateway: about 0.5 ms more between "gateway received" and "published" ([progress.md](docs/progress.md), 2026-11-03).

### FHIR sync worker (optional; NEVER RUN against real servers)

```sh
uv run python -m backend.fhir_sync.worker        # needs EHG_DB_DSN, FHIR_BASE_URL (a HAPI R5 server, config in deploy/hapi/) and the converted .npz folder; copies only nodes whose subject has active consent
```

Tested against a fake FHIR server (`tests/test_fhir_*.py`); never run against HAPI. It reads TimescaleDB, and since 11 Nov 2026 nothing in this repository fills TimescaleDB (the custom bridge and writer were removed; Telegraf's `mqtt_consumer` input with its PostgreSQL output is the standard tool for that step, and it is not configured here).

### Wire format in Swift, observability and backups

```sh
cd ios/EHGWire && swift test                               # decodes tests/fixtures/packet_vectors.json; command line only, no Xcode project or phone
uv run python tools/gen_packet_vectors.py                  # regenerate the vectors after any wire-format, gap-tracker or clock-map change (all copies change together)
deploy/backup/backup.sh --dry-run --out /tmp/ehg-backups   # prints the plan; restore_drill.sh --backup DIR restores onto a CLEAN server and times it
```

Alert rules and SLO recording rules: [`deploy/observability/rules/`](deploy/observability/rules) ([`docs/slo.md`](docs/slo.md)); the dashboard is provisioned from [`deploy/observability/grafana/dashboards/`](deploy/observability/grafana/dashboards). None has been evaluated by a running Prometheus or Grafana ([`docs/dr.md`](docs/dr.md) says what was and was not tested).
The VM itself: [`docs/deployment.md`](docs/deployment.md) (15 steps for Byron; nothing has been applied).

## Reproducing the results

Each row: what to run (after Quickstart steps 0 and 2: data converted, backend up), and what the reader should see.
`eval/*.py` print the lines quoted below; `--since` must cover the run.

| Claim | Setup | Commands | Expected output (recorded run) |
|---|---|---|---|
| Tooling works, **no hardware** | 1 simulated node, 1 % packet loss, ~5 min | `uv run python -m gateway.gateway --simulate 1 --sim-loss 0.01`, then `eval/integrity.py --node SIM-0000 --since 10m` and `eval/latency.py --node SIM-0000 --since 10m` | `delivery … = 100.0000 %`, `bit-exact mismatches: 0`, median ≈ 252 ms, p95 ≈ 459 ms (2026-09-29, before wire v2). Not an H1/H2 result: there is no Bluetooth hop, and ≈ 450 ms of it is batching |
| **H2** delivery > 99 %, every loss identified | DK EHG-93C0 serial feed, TPEHG tpehg1007, 7 min, faults `drop=0.05,burst=3,corrupt=0.01,disc=120,seed=3` | `tools/feed.py data/converted/tpehgdb/tpehg1007.npz --faults …` + `gateway.gateway`, then `eval/integrity.py --node EHG-93C0 --since 15m` | `delivery 8410/8410 = 100.0000 %`, `missing ranges (first 20): []` (run 3, 2026-10-20; 69 drops, 8 corruptions, 4 disconnects all recovered) |
| **H4** bit-exact at the interface | same run | same `eval/integrity.py` | `bit-exact mismatches: 0` |
| **H1** p95 < 2 s (stretch < 1 s) | same run; `feed.py` writes the clock pings `eval/results/sync_EHG-93C0.csv` | `eval/latency.py --node EHG-93C0 --since 15m` | `offset method: serial sync …`, `H1 per-sample end-to-end: … median=259 ms p95=478 ms …`, `target p95 < 2000 ms: PASS; stretch p95 < 1000 ms: PASS`, plus `eval/results/latency_cdf_EHG-93C0.png` |
| Wire format identical in C, Python and Swift | no hardware | `uv run pytest -q tests/test_ios_wire_format.py`, `cd ios/EHGWire && swift test` | 8 vectors decode bit-exact, 7 bad packets refused for the same reason, every bit flip refused |
| Backups restore | a local PostgreSQL (`brew install postgresql@14`) | `uv run pytest -q tests/test_backup_scripts.py` | dump, restore on a second clean server, `OK: 3 rows`, damaged backup refused |
| SLO numbers, failure drills | needs Docker and the VM | [`docs/dr.md`](docs/dr.md) | **not measured yet** |
| **H3** ≥ 8 boards at once | needs 8 boards (one DK here) | `gateway.gateway --nodes 8`; code path exercised by `--simulate 8` | not measured yet ([progress.md](docs/progress.md)) |

All results per run: [`docs/progress.md`](docs/progress.md). When a step fails: [`docs/troubleshooting.md`](docs/troubleshooting.md).

## Licence and citing

Licence: **pending** (awaiting supervisor confirmation); until a `LICENSE` file is added, default copyright applies.
To cite this software, use [`CITATION.cff`](CITATION.cff) (GitHub shows it as "Cite this repository").
