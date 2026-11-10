# Built-in or streamed recording (choose at build time)

**Date:** 2026-10-22
**Mode:** New Feature (Standard)
**Status:** Plan, not started
**Revisits:** [realistic-injection-plan.md](realistic-injection-plan.md), which removed the built-in mode ("2 code paths").
Reason for bringing it back: **stand-alone runs** (demos, soaks) without a laptop feeding the board over serial.

## Decisions (2026-10-22, with Byron)

| Decision | Chosen | Rejected |
|---|---|---|
| Why built-in returns | the board must run stand-alone (power it on, connect the gateway) | comparing the two methods; a fallback in case serial fails |
| How the mode is picked | **at build time**: `firmware/build.sh flash --record <npz>` = built-in; plain `build.sh flash` = serial feed (as now). Switching = reflash (~20 s) | both in one image, with the feeder taking over at run time |
| Fault injection in built-in mode | **not available** (it stays a serial-feed feature) | faults baked in at build time |
| H1 clock sync in built-in mode | **`feed.py --sync-only --node EHG-93C0`**: sends only SYNC pings, so built-in runs also get the exact offset | none (BLE-envelope offset, a few ms low) |

## What stays shared (no second copy)

The replay timer, tick stamp (H1 start), `replay_ring`, the packet builder, CRC, history/resend, disconnect handling,
Button 1/LEDs, SYNC answering and the gateway/backend/eval are all unchanged. Only two things differ by mode:
**where `take_frame()` gets frame N** and **where `cfg` (n_ch, fs, record_id, bits) comes from**.

## Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| R1 | `firmware/build.sh [pristine] flash --record <npz> [--seconds S]` builds an image with that recording inside, and `record_id` is looked up from the manifest | Must |
| R2 | A built-in image works with no `feed.py`: after boot, START (gateway or Button 1) streams the record, looped forever, frame_idx from 0 (row = frame_idx mod N) | Must |
| R3 | A plain `build.sh flash` gives today's serial-feed image, even if the previous build was built-in (no leftover CMake cache or header) | Must |
| R4 | A built-in board refuses CONFIG/DATA/FAULT with `@ERR built-in record mode …` and sends no `@LVL`; it still answers SYNC | Must |
| R5 | `feed.py --sync-only --node <name>` sends only SYNC pings and logs `eval/results/sync_<name>.csv`; works in both modes | Must |
| R6 | Same results in both modes: bit-exact 0 mismatches and 100 % delivery on a clean run; the boot log names the mode | Must |
| R7 | Docs say which mode to use when and how to build each (firmware.md Stages A–D, README, interface-spec 3b) | Must |

## Implementation units

### Unit 1: Bring back `tools/make_replay.py`

**Goal:** `.npz` → `firmware/ehg_node/src/replay_data.h` again, with `record_id` found automatically.
**Requirements trace:** R1
**Dependencies:** none
**Files:** `tools/make_replay.py` (restored from `43a9694`), `tests/test_make_replay.py` (restored), `.gitignore` (`firmware/ehg_node/src/replay_data.h` again)
**Approach:** `git show 43a9694:tools/make_replay.py`. Change `--record-id` from required to optional: default to
`tools.feed.record_id_for(npz)` (reuse it, don't copy it). Keep `--seconds` and `--bits` and the header layout
(`REPLAY_N_CH/N_FRAMES/FS_HZ/RECORD_ID/SAMPLE_BITS`, `replay_samples[]` under `REPLAY_DEFINE_SAMPLES`).
**Test scenarios:**
- [ ] The restored tests pass: exact samples, 24-bit uses int32, 16-bit refuses values that would wrap
- [ ] No `--record-id` → the manifest id (tpehg1007 → 0)
- [ ] `.npz` not in a manifest → clear error naming the manifest

**Verification:** `uv run pytest -q tests/test_make_replay.py` passes; the header for tpehg1007 starts `1093, 171, -748`.

### Unit 2: Firmware built-in source behind a Kconfig switch

**Goal:** with `CONFIG_EHG_BAKED_RECORD=y`, frames come from `replay_samples[]` and `cfg` from the header's constants.
**Requirements trace:** R2, R4, R6
**Dependencies:** Unit 1
**Files:** `firmware/ehg_node/Kconfig` (`EHG_BAKED_RECORD`, bool, default n), `src/replay.c`, `firmware/ehg_node/CMakeLists.txt`
(if `CONFIG_EHG_BAKED_RECORD` is set and `src/replay_data.h` is missing → `FATAL_ERROR` naming `build.sh --record`)
**Approach (replay.c only, guarded by `#ifdef CONFIG_EHG_BAKED_RECORD`):**
- `#define REPLAY_DEFINE_SAMPLES` + `#include "replay_data.h"`; at boot set `cfg = {valid, REPLAY_N_CH, REPLAY_SAMPLE_BITS, REPLAY_FS_HZ, REPLAY_RECORD_ID}`.
- `take_frame(want)` in built-in mode: copy row `want % REPLAY_N_FRAMES` (int16 or int32 per `REPLAY_SAMPLE_BITS`) into `f->s`; never starves.
- `replay_start()` in built-in mode: `start_idx = 0` (fresh session = frame 0).
- `on_config` / `on_data` / FAULT: `at_line("ERR built-in record mode: rebuild without --record to stream")`. `@LVL` suppressed.
  SYNC unchanged. The boot log says `source: built-in record 0 (tpehg…, 3 ch @ 20 Hz, 35180 frames)` or `source: serial feed`.
**Patterns:** today's `cfg`/`take_frame` split; `at_line` for messages to the laptop; the old pre-2026-10-20 `replay.c` for the row copy.
**Test scenarios:**
- [ ] Built-in image + gateway only, 10 min → `integrity.py` 100 %, 0 mismatches, `starved=0`
- [ ] Built-in image + `feed.py` (normal) → feeder prints `@ERR built-in record mode …` and exits non-zero
- [ ] Loop: a record shorter than the run (`--seconds 30`, 3 min run) → frame_idx keeps rising, rows wrap, still bit-exact
- [ ] 24-bit record (`convert.py --bits 24` source) → flags bit1 set, gateway decodes 3-byte samples

**Verification:** both images build without warnings; the boot log names the mode.
**Planning-time unknowns:** ~~whether `CONFIG_*` is visible in `CMakeLists.txt`~~ **Resolved:** yes. NCS samples
branch on `if(CONFIG_…)` after `find_package(Zephyr)`, e.g. `nrf/samples/wifi/nrf_cloud/CMakeLists.txt`.

### Unit 3: `build.sh --record`

**Goal:** one flag picks the mode, and a plain build always goes back to the serial feed.
**Requirements trace:** R1, R3
**Dependencies:** Units 1, 2
**Files:** `firmware/build.sh`
**Approach:** parse `--record <npz>` (and pass-through `--seconds S`) next to `pristine`/`flash`. With it: run
`uv run python tools/make_replay.py <npz> [--seconds S]`, then `west build … -- -DCONFIG_EHG_BAKED_RECORD=y`.
Without it: `-DCONFIG_EHG_BAKED_RECORD=n`. **Always pass the value explicitly**, because west caches `-D` options and a
built-in build would otherwise stick. Print the mode at the end (`built: serial feed` / `built: built-in tpehg1007`).
**Test scenarios:**
- [ ] `build.sh --record tpehg1007.npz` then plain `build.sh` → second image is serial-feed (boot log)
- [ ] `--record` with a missing file → stops before building, with the path in the error
- [ ] `pristine flash --record …` works (argument order independent)

**Verification:** the `.config` has `CONFIG_EHG_BAKED_RECORD=y` only for `--record` builds.

### Unit 4: `feed.py --sync-only`

**Goal:** exact H1 clock offset for built-in runs.
**Requirements trace:** R5
**Dependencies:** none (works against today's firmware too)
**Files:** `tools/feed.py`, `tests/test_feed.py`
**Approach:** `--sync-only --node EHG-93C0`: no npz needed, no CONFIG/DATA/FAULT; open the port, ping once a second,
write `eval/results/sync_<node>.csv` exactly as now, and print the board log. `npz` becomes optional only with `--sync-only`.
**Test scenarios:**
- [ ] Argument rules: `--sync-only` without `--node` → error; normal mode without npz → error
- [ ] Sync rows are logged in the same CSV format (existing parse test reused)
- [ ] Hardware: built-in run + `--sync-only` → `latency.py` prints `offset method: serial sync`

**Verification:** `latency.py` on a built-in run with `--sync-only` uses the serial-sync offset.

### Unit 5: Docs

**Goal:** a reader knows when to use each mode and how to build it.
**Requirements trace:** R7
**Dependencies:** Units 1–4
**Files:** `docs/firmware.md` (intro "two ways to feed the board", Stage A2 `make_replay.py` for built-in, B1 `--record`,
D5–D7 marked serial-only, stand-alone run order), `README.md` (quickstart: both commands), `docs/interface-spec.md` §3b
(`@ERR built-in record mode`, sync-only), `docs/progress.md` (dated entry), `tools/demo.sh` header (built-in: skip window 1, or run `--sync-only`)
**Test scenarios:** every command in the docs runs as written; every named function exists (grep); no doc claims faults work in built-in mode.

## Quality bar

- [x] Every unit traces to R-IDs
- [x] DAG: 1 → 2 → 3; 4 is independent; 5 last
- [x] 3 or more test scenarios per unit
- [x] At most 8 files per unit
- [x] No new abstraction: one Kconfig switch, one `#ifdef` region, one flag per tool
- [x] Unknowns: none left (the CMake question was resolved in review)
- [x] Handoff: the rules for refusing CONFIG, START numbering, looping, `@LVL` and cache handling are all specified

## Review notes (condensed)

- **Two code paths:** limited to `take_frame`, `cfg` init and message refusal in one file. Accepted by Byron in exchange for
  stand-alone runs. Both modes get a hardware check (Unit 2 and a serial regression run).
- **Stale-mode risk (P1, addressed):** a cached `-DCONFIG_EHG_BAKED_RECORD=y` would silently keep the built-in mode. Unit 3
  always passes it explicitly and prints the mode; the boot log names it too.
- **Flash:** TPEHG 216 KB fits in the 464 KB app slot next to the ~167 KB app. Icelandic 16 ch × 200 Hz needs `--seconds` (about 1–2 min).
- **Not in scope:** faults in built-in mode; switching modes without reflashing; demo.sh changes beyond its header.
