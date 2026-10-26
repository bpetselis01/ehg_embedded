# 0003. Built-in recording as a build-time option

**Date:** 2026-10-22 (commit `3eb063f`)

## Status

Accepted. Amends [0002](0002-digital-injection-over-serial.md)

## Context

[0002](0002-digital-injection-over-serial.md) removed the compiled-in recording to keep one code path. Demos and long
soaks then need a laptop feeding the board over USB the whole time. A board that runs stand-alone (power on, connect the
gateway) is closer to a real wearable.

## Decision

The mode is chosen **at build time**: `firmware/build.sh flash --record <npz> [--seconds S]` compiles the recording in
([`tools/make_replay.py`](../../tools/make_replay.py) → `replay_data.h`, `CONFIG_EHG_BAKED_RECORD`); a plain
`firmware/build.sh flash` is the serial feed. Only two things differ by mode: where frame n comes from and where the
record settings come from. Timing, packets, CRC, resend and the gateway are shared. A built-in board refuses
CONFIG/DATA/FAULT (`@ERR built-in record mode`) but still answers clock pings, so
`feed.py --sync-only --node EHG-93C0` keeps the exact H1 offset ([0005](0005-h1-clock-offset-from-serial-sync.md)).

Rejected: both modes in one image with the feeder taking over at run time (more states to test).

## Consequences

- Switching mode means reflashing (~20 s).
- No fault injection in built-in mode.
- The built-in image is larger (377 KB, 79 % of the slot, vs 167 KB streamed), and flash limits its length.
- Both modes gave 100 % delivery and 0 mismatches on the DK (2026-10-22).

**Sources:** [plans/baked-or-streamed-plan.md](../plans/baked-or-streamed-plan.md) (Decisions); [progress.md](../progress.md) 2026-10-22.
