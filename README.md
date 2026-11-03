# EHG telemetry pipeline: Thesis B (BIOM4951)

**Intended purpose:** research prototype for measuring wireless telemetry performance. Not for clinical decision-making. Not supplied to any person for patient care. Makes no diagnostic or predictive claims. It uses public, de-identified recordings only.

An open, reproducible wireless telemetry and cloud monitoring pipeline for electrohysterography (EHG):

```
public EHG record ─▶ nRF52840 DK (Zephyr, digital injection) ─BLE GATT notify─▶ Bleak gateway ─MQTT QoS1─▶ ingest ─▶ InfluxDB 2.7 ─▶ Grafana
                                                                                                    └─────▶ WebSocket raw viewer
```

The validated chain above is what the thesis results (H1 to H4) were measured on. Around it sits an **enterprise-style evolution** (mutual-TLS MQTT, a durable bus, TimescaleDB, a triage service and page, a reference FHIR server, observability, backups, infrastructure as code, a Swift wire library),
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
| [`backend/`](backend) | docker compose: Mosquitto, InfluxDB 2.7, ingest, Grafana (provisioned dashboard); optional `platform` profile; Python services: [`bridge/`](backend/bridge), [`ts_writer/`](backend/ts_writer), [`triage/`](backend/triage) (rules, alerts, audit, API), [`enroll/`](backend/enroll), [`fhir_sync/`](backend/fhir_sync), [`db/`](backend/db) (migrations) |
| [`viewer/`](viewer) | WebSocket raw multichannel viewer; the [triage page](viewer/triage.html) (`triage.html`, `.css`, `.js`) |
| [`ios/EHGWire/`](ios/EHGWire) | Swift package: the wire format for the future iPhone gateway (`swift test` on the command line; the app itself is not written) |
| [`eval/`](eval) | [`latency.py`](eval/latency.py) (H1), [`integrity.py`](eval/integrity.py) (H2 + H4 bit-exact) |
| [`papers/`](papers) | [`INDEX.md`](papers/INDEX.md) (tracked) + open-access PDFs (not tracked) |
| [`tests/`](tests) | pytest for the host code, plus [`test_docs.py`](tests/test_docs.py) (docs and wire-format copies match the code) |
| [`deploy/`](deploy) | the Oracle cloud core as code: Terraform, cloud-init, compose, `update.sh`, SOPS secrets (written, never applied; see [`deploy/README.md`](deploy/README.md)) |
| [`docs/api/`](docs/api) | OpenAPI (triage, enrolment) exported from the code, and AsyncAPI for the MQTT topics and bus subjects; both checked by tests |
| [`docs/slo.md`](docs/slo.md), [`docs/dr.md`](docs/dr.md), [`docs/security.md`](docs/security.md), [`docs/regulatory.md`](docs/regulatory.md) | service levels, backups and drills, threat model and controls, regulatory framing (no compliance claims) |
| [`docs/decisions/`](docs/decisions) | why the main design choices were made |
| [`docs/production-topology.md`](docs/production-topology.md) | the planned enterprise-style chain (sensor, BLE, mobile gateway, cellular, cloud, EHR), with each hop marked built, planned or design-only; [`docs/licences.md`](docs/licences.md) lists every component's licence |
| [`docs/research/`](docs/research) | nRF / BLE / macOS environment notes with sources |

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
#    Bluetooth: build/dfu_application.zip via the nRF Device Manager app (docs/firmware.md Stage F)

# 2. backend
cp backend/.env.example backend/.env && (cd backend && docker compose up -d)
#    Grafana http://localhost:3300 (admin/admin) → EHG overview. Influx http://localhost:8086

# 3. gateway (the macOS terminal app needs Bluetooth permission, see docs/progress.md)
uv run python -m gateway.gateway              # or: --simulate 3 --sim-loss 0.01 (no hardware)
uv run python viewer/server.py                # http://localhost:8765

# 4. evaluate
uv run python eval/latency.py --node EHG-93C0 --since 1h
uv run python eval/integrity.py --node EHG-93C0 --since 1h
uv run python tools/fhir_export.py --node EHG-93C0 --since 10m -o window.json   # FHIR R5 export

# tests (also run on every push: .github/workflows/tests.yml)
uv run pytest -q
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

### Data platform (optional profile): NATS + TimescaleDB beside InfluxDB

```sh
cd backend && docker compose --profile platform -f docker-compose.yml -f docker-compose.platform.yml up -d     # needs Docker; the default `docker compose up -d` is unchanged (4 containers)
docker compose stop ingest                                   # so InfluxDB is fed only from the stream
uv run python -m gateway.gateway --simulate 1 --sim-loss 0.01 --no-outbox   # a few minutes, then Ctrl-C
EHG_TS_DSN="host=localhost port=5433 dbname=ehg user=ehg password=local-only-change-me" uv run python eval/parity.py --node SIM-0000 --since 10m
```

`eval/integrity.py`, `eval/latency.py` and `tools/fhir_export.py` take `--backend influx|timescale`. Nothing here has run against real NATS/TimescaleDB yet ([decision 0012](docs/decisions/0012-timescaledb-nats-data-platform.md)); the SQL, bridge and writer logic is tested with fakes and a local PostgreSQL.

### Gateway outbox (on by default): survive broker outages and restarts

```sh
uv run python -m gateway.gateway --simulate 1 --gateway-id ehg-gw-01   # the outbox is gateway/outbox.db; --gateway-id adds retained online/offline status
uv run python -m gateway.gateway --simulate 1 --no-outbox              # the old behaviour: messages wait only in the MQTT client's memory
```

Every packet is written to the SQLite file first and deleted only when the broker confirms it; after an outage or a restart the waiting packets go out oldest first, marked `"replayed": true`
([decision 0009](docs/decisions/0009-gateway-durable-outbox.md), `eval/latency.py --exclude-replayed`). `--outbox-max-mb` (default 512) caps the file: over it the oldest unsent packets are dropped and named in the log.
One gateway process per outbox file (a second one stops with a message; give it `--outbox OTHER.db`). Left-over packets from an earlier run are sent at the next start, marked replayed; delete `gateway/outbox.db` first if you do not want them.
It costs time on the gateway: about 0.5 ms more between "gateway received" and "published" ([progress.md](docs/progress.md), 2026-11-03).

### Triage service, page and reference EHR (optional; NEVER RUN against real servers)

```sh
# needs the data platform profile above (Docker). Role ehg_triage is created by deploy/postgres/10-init-databases.sh; the migrate job creates the tables.
export AUTH_MODE=dev  # fixed dev users, loud banner, refuses to bind anywhere but 127.0.0.1; use AUTH_MODE=oidc with OIDC_ISSUER, OIDC_AUDIENCE, OIDC_JWKS_URL for real sign-in
export EHG_TRIAGE_DSN="host=localhost port=5433 dbname=ehg user=ehg_triage password=local-only-change-me" NATS_URL=nats://localhost:4222
uv run python -m backend.triage.stream           # rules + API + page on http://127.0.0.1:8780/  (pick a dev user; roles viewer < operator < admin)
uv run python -m backend.fhir_sync.worker        # needs EHG_DB_DSN, FHIR_BASE_URL (a HAPI R5 server, config in deploy/hapi/) and the converted .npz folder; copies only nodes whose subject has active consent
```

What IS tested: every route and role, the consent rule, the page's static safety rules and a hand run of the page in a browser against stub data, the sync worker against a fake FHIR server (`tests/test_triage_*.py`, `test_fhir_*.py`). What is NOT: the service against real NATS and TimescaleDB, HAPI, Keycloak ([docs/progress.md](docs/progress.md)).
The API documents: [`docs/api/openapi-triage.json`](docs/api/openapi-triage.json); refresh with `uv run python tools/export_api_docs.py`.

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
| Backups restore, audit chain intact | a local PostgreSQL (`brew install postgresql@14`) | `uv run pytest -q tests/test_backup_scripts.py` | dump, restore on a second clean server, `OK: 3 rows`, damaged backup refused |
| Parity of TimescaleDB with InfluxDB, SLO numbers, failure drills | needs Docker and the VM | see above and [`docs/dr.md`](docs/dr.md) | **not measured yet** |
| **H3** ≥ 8 boards at once | needs 8 boards (one DK here) | `gateway.gateway --nodes 8`; code path exercised by `--simulate 8` | not measured yet ([progress.md](docs/progress.md)) |

All results per run: [`docs/progress.md`](docs/progress.md). When a step fails: [`docs/troubleshooting.md`](docs/troubleshooting.md).

## Licence and citing

Licence: **pending** (awaiting supervisor confirmation); until a `LICENSE` file is added, default copyright applies.
To cite this software, use [`CITATION.cff`](CITATION.cff) (GitHub shows it as "Cite this repository").
