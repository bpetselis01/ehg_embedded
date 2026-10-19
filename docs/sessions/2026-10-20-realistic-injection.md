# Session Q&A Log: Realistic injection and firmware robustness

**Date:** 2026-10-20
**Mode:** New Feature
**Scope:** Deep
**Phases completed:** 0, 1, 2, 4, 5 (condensed, in plan), 6, 7. Phase 3's requirements are inside the plan.
**Outcome:** Full plan produced: `docs/plans/realistic-injection-plan.md`

---

## Questions & Answers by Phase

### Phase 2: Challenge & Explore (Byron's 6 questions)

**Q1:** A doctor shouldn't run build.sh to start capture. How does industry do it?
**AI position:** The premise is half right. build.sh *installs* firmware; capture already starts with the BLE START command,
which is industry practice (app command). Real devices: factory flash + BLE DFU (MCUboot + SMP), session start by button
(Zio, Faros) or app (BioButton, VitalPatch, Movesense). Lead-off detection is a quality check, not the start trigger.
**Resolved:** add DK Button 1 + LEDs; OTA DFU as the last (stretch) unit.

**Q2:** Is compile_commands.json run on the board / is it main.c?
**AI position:** Neither. It's a host-side list of the compiler commands for each file, written by CMake, read only by
editor tools (VS Code IntelliSense). The board receives only zephyr.hex.
**Resolved:** answered; no change.

**Q3:** Replace the baked recording with a laptop feeding samples at 20 Hz over USB?
**AI position:** Plausible and still "digital injection" (report §8.2), but the laptop must not set the timing (USB
jitter would become sensor jitter). Stream ahead into a board buffer; the board timer releases at fs. DAC into a real AFE
is more realistic but needs Zicheng's AFE.
**User:** chose USB stream, board-timed (recommended).
**Resolved:** baked array removed; host feeder + board-timed release.

**Q4:** How many threads does the nRF52840 have; one per channel?
**AI position:** Shot down. One core, so no parallelism; threads are limited only by RAM. The ADS129x samples all
channels simultaneously, so per-channel threads would split a simultaneous frame and add context switches. One
acquisition thread per frame (current design) is industry practice.
**Resolved:** no change.

**Q5:** Is the stamp taken after the sleep and before the copy and queue? Is that the standard latency start?
**AI position:** Yes on both. It matches IEC 60601-1-8 (event at patient), RFC 7679 (timestamp close to the event)
and VoIP (packetisation counted). Fix needed: the min-offset clock fit absorbs the minimum BLE delay, so latency
reads low by up to 30 ms. Pin the offset with serial SYNC pings.
**Resolved:** Unit 6.

**Q6:** Deliberately drop/corrupt packets to test robustness?
**AI position:** Yes: fault injection / network impairment. Bursty loss (Gilbert-Elliott) + disconnects are realistic;
bit corruption is not delivered over BLE (link CRC), so corruption is only meaningful as an app-level integrity test.
Calibrate from physical tests.
**User:** asked to add corruption injection "for simulation's sake".
**Resolved:** corruption on the sent copy + CRC-16 in wire v2; gateway drops bad packets → recovered via RESEND.

**Finding raised by AI:** a BLE disconnect stops the replay and the reconnect restarts at frame 0 (history wiped),
so an outage looks like a new run. **Resolved:** Unit 5 (continuous acquisition).

### Phase 2 / 4.0: decisions

**Q:** Which extra changes go in? **User:** all: P1 fix, clock sync, fault injection, button + DFU, plus corruption.
**Q:** Corruption handling? **User:** CRC, detect + resend (recommended).
**Q:** Link? **User:** shared J-Link VCOM. **AI pushed back once:** the feeder owns the port, both directions need
framing, J-Link bridge asymmetry biases the sync, and the baud must rise. **User:** kept shared VCOM.
**Q:** Underrun? **User:** count + skip (recommended). **Q:** Fault control? **User:** runtime over serial (recommended).
**Q:** DFU? **User:** last unit (recommended).

### Phase 6: Deepen

**Q (AI, internal):** Does the NCS 3.4.1 UART log backend conflict with IRQ-driven RX on uart0?
**Resolved:** No. It uses `uart_poll_out` unless `LOG_BACKEND_UART_ASYNC` is set (unset here); it registers no callback.

---

## Key Decisions

| Decision | Chosen | Phase | Rationale |
|---|---|---|---|
| Sample source | host stream, board timer | 2 | realistic timing, no reflash, unlimited soak, 16 ch/200 Hz |
| Link | shared J-Link VCOM | 4.0 | one cable (Byron); costs accepted |
| Underrun | count + skip | 4.0 | keeps board timing; source loss kept distinct |
| Disconnect | acquisition continues | 2 | real sensors keep sampling; H2 soak validity |
| Latency offset | serial SYNC pings | 2 | removes the min-delay bias of the lower-envelope fit |
| Corruption | CRC-16, wire v2 | 2 | otherwise corruption is stored silently |
| Per-channel threads | rejected | 2 | one core; simultaneous sampling |
| DFU | last unit | 4.0 | changes flash layout; needs the baked array gone |

## Positions Where AI Pushed Back

| Topic | AI Position | User Response | Final Resolution |
|---|---|---|---|
| Q1 premise | build.sh installs; START already starts capture | accepted (button + DFU added) | Units 8, 9 |
| Laptop-paced 20 Hz | USB jitter would become sensor timing | chose board-timed | Unit 3 |
| Per-channel threads | not useful on one core; breaks simultaneity | no objection | rejected |
| Shared VCOM | native USB is cleaner and better for sync | kept shared VCOM | accepted risk, Unit 0 check |
| Corruption realism | not delivered over BLE | wanted it anyway | done with CRC, so it tests detection |
