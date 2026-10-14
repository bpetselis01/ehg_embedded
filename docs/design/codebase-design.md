# Thesis B codebase: design spec

Date: 2026-09-29 · Author: Byron Petselis · Status: **self-approved, needs Byron's review**

Source of truth for intent: *BIOM4951 Interim Report* (Thesis A, 10 Aug 2026), read in full (all 34 pages, including Fig. 10 architecture and Fig. 12 Gantt chart).

## 1. Understanding

**What Byron said (report + request):**
- Build and quantitatively evaluate an open, reproducible EHG telemetry pipeline: dataset replay → nRF52840 (Zephyr/NCS) → BLE GATT notify (2M PHY, DLE, batching, seq numbers, timestamps) → Python Bleak gateway → Mosquitto (QoS 1) → ingestion → InfluxDB (pinned pre-3.x) → Grafana (overview, alerts) + custom WebSocket raw-waveform viewer.
- Main replay source TPEHG (3 ch, 20 Hz, 16-bit). Stretch source Icelandic EHGDB (16 ch, 200 Hz). Full fidelity is transported; decimation only downstream.
- Hypotheses: H1 latency p95 < 2 s (stretch < 1 s), injection → queryable in InfluxDB; H2 > 99 % delivery (stretch 99.9 %) over ≥ 6 h soak, every loss identifiable by seq number, with bundling and re-request; H3 ≥ 8 concurrent nodes (stretch); H4 bit-exact at the interface, ≥ 99 % label agreement (stretch).
- Zicheng's classifier contract: 3 TPEHG channels, 20 Hz, zero-phase 0.08–4 Hz band-pass, 180 s end-crop, 60 s windows / 30 s step. Used unchanged as an optional server-side hook.
- Gantt chart: Phase 0 (hardware, toolchain, dataset conversions, interface spec agreement) runs Sep; Phase 1 (injection, ring buffer, GATT, seq + timestamps) runs Oct to mid-Nov; Phase 2 (Bleak central, MQTT, InfluxDB) runs Oct to Dec. Thesis B ends 10 Dec 2026.
- The DK is set up and tested (blinky and peripheral_uart work). Byron wants maximum autonomous progress in this folder.

**Assumptions I made (Byron to confirm):**
- A1. One monorepo in `Thesis B/`, local git only, with no GitHub remote until Byron adds one.
- A2. The MVP runs the backend locally in Docker. Cloud deployment (Phase 3a) is documented but not provisioned, because it needs Byron's accounts and money decisions.
- A3. InfluxDB **2.7** (OSS, pinned). The report says "an earlier, fixed version" than 3 Core, and 2.7 is the stable line with first-class Grafana support.
- A4. TPEHG transport uses the **unfiltered** signals of the three bipolar channels as raw int16 ADC units. Filtering is a downstream concern (Zicheng's pipeline filters itself), and raw units make bit-exact H2/H4 checks trivial.
- A5. Digital injection = the record is compiled into flash as a const int16 array (one TPEHG record ≈ 216 KB and fits in 1 MB flash). For the 6 h soak it loops. The Icelandic stretch set needs ≈ 6.4 KB/s, so only ~2 min fits in flash. Streaming it over UART is deferred.
- A6. The interface spec written here is a **draft for agreement with Zicheng** (Gantt task 0d), not agreed.
- A7. Zicheng's classifier code is not in this folder, so the H4 classification hook is not built. Only the bit-exact half of H4 is implemented.
- A8. H3 needs 8 physical nodes. With one DK this is deferred; the firmware and gateway support N connections, so it is a hardware question only.

**Success criteria for this session:** Phase 0 complete. A working single-node MVP of phases 1–2 on the real DK: TPEHG record → BLE → gateway → MQTT → InfluxDB → Grafana, with H1/H2 evaluation scripts producing numbers from a short run. All documented well enough that Byron (or a future student) can reproduce it from the README.

## 2. Repository layout

```
Thesis B/
  README.md            quickstart + phase status
  pyproject.toml       one uv-managed Python env for all host code
  docs/                spec, plan, interface-spec, setup, research notes
  papers/              open-access PDFs (gitignored) + INDEX.md (tracked)
  data/raw/            PhysioNet downloads (gitignored)
  data/converted/      per-record .npz + manifest (gitignored, reproducible)
  tools/               dataset conversion + firmware replay-array generator
  firmware/ehg_node/   Zephyr app (NCS v3.4.1, nrf52840dk/nrf52840)
  gateway/             Bleak central → MQTT
  backend/             docker-compose: mosquitto, influxdb, grafana, ingest
  viewer/              WebSocket raw-waveform viewer (after MVP)
  eval/                H1/H2 analysis scripts
  tests/               pytest for host-side logic
```

## 3. Wire format (the interface every layer shares)

A BLE notification carries one packet, little-endian:

| off | type | field | notes |
|---|---|---|---|
| 0 | u8 | version | = 1 |
| 1 | u8 | n_ch | 3 (TPEHG) or 16 (EHGDB) |
| 2 | u16 | fs_hz | 20 / 200 |
| 4 | u32 | seq | packet counter, starts at 0 on stream start |
| 8 | u32 | frame_idx | absolute frame counter since stream start. Source index = `frame_idx % record_len` |
| 12 | u32 | t_inject | device tick (32 768 Hz) when frame 0 was injected. Wraps after 36 h |
| 16 | u8 | n_frames | frames in this packet |
| 17 | u8 | flags | bit0 = retransmission |
| 18 | u16 | record_id | index into the replay manifest |
| 20 | i16[n_frames·n_ch] | samples | frame-major (f0c0, f0c1, …) |

- Batch size is a Kconfig (`CONFIG_EHG_FRAMES_PER_PACKET`, default 10 → 0.5 s at 20 Hz, 80-byte packet).
- The control characteristic (write) takes `op u8`: `0x01` start, `0x02` stop, `0x03` resend `{seq u32, count u16}`. Firmware keeps the last `CONFIG_EHG_HISTORY_PACKETS` (default 256) packets for resend.
- Host-side MQTT message (topic `ehg/<node_id>/pkt`, QoS 1) is JSON: the decoded packet plus `node_id`, `t_inject_host` (device tick mapped to host epoch), `t_gw_rx`, `t_mqtt_pub`. JSON ints keep samples exact.

## 4. Components

- **tools/convert.py**: WFDB → `data/converted/<db>/<record>.npz` (int16 samples, fs, channel names, gain, baseline, units) + `manifest.json`. **tools/make_replay.py**: npz → `firmware/ehg_node/src/replay_data.c` (generated, gitignored).
- **firmware/ehg_node**: `replay.c` (k_timer at fs pushes frames into a ring buffer and stamps inject ticks), `ehg_service.c` (GATT service, packetiser, history, resend), `main.c` (BT init, advertise as `EHG-<id>`, request 2M PHY / DLE / MTU on connect, log stats on UART). It reuses NCS/Zephyr APIs and writes nothing custom where the stack already does it.
- **gateway/**: `packet.py` (parse/pack, shared by tests and eval), `gateway.py` (scan by name prefix, connect N nodes, subscribe, gap detection → resend request, clock mapping by min-offset envelope, per-notification CSV log for loss attribution, MQTT publish).
- **backend/**: `docker-compose.yml` (eclipse-mosquitto 2, influxdb 2.7, grafana, ingest). `ingest/ingest.py` subscribes `ehg/+/pkt` and writes the measurements `ehg_raw` (one point per frame, tags node_id/record, fields c0..cN, seq, frame_idx) and `ehg_pkt` (per packet: t_inject_host, t_gw_rx, t_mqtt_pub, t_db_ack). Grafana is provisioned with the datasource and a starter dashboard.
- **eval/**: `latency.py` (H1: ehg_pkt → median/p95/p99/max + CDF plot), `integrity.py` (H2/H4-bit-exact: reconcile ehg_raw against source npz; delivery %, gap list, mismatches).

## 5. Error handling

- Firmware ring-buffer overflow is counted and logged, never silent, and shows up downstream as a frame_idx discontinuity.
- Gateway: seq gap → resend request. A packet still missing after resend is logged as lost with its seq. Disconnect → reconnect loop.
- MQTT QoS 1 and persistent session. Ingest dedups on (node_id, seq), because QoS 1 can duplicate. Influx writes are idempotent on identical timestamp + tags anyway.

## 6. Testing

- Host: pytest for packet pack/parse round-trip, gap detection, clock mapping, reconciliation, and converter bit-exactness against wfdb.
- Firmware: builds cleanly, flashes, and advertises; the gateway receives packets whose samples equal the source record (the end-to-end check *is* the H2/H4 bit-exact check).
- Backend: `docker compose up` + ingest of a synthetic packet stream.

## 7. Out of scope now (and why)

Cloud deployment (needs accounts), multi-node H3 (needs hardware), classifier hook (needs Zicheng's code), PPK2 energy (needs the PPK2), DAC replay (needs Zicheng's AFE), Icelandic full-record streaming (flash-limited, needs UART feed). Each is listed in README's phase table with what unblocks it.
