# Realistic injection, continuous acquisition, fault injection

**Date:** 2026-10-20
**Mode:** New Feature, Deep
**Status:** Implemented 2026-10-20 (results and deviations: [progress.md](../progress.md), section 2026-10-20)
**Replaces:** design-spec assumption A5 (the recording is compiled into flash)

## Problem frame

The firmware's "sensor" is a whole patient recording compiled into the image. Changing the recording means
rebuilding and reflashing. Flash caps the length of a soak (only about 2 min of the Icelandic set fits). A BLE
disconnect also *stops* acquisition, and the reconnect *restarts the recording at frame 0*. A real wearable keeps
sampling while the radio is down, so today an outage looks like a fresh run instead of a lost-and-recovered stretch,
which weakens the H2 6 h soak. The H1 clock fit (lower envelope) also absorbs the minimum BLE delay, so every latency
reads low by up to one connection interval (30 ms). Finally, the pipeline has no way to test itself under the loss
patterns a real telemonitoring link produces.

## Research basis (2026-10-20)

- **Session start / firmware delivery.** Firmware is installed at the factory and updated over BLE: Movesense DFU,
  BioButton self-update, MCUboot + MCUmgr/SMP in NCS. Sessions start with a button (Zio, Bittium Faros) or an app
  command (BioButton, VitalPatch, Movesense). Electrode contact (lead-off detection) is a quality check, not the
  start trigger.
- **Injection.** The most realistic option is a DAC/AWG playing the record into a real AFE (NeuroDAC, J Neural Eng 2021;
  the PregCare EHG simulator check). It needs Zicheng's AFE, so it stays a stretch goal. The most practical option is to
  stream from the host over serial into a device ring buffer, released by a **device-side timer**, behind the
  interface a future ADC driver would use. This is still the report's "digital injection" (§8.2, Sensor-in-the-Loop [60]).
- **Latency start point.** IEC 60601-1-8 starts at the event in the patient. RFC 7679 says to timestamp as close to the
  event as possible. VoIP counts packetisation delay. So the current definition holds: per sample, from the
  sampling instant, including the batching wait, to DB ack. The bias to fix: lower-envelope / min-offset fits know
  the offset only up to the minimum one-way delay (Moon et al. 1999; Paxson 1998). It must be pinned independently.
- **Fault injection / network impairment.** BLE link-layer CRC + ARQ means corrupted bits are never delivered. Real
  impairment is **bursty loss** (Gilbert-Elliott model), delay, and supervision-timeout disconnects. Tipparaju et al.
  (Biosensors 2021) remove BLE loss with a re-request queue, which is prior art for our seq + history + RESEND design.
  FDA "RF Wireless Technology in Medical Devices" (2013) and ANSI C63.27 frame this as testing "functional wireless
  performance" against pass/fail criteria set in advance.
- **Per-channel threads: rejected.** The nRF52840 is one Cortex-M4F core, so threads give no parallelism. The
  ADS129x samples all channels simultaneously with one data-ready per frame, and per-channel threads would break that
  simultaneity. Industry practice is one acquisition ISR or thread per frame, which is what we already do.

## Decisions (2026-10-20, with Byron)

| Decision | Chosen | Rejected |
|---|---|---|
| Sample source | Host streams ahead over serial; **board timer releases** at fs; baked array removed | laptop-paced (USB jitter enters the "sensor"); keeping the baked mode (2 code paths) |
| Link | **J-Link VCOM UART, shared with logs** (1 cable) | native USB CDC (2nd cable). Costs accepted: framing, the feeder owns the port, a sync bias from the J-Link bridge (measured + reported) |
| Underrun (host fell behind) | count `starved`, skip that frame, frame_idx still advances | waiting (timing would become laptop-driven) |
| Fault settings | runtime command over serial, seeded, logged per run, off at boot | Kconfig (rebuild per sweep point) |
| Corruption | inject on the sent copy only; **CRC-16 in each packet (wire v2)**; gateway drops bad packets, so a corrupted packet becomes a gap that is recovered by RESEND | detect offline only; gateway fuzz only |
| Start | BLE START (existing) **+ DK Button 1**, LEDs for state | electrode-contact auto-start |
| OTA DFU | MCUboot + SMP over BLE, **last unit** | separate plan |

## Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| R1 | Samples reach the firmware from the laptop over the J-Link VCOM UART at any recording's rate (TPEHG 3 ch × 20 Hz up to Icelandic 16 ch × 200 Hz), with no rebuild to change the recording | Must |
| R2 | The board's own timer sets the sampling instant: frame n is due at t0 + n/fs; serial jitter only affects buffer level | Must |
| R3 | Samples stay bit-exact end to end; `integrity.py` passes on a streamed run (H2/H4) | Must |
| R4 | Underrun is counted (`starved`), never silent, and kept distinct from radio loss in the results | Must |
| R5 | Acquisition continues through BLE disconnects; seq and frame_idx continue; after reconnect the gateway recovers the outage from history (up to 256 packets) | Must |
| R6 | H1 clock offset pinned independently with serial SYNC pings; `latency.py` reports latency with the min-delay bias removed, plus the sync uncertainty | Must |
| R7 | Each packet carries a CRC-16; the gateway rejects a mismatch and the packet is recovered as a gap | Must |
| R8 | Fault injection at runtime: Gilbert-Elliott drop, corruption of the sent copy, periodic forced disconnect; seeded, counted in the log, recorded with the run | Must |
| R9 | A written physical test protocol (distance, body, Wi-Fi/microwave) gives the real loss/burst numbers used to set R8 | Should |
| R10 | DK Button 1 starts/stops a session; LEDs show connected / streaming | Should |
| R11 | Firmware can be updated over BLE (MCUboot + SMP) without a cable | Should (stretch) |
| R12 | `firmware.md`, `interface-spec.md`, README match the new flow; the baked-array path is removed everywhere | Must |

## Architecture after the change

```
laptop                                               nRF52840 DK
tools/feed.py ── VCOM UART (1 Mbaud) ──────────────> uart RX ISR -> rx ring -> feed parser
  CONFIG / DATA(frame_idx, samples) / SYNC / FAULT     CONFIG: n_ch, fs, record_id, bits
  <── text lines: Zephyr log + "@..." machine lines     DATA -> frame buffer (≈ 2 s lead)
        @HELLO EHG-93C0 · @LVL 40 · @SYNC id tick       board timer (replay.c): every 1/fs pop next frame,
                                                         stamp tick = t_inject (H1 start), -> replay_ring
gateway/gateway.py <──── BLE notify (wire v2, CRC) ──── ehg_service.c: pack, history, fault stage, notify
                   ───── START/STOP/RESEND ────────────>   (keeps running when disconnected)
```

- **Host → board:** binary only. COBS frames delimited by `0x00`, each `type | payload | CRC-16`. Only `feed.py` writes, so nothing interleaves.
- **Board → host:** text lines only, through Zephyr logging, which writes each message whole. Machine messages are
  lines starting with `@`; `feed.py` parses those and prints every other line as the board log. This avoids a second
  binary framing on the shared TX.
- **Frame numbering:** `frame_idx` is set by the feeder (source position, continuing across record loops, so
  row = frame_idx mod N for the integrity check). At each tick the board expects `e`; it discards stale frames (< e,
  counted `late`). With nothing for `e`, it counts `starved` and moves to e+1. A packet only ever holds consecutive
  frames: a jump closes it early (`n_frames` < max), which the wire format already allows.
- **Frames per packet** become runtime: `min(10, ⌊(244 − hdr − crc)/(n_ch × bytes)⌋)`, so 3 ch → 10 and 16 ch 16-bit → 6.

## Implementation units

### Unit 0: Check: UART RX coexists with the log backend (hardware check)

**Goal:** prove interrupt-driven RX on `uart0` works while `LOG_BACKEND_UART` writes on the same UART, at 1 Mbaud.
**Requirements trace:** R1
**Dependencies:** none
**Files:** scratch branch only: `src/main.c` (temporary echo), `boards/nrf52840dk_nrf52840.overlay` (`current-speed = <1000000>`), `prj.conf` (`CONFIG_UART_INTERRUPT_DRIVEN=y`)
**Approach:** register `uart_irq_callback_set` for RX; count bytes and log `@RX n` every second; push 10 kB/s from Python for 10 min.
**Test scenarios:**
- [ ] 10 kB/s for 10 min: RX count = bytes sent, no log lines lost or split
- [ ] 1 Mbaud is stable on the J-Link OB VCOM (if not: 460800, which still gives ≥ 7× the Icelandic rate)
- [ ] The log keeps working with no host attached (no RX)

**Verification:** a counter match plus intact log lines. **If it fails, stop and switch the link to native USB before Unit 3.**
**Planning-time unknowns:** ~~whether NCS 3.4.1's UART log backend uses the async API on uart0~~ **Resolved
2026-10-20 (Phase 6):** `zephyr/subsys/logging/backends/log_backend_uart.c` writes with `uart_poll_out` unless
`CONFIG_LOG_BACKEND_UART_ASYNC` is set. Our `.config` has it unset (`LOG_BACKEND_UART=y`, `LOG_MODE_DEFERRED=y`),
and the backend registers no UART callback, so `uart_irq_callback_set` for RX is free. Unit 0 drops from a design
blocker to a hardware check (1 Mbaud stability + no lost log lines).

### Unit 1: Wire format v2: CRC-16

**Goal:** every packet ends in a CRC-16/CCITT over header + samples; the version goes to 2.
**Requirements trace:** R7, R3
**Dependencies:** none
**Files:** `firmware/ehg_node/src/ehg_packet.h`, `firmware/ehg_node/src/ehg_service.c` (append CRC, `crc16_itu_t`), `gateway/packet.py` (`pack` appends, `parse` checks: `ValueError("crc mismatch")`), `tests/test_packet.py`, `docs/interface-spec.md` §3
**Approach:** trailer, not header, so the field offsets stay the same. The Zephyr `crc16_itu_t(0, …)` result must equal Python `binascii.crc_hqx(…, 0)`, using the same known vector in both.
**Patterns:** the "change both copies together" rule in CLAUDE.md.
**Test scenarios:**
- [ ] pack → parse round trip; one flipped bit anywhere → `crc mismatch`
- [ ] Known vector `b"123456789"` → `0x31C3` in both C and Python
- [ ] A v1 packet → `unknown version 1` (no silent acceptance)

**Verification:** pytest green; the firmware builds with the size assertion `PKT_LEN + 2 ≤ 244`.

### Unit 2: Serial protocol + `tools/feed.py`

**Goal:** a host tool that streams a `.npz` to the board, keeps the board buffer about 2 s ahead, and handles `@` lines.
**Requirements trace:** R1, R2, R4
**Dependencies:** Unit 0
**Files:** `tools/feed.py` (new: COBS + CRC encode, CONFIG/DATA/SYNC/FAULT messages, `@LVL` flow control, record loop, log passthrough, `eval/results/feed_<node>.csv`), `tests/test_feed.py`, `docs/interface-spec.md` (new §: serial injection protocol)
**Approach:** `pyserial` (check it's in `pyproject`; add it if not). Messages: `CONFIG{n_ch,fs,record_id,bits}`, `DATA{frame_idx u32, samples}` (batched up to 10 frames per message), `SYNC{id}`, `FAULT{…}` (Unit 7). Sends CONFIG first and waits for `@HELLO <name>`. Flow control: target level = 2 s of frames, read from `@LVL` (1/s), so the clock drift between host and board (20–50 ppm, about 1 s over 6 h) is absorbed.
**Patterns:** `gateway --simulate` already reads the `.npz` and loops it; reuse the loader in `tools/convert.py`.
**Test scenarios:**
- [ ] COBS round trip, including payloads full of `0x00`
- [ ] Frame numbering continues across the record loop (`frame_idx` 35180 → row 0)
- [ ] `@` line parsing: valid, truncated, and an unknown `@` type passes through as log
- [ ] Board never answers `@HELLO` → exit with a clear message within 5 s

**Verification:** pytest; `feed.py --dry-run` prints the encoded bytes for one frame.

### Unit 3: Firmware: streamed source behind the existing replay interface

**Goal:** `replay.c` releases frames from the serial-fed buffer instead of `replay_samples[]`; record parameters arrive at runtime.
**Requirements trace:** R1, R2, R3, R4
**Dependencies:** Units 0, 1, 2
**Files:** `src/replay.c` (RX ISR → ring, parser, frame buffer, timer release, `starved`/`late` counters, `@LVL`/`@HELLO` lines), `src/replay.h`, `src/ehg_packet.h` (frame samples as `int32_t`, drop the `replay_data.h` include), `src/ehg_service.c` (runtime n_ch/fs/record_id/bits/frames-per-packet, close the packet on a frame_idx jump, store the length in history), `prj.conf`, `boards/nrf52840dk_nrf52840.overlay` (new), `CMakeLists.txt` (remove the replay_data check), `Kconfig` (`EHG_FEED_BUFFER_FRAMES`)
**Approach:** keep the due-time maths (`t0 + n·ticks/fs`) and the stamp position (right after the wake, before the copy): **H1's start point is unchanged**. START before CONFIG → logged error, ignored.
**Patterns:** today's spinlock-guarded replay state; `K_MSGQ` to `ehg_service.c`.
**Test scenarios:**
- [ ] TPEHG streamed for 10 min → `integrity.py` 100 % delivered, 0 mismatches
- [ ] Feeder killed mid-run → `starved` climbs, DB shows a frame_idx gap, no crash; feeder restarted → resumes
- [ ] Icelandic 16 ch × 200 Hz for 10 min → 6 frames/packet, `starved` = 0, `max_inject_late` within today's range
- [ ] Malformed or CRC-bad serial frame → dropped and counted (`rx_bad`), stream continues

**Verification:** `max_inject_late` same order as the baked version; bit-exact check passes.
**Planning-time unknowns:** *Deferred:* RX ring size (start at 4 KB). The frame buffer is ≥ 2 s at the highest rate: 400 frames × 16 ch × 4 B = 25.6 KB RAM, which fits in the 182 KB free.

### Unit 4: Remove the baked-array path

**Goal:** one code path; no stale references.
**Requirements trace:** R12
**Dependencies:** Unit 3
**Files:** delete `tools/make_replay.py`, `tests/test_make_replay.py`; edit `firmware/build.sh` (drop the replay_data check), `.gitignore`, `README.md` (step 0), `tools/convert.py` (BEFORE → AFTER line), `docs/codebase-guide.md`
**Test scenarios:**
- [ ] `git grep -e make_replay -e replay_data` finds only historical docs (plans/sessions)
- [ ] `build.sh pristine` succeeds on a clean checkout without `replay_data.h`
- [ ] pytest green after the test deletion

**Verification:** a clean clone builds and streams.

### Unit 5: Continuous acquisition across disconnects (P1)

**Goal:** a disconnect is an outage, not a restart.
**Requirements trace:** R5
**Dependencies:** Unit 3
**Files:** `src/ehg_service.c` (no `replay_stop()` on disconnect; when offline, packets still go to history with seq++, counted `offline`), `src/replay.c` (START while running = no-op), `gateway/gateway.py` (one `Node` per board for the whole run, re-bind `request_resend` on each connection; START only if not already streaming), `tests/test_gateway.py` (new, `Node` with fake publish/resend)
**Approach:** after reconnect the first packet shows a seq jump, `GapTracker` requests the range, and `serve_resends` replays history. Outages longer than 256 packets (128 s at TPEHG, about 43 s at 16 ch/200 Hz) → `resend_miss`, reported as a known limit.
**Test scenarios:**
- [ ] Unit test: `Node` sees seq 0..9, then 40 → resend (10, 30); the state survives a simulated reconnect
- [ ] Hardware: 20 s outage (gateway killed) → `integrity.py` 100 % after recovery, single stream
- [ ] 200 s outage at TPEHG → recovered except the oldest packets, which equal `resend_miss`, and `integrity.py` lists those ranges
- [ ] User STOP then START → history cleared, seq restarts (deliberate new run)

**Verification:** the soak shows `streams=1` across disconnects.

### Unit 6: Clock sync over serial (H1 bias fix)

**Goal:** pin the board→host clock offset independently of BLE.
**Requirements trace:** R6
**Dependencies:** Units 2, 3
**Files:** `src/replay.c` (SYNC → stamp the tick at RX in the parser, `@SYNC id tick`), `tools/feed.py` (1 ping/s, CSV `t_send,t_recv,tick`), `eval/latency.py` (use sync pairs: min-RTT samples → offset ± RTT/2, linear drift fit across the run; print both the old and the sync-based results and their difference), `tests/test_latency.py`
**Approach:** NTP-style; keep the lowest-RTT 10 % of pings because the deferred log adds asymmetric delay. Report the uncertainty `= min RTT / 2`.
**Test scenarios:**
- [ ] Synthetic pairs with known offset/drift → recovered within 0.1 ms
- [ ] No sync file → falls back to the current method with a printed warning
- [ ] Hardware: the sync-based median latency ≥ the old median, difference ≤ 1 connection interval (30 ms), or else investigate

**Verification:** `latency.py` prints `offset method: serial-sync (±x ms)`.
**Planning-time unknowns:** *Deferred:* the J-Link bridge's asymmetry. If min-RTT/2 > 5 ms, write it up as the stated limit (the accepted cost of shared VCOM).

### Unit 7: Fault injection + physical test protocol

**Goal:** reproducible impairment, sweepable without reflashing.
**Requirements trace:** R7, R8, R9
**Dependencies:** Units 1, 2, 5
**Files:** `src/ehg_service.c` (fault stage just before `bt_gatt_notify`: Gilbert-Elliott drop with seeded xorshift; corrupt = flip 1 random bit in a *copy*; `k_work_delayable` → `bt_conn_disconnect` every N s; counters `inj_drop`, `inj_corrupt`, `inj_disc` in the stats line), `src/replay.c` (FAULT message → settings), `tools/feed.py` (`--faults drop=0.02,burst=4,corrupt=0.001,disc=600,seed=1`; recorded in the feed CSV header), `gateway/gateway.py` (`crc mismatch` → count `crc_bad`, drop; the gap logic recovers it), `docs/fault-injection.md` (new: model, parameters, physical protocol: 1/3/6/10 m, body shadowing while walking, Wi-Fi load + microwave; log seq gaps → fit GE p, r)
**Test scenarios:**
- [ ] drop=0.05 burst=1 for 10 min → `integrity.py` 100 %, `resent` ≈ `inj_drop`
- [ ] corrupt=0.01 → gateway `crc_bad` ≈ `inj_corrupt`, 0 bit-exact mismatches in the DB
- [ ] disc=60 for 10 min → 10 reconnects, `streams=1`, 100 % delivery
- [ ] Same seed twice → the same drop pattern (compare the CSV seq gaps)
- [ ] Defaults at boot → all counters 0

**Verification:** a results table of injected vs recovered, per setting.

### Unit 8: Button start + LEDs

**Goal:** start a session the way Zio and Faros do.
**Requirements trace:** R10
**Dependencies:** Unit 3
**Files:** `src/main.c` (`dk_buttons_init`; Button 1 toggles `replay_start`/`replay_stop`; LED1 = connected, LED2 = streaming), `prj.conf` (`CONFIG_DK_LIBRARY=y`), `docs/firmware.md`
**Test scenarios:**
- [ ] Press → `replay start` log + LED2 on; press again → stop + LED2 off
- [ ] Press before CONFIG → logged "no record configured", LED2 stays off
- [ ] Button START then gateway START → no restart (Unit 5 no-op)

**Verification:** the hardware log plus the LEDs.

### Unit 9: OTA DFU (MCUboot + SMP over BLE): stretch, last

**Goal:** update firmware without a cable.
**Requirements trace:** R11
**Dependencies:** Units 1–8
**Files:** `firmware/ehg_node/sysbuild.conf` (new: `SB_CONFIG_BOOTLOADER_MCUBOOT=y`), `prj.conf` (`CONFIG_NCS_SAMPLE_MCUMGR_BT_OTA_DFU=y`), `firmware/build.sh` (print the `dfu_application.zip` path), `docs/firmware.md` (new stage: update), `README.md`
**Approach:** flash once by cable (MCUboot + app), then `nrfutil mcu-manager` or the nRF Device Manager phone app uploads the image; test → confirm. Note: the default MCUboot key is a public dev key, fine for a thesis rig but not for a device.
**Test scenarios:**
- [ ] Bump the version string → upload over BLE → the log shows the new version after reset
- [ ] Interrupted upload → the old image still boots
- [ ] Unconfirmed image → reverts on the next reset

**Planning-time unknowns:** *Deferred:* the SMP service's RAM/buffer needs alongside our MTU 247 settings; whether the EHG service and SMP coexist without changing `CONFIG_BT_*` buffer counts.

### Unit 10: Docs

**Goal:** the docs describe the system as built.
**Requirements trace:** R12
**Dependencies:** Units 1–9 (each unit updates its own docs; this unit is the final pass)
**Files:** `docs/firmware.md` (Stages A–E rewritten: no flash per recording; feeder → CONFIG → START), `docs/interface-spec.md`, `README.md`, `docs/progress.md`, `docs/design/codebase-design.md` (A5 superseded)
**Test scenarios:** every function and log line named exists (grep); a cold read of README from step 0 works; no `replay_data` mention outside history.

## Quality bar

- [x] Every unit traces to R-IDs
- [x] DAG: 0 → 2 → 3 → {4, 5, 6, 8}; 1 → 3; {1, 2, 5} → 7; all → 9 → 10
- [x] 3 or more test scenarios per unit
- [x] At most 8 files per unit (Unit 3 is at 8: watch it)
- [x] At most 2 new abstractions per unit (Unit 3: serial parser + frame buffer; Unit 7: fault stage)
- [x] Unknowns classified: the Unit 0 design question was resolved in Phase 6; the rest are deferred
- [x] Handoff: the behaviour for underrun, stale frames, reconnect, CRC fail, START-while-running and START-before-CONFIG is all specified

## Review (Phase 5)

**P0:** none open. Unit 0 exists so the shared-UART risk fails early, before any feature work depends on it.

**P1, accepted by Byron (shared VCOM):** the feeder owns the port, so `screen` or nRF Terminal can't be used during runs
(the feeder prints the log); the sync uncertainty from the J-Link bridge is measured and reported, not removed.

**P1, addressed in plan:**
- The outage limit = history depth (256 packets). This is now a *stated, measured* limit (Unit 5, 200 s test).
  Raising `CONFIG_EHG_HISTORY_PACKETS` costs about 244 B per packet of RAM; 512 = 125 KB still fits.
- A resend storm after a long outage competes with live packets for the link. Unit 5's 200 s test watches
  `max_inject_late` and `ring_overflow`.
- A corrupted `seq` field → the packet is dropped by CRC before seq is trusted, so a corruption can't fake a gap.

**Coverage:** host-side logic (COBS, CRC, @-parsing, Node reconnect, sync fit) → pytest. Firmware behaviour →
hardware runs with counters plus `integrity.py`, the same as today (no ztest; YAGNI for a single-board thesis rig).
Not covered automatically: the firmware parser against fuzzed serial input beyond the Unit 3 malformed-frame test.

**Out of scope:** DAC/AFE replay (needs Zicheng's AFE); MQTT-hop impairment with Toxiproxy (optional later: TCP
turns loss into stalls, and ingest already dedups QoS 1 duplicates); per-channel threads (rejected above).

## Deviations found during implementation (2026-10-20)

- **Baud 460800, not 1 Mbaud**, plus **RTS/CTS flow control** (Unit 0 + run 2 findings).
- **`@` lines carry a CRC**; **CONFIG carries a feeder session number** (repeat CONFIGs no longer purge the queue);
  the board reports `@LVL` only after CONFIG, and the feeder re-sends CONFIG after 3 s of silence (board reset).
- **Gateway re-requests** still-missing packets (1 s, up to 5 times): a single request lost about 5 % of re-sends under 5 % injected loss.
- **Unit 5's hardware test** used the forced-disconnect fault (Unit 7) instead of killing the gateway: a restarted gateway
  is a new process with no memory of earlier seqs, so it could not test recovery.
- **Unit 9 upload** not tested: `nrfutil mcu-manager ble` needs a second Nordic board; use the nRF Device Manager phone app.
