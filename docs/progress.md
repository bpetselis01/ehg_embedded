# Progress log

## 2026-10-20: realistic injection, continuous acquisition, fault injection (plan: [plans/realistic-injection-plan.md](plans/realistic-injection-plan.md))

What changed (details: [firmware.md](firmware.md), [interface-spec.md](interface-spec.md) §3/§3b, [fault-injection.md](fault-injection.md)):
- **The recording is streamed, not built in.** [`tools/feed.py`](../tools/feed.py) sends it over the J-Link serial port
  (460800 baud, RTS/CTS) about 2 s ahead; the board's timer still decides each sampling instant (H1 start unchanged).
  `make_replay.py` / `replay_data.h` are gone. Any record, any length, 16 ch × 200 Hz possible.
- **Wire v2:** CRC-16 trailer on every BLE packet. **Acquisition continues through disconnects**; the gateway keeps one
  gap tracker per board and re-asks for lost re-sends (1 s, up to 5 times).
- **H1 clock offset from serial pings** (`eval/results/sync_<node>.csv`, ±1.8 ms) instead of the BLE lower envelope.
- **Fault injection** (`feed.py --faults`): Gilbert burst loss, bit corruption, forced disconnects; seeded, logged.
- **Button 1** starts/stops; LED1 connected, LED2 sampling. **MCUboot + MCUmgr/SMP** for updates over Bluetooth.

Hardware results (DK EHG-93C0, macOS gateway, Docker backend, TPEHG tpehg1007, 7 min each):

| Run | Faults | Delivery | Bit-exact | H1 median / p95 | Notes |
|---|---|---|---|---|---|
| Unit 3 (no backend) | none | 474/474 packets first frame checked | 0 mismatches | – | `starved=0`, `rx_bad=0`, max_inject_late 397 µs |
| run 2 | drop 5 % (burst 3), corrupt 1 %, disc 120 s | 98.76 % | 0 | 257 / 474 ms | exposed: serial line errors (fixed: RTS/CTS) and single-shot resend requests (fixed: retries) |
| run 3 | same, seed 3 | **100.0000 %** (8410/8410) | **0** | **259 / 478 ms** | 69 drops, 8 corruptions, 4 disconnects all recovered; `streams=1`; sync ±1.8 ms; envelope bias measured 0.1–3 ms |

Engineering notes from this session:
- **1 Mbaud loses board→laptop bytes on the J-Link VCOM** (≈0.1 % of log lines); 460800 is clean without Bluetooth.
  With Bluetooth busy a few log lines still lose bytes, so machine lines (`@…`) carry a CRC.
- **Zephyr deferred logging batches lines for up to 1 s** (`LOG_PROCESS_THREAD_SLEEP_MS`); `LOG_PROCESS_TRIGGER_THRESHOLD=1`
  makes each line go out at once. Without it the feeder's queue reports arrived in late pairs and the board ran dry.
- **Without RTS/CTS the UART dropped ~1 serial message a minute** while Bluetooth was busy (UART line errors).
- **pyserial `read(4096)` with a timeout waits the full timeout**: it put 50 ms into every clock ping; `read(in_waiting)` fixed it (RTT 3.5 ms).
- `nrfutil mcu-manager ble` needs a second Nordic board as its radio; the phone app is the one-board update route.
- A reset makes the serial line emit a burst of junk bytes; harmless (only CRC-checked `@` lines are acted on).

Not verified on hardware: pressing Button 1 / the LEDs (no one at the board); an actual over-the-air image upload
(the update service answered an image-list request: slot 0, confirmed, active).

## 2026-09-29: autonomous setup session (while Byron was away)

### Gantt status

| Phase | Task | Status |
|---|---|---|
| 0a | Order DK | ✅ (done by Byron) |
| 0b | NCS install, sample app | ✅ NCS v3.4.1. The `ehg_node` firmware builds and flashes, and the DK advertises as `EHG-93C0` |
| 0c | Dataset conversions | ✅ TPEHG (300), TPEHGT (31), ICEHG (126; was 252 with duplicates, fixed 2026-10-16), Icelandic EHGDB (123 segments) → [`data/converted/`](../data/converted), bit-exact vs WFDB (test) |
| 0d | Interface spec with Zicheng | 🟡 **Draft** in [`docs/interface-spec.md`](interface-spec.md), with questions for Zicheng marked |
| 1a | Digital sample injection | ✅ Absolute-tick scheduler, record loops for soak tests |
| 1b | Ring buffer | ✅ `k_msgq`; overflow counted and logged |
| 1c | GATT notification service | ✅ Built and flashed. ⛔ **Not yet tested over the air** (macOS Bluetooth permission, see below) |
| 1d | Seq + timestamps + injection jitter | ✅ In the packet header. `max_inject_late` is logged on UART |
| 2a | Bleak central | ✅ Written. Resend requests on gaps, per-notification CSV log. ⛔ Waits on the same permission |
| 2b | MQTT (Mosquitto QoS 1) | ✅ Docker |
| 2c | InfluxDB schema + ingest | ✅ `ehg_raw` / `ehg_pkt`, dedup on (node, seq) |
| 3a | Grafana | ✅ Local, provisioned dashboard (raw, latency, pkt rate, retransmissions). Cloud deployment ❌ not done (needs your account/cost decision) |
| 3b | WebSocket viewer | ✅ [`viewer/`](../viewer) (screenshot verified) |
| 4a/4b | H1/H2 tooling | ✅ [`eval/latency.py`](../eval/latency.py) (drift-corrected), [`eval/integrity.py`](../eval/integrity.py) |

**Pipeline check with simulated nodes** (no BLE hop; full Docker stack; 1 % injected loss):
- 100 % delivery after resends, 0 bit-exact mismatches.
- Per-sample latency: median 252 ms, p95 459 ms. About 450 ms of that is batching (10 frames at 20 Hz). The software stack adds about 10 ms.

These are **not H1/H2 results**, because the BLE hop is missing. They only show the tooling works.

### Needs you (blockers)

1. **Bluetooth permission for the terminal app.** Bleak scans hang silently because macOS never granted Bluetooth access to the app that runs your terminal.
   - Fix: System Settings → Privacy & Security → Bluetooth, and enable it for your terminal (or VS Code). Or run `uv run python -m gateway.gateway --no-mqtt` yourself once and click Allow.
   - Then: `uv run python -m gateway.gateway` with the DK powered.
   - The first BLE run will show on the DK's UART what macOS actually granted (MTU, PHY, DLE, connection interval). Record those numbers in the thesis.
2. **Docker Desktop is very slow to start containers on this machine** (5–15 minutes). It's not broken, just slow. Once up, the stack stays up (`restart: unless-stopped`). I did not restart Docker Desktop because your Supabase stack is running in it.
3. Grafana is on **port 3300**: VS Code is holding 3000–3003.
4. **Interface spec questions for Zicheng**: raw vs pre-filtered TPEHG channels; Icelandic channel choice / bipolar derivation ([`docs/interface-spec.md`](interface-spec.md) §1, §6).
5. **Classifier hook**: Zicheng's code isn't in this repo, so the H4 label-agreement half isn't wired. The proposed hook contract is in the interface spec §5.
6. **Cloud deployment (3a)**: not provisioned. The compose file runs unchanged on any small VM. Before exposing it: set `backend/.env` secrets, and turn on Mosquitto password + TLS (commented in [`mosquitto.conf`](../backend/mosquitto/mosquitto.conf)).

### Findings worth putting in the thesis (the reference audit found these; details in [`papers/INDEX.md`](../papers/INDEX.md))

- **BioGAP firmware *is* public.** `pulp-bio/BioGAP` has had Apache-2.0 NCS/Zephyr nRF5340 firmware in `Firmware/src_NRF` since July 2025. The interim report (§8.3) says no code was found. A local clone is at `~/thesisb-refs/BioGAP` (outside the repo); its [`prj.conf`](../firmware/ehg_node/prj.conf) is a direct reference for PHY/DLE/connection-parameter setup. Also, ref [72]'s URL points to `pulp-bio/VowelNet`, not BioGAP.
- **[55]:** the TimescaleDB 3.5× / 3.4–71× figures are a Timescale vendor benchmark that the paper quotes, not the paper's own result.
- **[18]:** no "≥20k connections" figure is in the paper (3 publishers / 15 subscribers were tested).
- **[20]:** chest/wrist nodes at 1 kHz; the ring + nRF52840 detail comes from [19], not [20].
- **[47]:** S140 is an nRF5-SDK SoftDevice. NCS uses the SoftDevice Controller; its link limit (20) still holds (`BT_MAX_CONN` range 1–20).
- **[51]:** latencies are in *minutes*, so it's a weak comparator for a < 2 s target.
- **[17]:** 99.72 % is decrypt-and-store over 1 h; InfluxDB is only mentioned as optional there.
- **Refs [33], [39], [71], [73]** are missing from the numbered list.

### Engineering notes discovered in this session

- **NCS sysbuild fails on paths containing spaces.** Build through the symlink `~/thesisb` ([`firmware/build.sh`](../firmware/build.sh) does this).
- **Timer rounding:** `k_timer` periods round to 32 768 Hz ticks, so 50 ms runs as 20.005 Hz. The replay therefore schedules on absolute ticks.
- **Clock alignment for H1 must use the *last* frame of a batch.** Aligning on frame 0 folds the batching delay into the offset and makes latency read ~0 (this bug was caught by a test).
- **Crystal drift** of 20–50 ppm is 72–180 ms/h. [`eval/latency.py`](../eval/latency.py) fits the lower envelope of the offset (Moon et al. 1999) from the raw device ticks stored in `ehg_pkt.t_inject_dev`.
- **Absolute latency excludes the minimum BLE link delay** (about one connection interval). State this with the H1 results. A round-trip ping characteristic would bound it, if a marker asks.
- **Ingest runs in Docker** and stamps `t_db_ack` with the Docker VM clock. Docker Desktop keeps it synced with macOS, but check the offset before the final H1 runs (or run ingest on the host).
- **8 nodes (H3):** macOS's connection limit is undocumented. The fallback is a DK/dongle as a multi-link central (NCS `central_multilink`). `--simulate 8` load-tests everything after BLE today.

### Next steps (in order)

1. Grant Bluetooth permission → first over-the-air run → record the negotiated link parameters.
2. Run a 10 min run, then the 6 h TPEHG soak (H2 MVP): [`eval/integrity.py`](../eval/integrity.py) + [`eval/latency.py`](../eval/latency.py).
3. Agree the interface spec with Zicheng; wire the classifier hook (H4).
4. Cloud deployment decision (3a).
