# EHG telemetry pipeline: Thesis B (BIOM4951)

An open, reproducible wireless telemetry and cloud monitoring pipeline for electrohysterography (EHG):

```
public EHG record ─▶ nRF52840 DK (Zephyr, digital injection) ─BLE GATT notify─▶ Bleak gateway ─MQTT QoS1─▶ ingest ─▶ InfluxDB 2.7 ─▶ Grafana
                                                                                                    └─────▶ WebSocket raw viewer
```

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
| [`backend/`](backend) | docker compose: Mosquitto, InfluxDB 2.7, ingest, Grafana (provisioned dashboard) |
| [`viewer/`](viewer) | WebSocket raw multichannel viewer |
| [`eval/`](eval) | [`latency.py`](eval/latency.py) (H1), [`integrity.py`](eval/integrity.py) (H2 + H4 bit-exact) |
| [`papers/`](papers) | [`INDEX.md`](papers/INDEX.md) (tracked) + open-access PDFs (not tracked) |
| [`tests/`](tests) | pytest for the host code, plus [`test_docs.py`](tests/test_docs.py) (docs and wire-format copies match the code) |
| [`deploy/`](deploy) | the Oracle cloud core as code: Terraform, cloud-init, compose, `update.sh`, SOPS secrets (written, never applied; see [`deploy/README.md`](deploy/README.md)) |
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

### Gateway outbox (on by default): survive broker outages and restarts

```sh
uv run python -m gateway.gateway --simulate 1 --gateway-id ehg-gw-01   # the outbox is gateway/outbox.db; --gateway-id adds retained online/offline status
uv run python -m gateway.gateway --simulate 1 --no-outbox              # the old behaviour: messages wait only in the MQTT client's memory
```

Every packet is written to the SQLite file first and deleted only when the broker confirms it; after an outage or a restart the waiting packets go out oldest first, marked `"replayed": true`
([decision 0009](docs/decisions/0009-gateway-durable-outbox.md), `eval/latency.py --exclude-replayed`). `--outbox-max-mb` (default 512) caps the file: over it the oldest unsent packets are dropped and named in the log.
One gateway process per outbox file (a second one stops with a message; give it `--outbox OTHER.db`). Left-over packets from an earlier run are sent at the next start, marked replayed; delete `gateway/outbox.db` first if you do not want them.
It costs time on the gateway: about 0.5 ms more between "gateway received" and "published" ([progress.md](docs/progress.md), 2026-11-03).

## Reproducing the results

Each row: what to run (after Quickstart steps 0 and 2: data converted, backend up), and what the reader should see.
`eval/*.py` print the lines quoted below; `--since` must cover the run.

| Claim | Setup | Commands | Expected output (recorded run) |
|---|---|---|---|
| Tooling works, **no hardware** | 1 simulated node, 1 % packet loss, ~5 min | `uv run python -m gateway.gateway --simulate 1 --sim-loss 0.01`, then `eval/integrity.py --node SIM-0000 --since 10m` and `eval/latency.py --node SIM-0000 --since 10m` | `delivery … = 100.0000 %`, `bit-exact mismatches: 0`, median ≈ 252 ms, p95 ≈ 459 ms (2026-09-29, before wire v2). Not an H1/H2 result: there is no Bluetooth hop, and ≈ 450 ms of it is batching |
| **H2** delivery > 99 %, every loss identified | DK EHG-93C0 serial feed, TPEHG tpehg1007, 7 min, faults `drop=0.05,burst=3,corrupt=0.01,disc=120,seed=3` | `tools/feed.py data/converted/tpehgdb/tpehg1007.npz --faults …` + `gateway.gateway`, then `eval/integrity.py --node EHG-93C0 --since 15m` | `delivery 8410/8410 = 100.0000 %`, `missing ranges (first 20): []` (run 3, 2026-10-20; 69 drops, 8 corruptions, 4 disconnects all recovered) |
| **H4** bit-exact at the interface | same run | same `eval/integrity.py` | `bit-exact mismatches: 0` |
| **H1** p95 < 2 s (stretch < 1 s) | same run; `feed.py` writes the clock pings `eval/results/sync_EHG-93C0.csv` | `eval/latency.py --node EHG-93C0 --since 15m` | `offset method: serial sync …`, `H1 per-sample end-to-end: … median=259 ms p95=478 ms …`, `target p95 < 2000 ms: PASS; stretch p95 < 1000 ms: PASS`, plus `eval/results/latency_cdf_EHG-93C0.png` |
| **H3** ≥ 8 boards at once | needs 8 boards (one DK here) | `gateway.gateway --nodes 8`; code path exercised by `--simulate 8` | not measured yet ([progress.md](docs/progress.md)) |

All results per run: [`docs/progress.md`](docs/progress.md). When a step fails: [`docs/troubleshooting.md`](docs/troubleshooting.md).

## Licence and citing

Licence: **pending** (awaiting supervisor confirmation); until a `LICENSE` file is added, default copyright applies.
To cite this software, use [`CITATION.cff`](CITATION.cff) (GitHub shows it as "Cite this repository").
