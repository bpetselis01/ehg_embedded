# EHG telemetry pipeline: Thesis B (BIOM4951)

An open, reproducible wireless telemetry and cloud monitoring pipeline for electrohysterography (EHG):

```
public EHG record ─▶ nRF52840 DK (Zephyr, digital injection) ─BLE GATT notify─▶ Bleak gateway ─MQTT QoS1─▶ ingest ─▶ InfluxDB 2.7 ─▶ Grafana
                                                                                                    └─────▶ WebSocket raw viewer
```

Plain-language code walkthrough: [`docs/codebase-guide.md`](docs/codebase-guide.md). Design: [`docs/design/codebase-design.md`](docs/design/codebase-design.md). Wire format: [`docs/interface-spec.md`](docs/interface-spec.md). Progress and open items: [`docs/progress.md`](docs/progress.md).

## Layout

| Path | What |
|---|---|
| [`tools/`](tools) | [`convert.py`](tools/convert.py) (WFDB → int16 `.npz`), [`feed.py`](tools/feed.py) (streams a `.npz` into the board over serial) |
| [`firmware/ehg_node/`](firmware/ehg_node) | Zephyr app for `nrf52840dk/nrf52840`, NCS v3.4.1. [`firmware/build.sh`](firmware/build.sh) builds and flashes it |
| [`gateway/`](gateway) | Bleak central → MQTT; `--simulate N` software nodes use the same wire format |
| [`backend/`](backend) | docker compose: Mosquitto, InfluxDB 2.7, ingest, Grafana (provisioned dashboard) |
| [`viewer/`](viewer) | WebSocket raw multichannel viewer |
| [`eval/`](eval) | [`latency.py`](eval/latency.py) (H1), [`integrity.py`](eval/integrity.py) (H2 + H4 bit-exact) |
| [`papers/`](papers) | [`INDEX.md`](papers/INDEX.md) (tracked) + open-access PDFs (not tracked) |
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

# tests
uv run pytest -q
```

The DK serial port is VCOM0 (`/dev/cu.usbmodem0010502082941`, 460800 baud). It carries the recording in (from `tools/feed.py`) and the board's log out, so `feed.py` prints the log: link parameters and, every 10 s, `sent / resent / ring_overflow / starved / max_inject_late`. Only one program can open the port.
