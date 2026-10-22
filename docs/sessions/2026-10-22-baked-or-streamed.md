# Session Q&A Log: Built-in or streamed recording

**Date:** 2026-10-22
**Mode:** New Feature
**Scope:** Standard
**Phases completed:** 0, 1, 2, 4, 5 (condensed, in plan), 6 (one unknown resolved), 7
**Outcome:** Plan produced: `docs/plans/baked-or-streamed-plan.md`

---

## Questions & Answers by Phase

### Phase 2: Challenge & Explore

**Q:** Byron asked to be able to use either the one-time flashed recording or the continuous UART feed.
**AI position:** Feasible with one localized difference. `take_frame()` and the `cfg` source are the only seams; timing,
packets, CRC, resend and faults are shared. Flagged that this reverses the 2026-10-20 "two code paths" decision, and costs
a second hardware check per change.

**Q:** Main reason for bringing the built-in recording back? **User:** run without the laptop feed (stand-alone).
**AI position:** Then the board must play a record with no serial input; noted the loss of clock-sync pings
(latency reads a few ms low) unless addressed.

**Q:** How should the mode be chosen? **AI recommended:** both in one image, with the serial feed taking over at run time.
**User:** build-time choice only. **Resolved:** `build.sh flash --record <npz>` = built-in; plain build = serial.

### Phase 4.0: Clarify

**Q:** Fault injection in built-in mode? **User:** not available (recommended).
**Q:** Clock sync for built-in runs? **User:** yes, add `feed.py --sync-only`. **AI added:** `--node` is required, because a
sync-only feeder never gets `@HELLO` with the board's name.

### Phase 6: Deepen

**Q (AI, internal):** Are `CONFIG_*` values visible in the app's CMakeLists? **Resolved:** yes (NCS samples use `if(CONFIG_…)`).

---

## Key Decisions

| Decision | Chosen | Phase | Rationale |
|---|---|---|---|
| Purpose | stand-alone runs | 2 | demos/soaks without a laptop feeding serial |
| Selection | build-time flag | 2 | one active path per image; fewest runtime rules |
| Faults in built-in mode | none | 4.0 | smallest change; they stay a serial-feed feature |
| Clock sync in built-in mode | `feed.py --sync-only --node` | 4.0 | exact H1 offset without streaming samples |
| Stale mode | always pass `-DCONFIG_EHG_BAKED_RECORD=y/n` | 5 | west caches `-D` options |

## Positions Where AI Pushed Back

| Topic | AI Position | User Response | Final Resolution |
|---|---|---|---|
| Selection | runtime switch (no reflash between modes) | build-time only | build-time flag |
| Reversal | it reverses the "one code path" decision; costs double checks | accepted | difference confined to replay.c |
