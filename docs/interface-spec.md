# EHG sample interface specification: DRAFT v1

Status: **draft for agreement with Zicheng** (Gantt task 0d). Written 2026-09-29. Nothing here is agreed yet.

This spec defines how EHG samples move from a replay source to the backend, and how a downstream consumer such as Zicheng's classifier reads them back. Every layer uses the same numbers, so a sample can be compared bit-for-bit with its source (H2, H4).

## 1. Sample representation

| Property | TPEHG (main) | Icelandic EHGDB (stretch) | ICEHG / TPEHGT (optional) |
|---|---|---|---|
| Channels | 3: signals `1`,`2`,`3` (unfiltered bipolar) | 16: `EHG1`…`EHG16` in numeric (grid) order | `S1..S3` / `EHG1..3`(+`TOCO`) |
| fs | 20 Hz | 200 Hz | 20 Hz |
| Sample type | int16, raw ADC units (24-bit optional, below) | int16, raw ADC units | int16 |
| To physical units | `mV = (adu - baseline) / gain`; gain 13107 adu/mV, baseline 0 | gain 131.068 adu/mV | per `.npz` |
| Timestamp epoch | device ticks at 32 768 Hz since boot (u32), mapped to Unix epoch seconds by the gateway | same | same |

Filtering is **not** applied in transit. Zicheng's 0.08–4 Hz zero-phase band-pass, 180 s end-crop and 60 s windows with a 30 s step all run downstream, on the reconstructed stream.

**Question for Zicheng:** your pipeline selects three TPEHG channels. Are they the raw signals `1/2/3` that are then filtered in Python, or the pre-filtered `*_DOCFILT-4-0.08-4` copies? If it's the copies, we either transport those instead or reproduce the filter. Either works; we need to pick one.

## 2. Converted file format (`data/converted/<db>/<record>.npz`)

Produced by [`tools/convert.py`](../tools/convert.py). Keys: `samples` int16 `[n_frames, n_ch]` (int32 with `--bits 24`), `fs`, `channels`, `gain`, `baseline`, `units`. `manifest.json` maps `record_id` to the record name. `record_id` is what travels on the wire, so it is tied to the record name and never reused (manifests are tracked in git).

## 3. Wire packet (BLE notification payload), little-endian

| off | type | field |
|---|---|---|
| 0 | u8 | version = 2 (v2 added the CRC trailer; v1 packets are rejected) |
| 1 | u8 | n_ch |
| 2 | u16 | fs_hz |
| 4 | u32 | seq: packet counter from stream start |
| 8 | u32 | frame_idx: frame counter from stream start. Source row = `frame_idx % n_frames(record)` |
| 12 | u32 | t_inject: device tick when frame 0 was injected |
| 16 | u8 | n_frames |
| 17 | u8 | flags (bit0 = retransmission, bit1 = 24-bit samples) |
| 18 | u16 | record_id |
| 20 | i16 × n_frames × n_ch | samples, frame-major. If flags bit1: 3-byte signed little-endian each instead (opt-in: a record converted with `convert.py --bits 24`, streamed by `feed.py`; for 24-bit front ends such as ADS129x) |
| end − 2 | u16 | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF; Zephyr `crc16_itu_t(0xFFFF, …)`, Python `binascii.crc_hqx(…, 0xFFFF)`) of every byte before it. Check value: `"123456789"` → `0x29B1`. A mismatch is dropped by the gateway and recovered like a missing packet |

Size = 20 + n_frames × n_ch × (2 or 3) + 2, e.g. 3 ch × 10 frames 16-bit = 82 bytes; must be ≤ 244 (MTU 247 − 3).

Implemented in [`firmware/ehg_node/src/ehg_packet.h`](../firmware/ehg_node/src/ehg_packet.h) and [`gateway/packet.py`](../gateway/packet.py). The BLE service is `4e480001-a0e1-4c3b-8747-9a1b2c3d4e5f`. Data characteristic `…0002` (notify). Control characteristic `…0003` (write): `01` start, `02` stop, `03 <u32 seq> <u16 count>` resend.

## 3b. Serial injection link (laptop → board, J-Link VCOM, 460800 baud 8N1)

The laptop ([`tools/feed.py`](../tools/feed.py)) streams the recording into the board; the board's timer
([`replay.c`](../firmware/ehg_node/src/replay.c)) decides when each frame is sampled. Same port as the board's log, so:

**Laptop → board: binary only.** Each message = COBS-encoded `type | payload | CRC-16`, then one `0x00`.
CRC as in §3 (CCITT-FALSE, over type + payload). Malformed, wrong-CRC or wrong-length messages are dropped and counted (`rx_bad` in the 10 s log line).

| type | name | payload (little-endian) |
|---|---|---|
| `0x10` | CONFIG | u8 n_ch (1–16), u16 fs_hz, u16 record_id, u8 bits (16/24), u16 feeder session (random per feeder start). Same session again = a repeat: answered, queue kept. New session = queue emptied. Ignored while a session runs |
| `0x11` | DATA | u32 frame_idx of the 1st frame, u8 n (≤ 10), then n × n_ch samples (2 or 3 bytes, signed) |
| `0x12` | SYNC | u32 ping id. Board answers `@SYNC <id> <tick>` at once; feeder logs `eval/results/sync_<node>.csv` for `eval/latency.py` |

**Board → laptop: text lines only**, through Zephyr's log (so a line is never split). Lines containing `@WORD … #xxxx` are for the feeder, where `xxxx` = CRC-16 (hex) of the text between `@` and ` #`: the VCOM loses a few board→laptop bytes per 10,000 lines while the radio is busy, so damaged lines are ignored. Everything else is the normal log.

| line | meaning |
|---|---|
| `@HELLO <name> <n_ch> <fs> <record_id>` | CONFIG accepted |
| `@LVL <frames>` | frames queued on the board, once a second, only after CONFIG. The feeder tops up to `--lead` seconds; 3 s without one = board reset, so the feeder sends CONFIG again |
| `@SYNC <id> <tick>` | board clock (u32, 32768/s) when ping `id` was parsed |
| `@ERR <text>` | refused command (e.g. START before CONFIG, CONFIG while running) |

`frame_idx` is set by the feeder: from 0, continuing when the record loops (row = `frame_idx % n_frames`).
At START the board takes the oldest queued frame as the session's first and then expects consecutive numbers:
older frames are dropped (`late`), a missing one is skipped (`starved`, frame_idx gap in the DB, source-side, not radio loss).

## 4. MQTT message (`ehg/<node_id>/pkt`, QoS 1, JSON)

`{node_id, seq, frame_idx, t_inject, t_inject_host, n_ch, fs_hz, record_id, flags, samples: [[...]], t_gw_rx, t_mqtt_pub}`

## 5. InfluxDB schema (bucket `ehg`)

- `ehg_raw`: one point per frame. Tags `node_id`, `record_id`. Fields `c0..c{n-1}` (int), `seq`, `frame_idx`. Time = injection time of the frame.
- `ehg_pkt`: one point per packet. Fields `seq, n_frames, retrans, t_inject_dev, t_inject_host, t_gw_rx, t_mqtt_pub, t_ingest_rx, t_db_ack`.

**Proposed classifier hook:** the classifier reads `ehg_raw` for a `node_id` over a 60 s window with a 30 s step (Flux query in [`eval/backend_client.py:query_frames`](../eval/backend_client.py)), converts to mV with the gain above, runs unchanged, and writes the label to measurement `ehg_label` (tags `node_id`, fields `label`, `score`, `window_start`). A separate subscriber like this is also how report §8.4 describes the hook.

## 5b. FHIR export (clinical boundary)

[`tools/fhir_export.py`](../tools/fhir_export.py) turns a stored window into an HL7 FHIR R5 Bundle: a `Device` (node id) and an `Observation`
with one `component` per channel, each `valueSampledData` = raw counts in `data` ("E" for frames that never arrived),
`factor` = 1/gain, `origin` = −baseline/gain mV, `interval` = 1000/fs ms. Raw counts stay unchanged (H4).
The live path (BLE → MQTT → InfluxDB) stays custom. Codes use a local system: no standard EHG code was found.

## 6. Icelandic → classifier decimation policy (H4 stretch; proposed)

Transport all 16 channels at 200 Hz (report §6.9). For the 3-channel 20 Hz classifier, downstream: pick 3 channels (proposed: EHG6, EHG7, EHG10, the central 2×2 area; **Zicheng to decide**). Then apply an anti-alias FIR low-pass at 8 Hz with zero phase (`scipy.signal.decimate(x, 10, ftype='fir', zero_phase=True)`), then Zicheng's normal pipeline. The Icelandic set is monopolar, TPEHG is bipolar: deriving bipolar pairs (e.g. EHG6−EHG7) is closer to TPEHG and needs agreement.
