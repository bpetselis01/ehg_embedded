# EHG telemetry pipeline

Wireless telemetry for electrohysterography (EHG): an nRF52840 node streams multichannel EHG over Bluetooth LE to a gateway, which publishes it over MQTT into InfluxDB for live dashboards and evaluation.

**Intended purpose:** research prototype for measuring wireless telemetry performance. Not for clinical decision-making. Not supplied to any person for patient care. Makes no diagnostic or predictive claims. See [docs/regulatory.md](docs/regulatory.md).

```
EHG record ─▶ nRF52840 (Zephyr) ─BLE notify─▶ gateway (Bleak) ─MQTT QoS 1─▶ ingest ─▶ InfluxDB 2.7 ─▶ Grafana
                                                                          └─▶ WebSocket raw viewer
```

## Requirements

- macOS with Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Docker Desktop
- nRF52840 DK and nRF Connect SDK v3.4.1 (firmware only)
- `mosquitto_sub` for the demo (`brew install mosquitto`)

## Quick start

```sh
uv sync                                          # Python dependencies
uv run python tools/convert.py tpehgdb           # convert a downloaded PhysioNet record set to .npz
[ -f backend/.env ] || cp backend/.env.example backend/.env
(cd backend && docker compose up -d)             # Mosquitto, InfluxDB, ingest, Grafana
tools/run.sh default                             # flash if needed, then open the demo windows
```

Run `tools/run.sh` from **Terminal.app**, the only app with Bluetooth permission. It opens one window per stage of the pipeline:

| Window | Shows |
|---|---|
| 1 Board + feeder | The recording streamed into the board; board counters every 10 s |
| 2 Bluetooth | Each packet the gateway receives |
| 3 Gateway | Link health every 10 s: received, missing, duplicates, bad checksums |
| 4 MQTT | Each message the broker passes on |
| 5 Database | Each packet stored, with its end-to-end latency |
| 6 Viewer server | Serves the raw viewer at http://localhost:8765 |
| 7 Metrics | Per-channel signal descriptors every 30 s |

Grafana: http://localhost:3300/d/ehg-overview (user `admin`, password `GRAFANA_ADMIN_PASSWORD` in `backend/.env`).

Live pass/fail against the targets:

```sh
uv run python eval/run_check.py --watch 30
```

Stop the gateway (window 3) first with Ctrl-C, so it sends STOP to the board, then the other windows.

## Runs

`tools/run.sh --list` shows every run and its status. `tools/run.sh --dry-run NAME` prints its commands without running them.

| Run | Status | What it is |
|---|---|---|
| `default` | Tested on hardware | Local pipeline with the live viewer, signal metrics and alerts |
| `measured` | Tested on hardware | Seeded link faults, outbox off |
| `faults` | Tested on hardware | Default plus seeded link faults ([fault injection](docs/fault-injection.md)) |
| `built-in` | In development | Recording compiled into the board, no laptop feed |
| `simulate` | In development | Software node, no board or Bluetooth |
| `status` | In development | Gateway online/offline messages |
| `fhir` | In development | FHIR R5 export of the last 10 minutes |
| `passkey` | In development | LESC passkey pairing |
| `button` | In development | Button 2 pairing |
| `tls` | In development | MQTT over TLS 1.3 with client certificates |
| `iphone` | In development | iPhone app replaces the Mac gateway |
| `cloud` | Planned | Hosted InfluxDB and Grafana |
| `keycloak`, `hapi-fhir`, `observability`, `oracle` | Cloud only | Defined in [`deploy/`](deploy) |

## Firmware

```sh
firmware/build.sh flash                          # build and flash the DK
firmware/build.sh flash --record data/converted/tpehgdb/tpehg1007.npz   # recording compiled in
```

The DK serial port (VCOM0, 460800 baud) carries the recording in and the board log out. See [firmware/README.md](firmware/README.md).

## Evaluation

```sh
uv run python eval/latency.py --node EHG-93C0 --since 1h     # end-to-end latency, p95
uv run python eval/integrity.py --node EHG-93C0 --since 1h   # delivery and bit-exact check
```

## Tests

```sh
uv run pytest -q                                 # host code; also runs in CI on every push
EHG_HIL=1 uv run pytest -m hardware -s           # default run on the real board (Terminal.app)
cd ios/EHGWire && swift test                     # wire format in Swift
```

## Project layout

| Path | Contents |
|---|---|
| [`firmware/ehg_node/`](firmware/ehg_node) | Zephyr application for `nrf52840dk/nrf52840` |
| [`gateway/`](gateway) | Bluetooth LE central and MQTT publisher |
| [`backend/`](backend) | Docker Compose stack, ingest service, signal metrics, Grafana provisioning |
| [`viewer/`](viewer) | WebSocket raw-signal viewer |
| [`eval/`](eval) | Latency, integrity and live run checks |
| [`tools/`](tools) | Data conversion, serial feeder, demo launcher |
| [`ios/`](ios) | Swift wire format and iPhone gateway |
| [`deploy/`](deploy) | Cloud deployment as code |
| [`tests/`](tests) | pytest suite |

Every source file is listed in [docs/codebase-guide.md](docs/codebase-guide.md).

## Documentation

- [Troubleshooting](docs/troubleshooting.md)
- [Deployment](docs/deployment.md) and [disaster recovery](docs/dr.md)
- [Security](docs/security.md) and [supply chain](docs/supply-chain.md)
- [Architecture decisions](docs/decisions)
- [iPhone gateway](docs/ios-gateway.md)
- [Third-party licences](docs/licences.md)

## Licence and citation

Licence: pending. Until a `LICENSE` file is added, default copyright applies. To cite this software, use [CITATION.cff](CITATION.cff).
