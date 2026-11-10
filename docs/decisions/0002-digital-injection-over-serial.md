# 0002. Digital injection: stream the recording over serial, release it with the board's timer

**Date:** 2026-10-20 (commit `ca54a5c`)

## Status

Accepted, amended by [0003](0003-built-in-recording-build-time-option.md)

## Context

The pipeline is tested with real public EHG recordings played back as if a sensor were measuring them, so every result
has a known right answer (report §8.2, Sensor-in-the-Loop). The first version compiled one recording into flash: a new
recording meant a rebuild, and flash held only ~2 min of the 16-channel Icelandic set. Playing the record through a
DAC into a real analogue front end would be most realistic, but it needs Zicheng's hardware.

## Decision

[`tools/feed.py`](../../tools/feed.py) streams the recording over the J-Link serial port (the same cable as the log),
about 2 s ahead, at **460800 baud with RTS/CTS flow control**. The board's own timer releases frame n at t0 + n/fs and
stamps that tick as the sampling instant (H1 start), so serial jitter changes only the buffer level, never the timing.
A frame not delivered in time is counted (`starved`) and skipped, kept separate from radio loss.

Rejected: laptop-paced release (USB jitter would enter the "sensor"); native USB CDC (a second cable);
1 Mbaud (lost board→laptop bytes on the J-Link VCOM); no flow control (~1 lost serial message a minute with Bluetooth busy).

## Consequences

- Any recording, any length, up to 16 ch × 200 Hz, with no rebuild.
- The feeder owns the serial port, so it also prints the board log; machine lines carry a CRC because the VCOM loses a few bytes.
- The board needs the laptop connected by USB to run. [0003](0003-built-in-recording-build-time-option.md) adds a stand-alone option.

**Sources:** [plans/realistic-injection-plan.md](../plans/realistic-injection-plan.md) (Decisions, Research basis); [progress.md](../progress.md) 2026-10-20 engineering notes; [interface-spec.md](../interface-spec.md) §3b.
