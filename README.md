# EHG telemetry pipeline: Thesis B (BIOM4951)

An open, reproducible wireless telemetry and cloud monitoring pipeline for electrohysterography (EHG):

```
public EHG record ─▶ nRF52840 DK (Zephyr, digital injection) ─BLE GATT notify─▶ Bleak gateway ─MQTT QoS1─▶ ingest ─▶ InfluxDB 2.7 ─▶ Grafana
                                                                                                    └─────▶ WebSocket raw viewer
```

Plain-language code walkthrough: `docs/codebase-guide.md`. Design: `docs/design/codebase-design.md`. Wire format: `docs/interface-spec.md`. Progress and open items: `docs/progress.md`.

## Layout

| Path | What |
|---|---|
| `tools/` | `convert.py` (WFDB → int16 `.npz`), `make_replay.py` (`.npz` → firmware replay header) |
| `firmware/ehg_node/` | Zephyr app for `nrf52840dk/nrf52840`, NCS v3.4.1. `firmware/build.sh` builds and flashes it |
| `gateway/` | Bleak central → MQTT; `--simulate N` software nodes use the same wire format |
| `backend/` | docker compose: Mosquitto, InfluxDB 2.7, ingest, Grafana (provisioned dashboard) |
| `viewer/` | WebSocket raw multichannel viewer |
| `eval/` | `latency.py` (H1), `integrity.py` (H2 + H4 bit-exact) |
| `papers/` | `INDEX.md` (tracked) + open-access PDFs (not tracked) |
| `docs/research/` | nRF / BLE / macOS environment notes with sources |

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

# 1. firmware: bake one record into flash, build, flash the DK
uv run python tools/make_replay.py data/converted/tpehgdb/tpehg1007.npz --record-id 0
firmware/build.sh flash                       # uses ~/thesisb symlink: NCS can't build from paths with spaces

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

The DK serial log is on VCOM0 (`/dev/cu.usbmodem0010502082941`, 115200). It prints link parameters and, every 10 s, `sent / resent / ring_overflow / max_inject_late`.
