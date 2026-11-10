# 0005. H1 clock offset from serial sync pings

**Date:** 2026-10-20 (commit `ca54a5c`)

## Status

Accepted. Replaces the BLE lower-envelope offset as the H1 method

## Context

H1 latency starts at the sampling instant, stamped with the board's clock, and ends when InfluxDB acknowledges the
write, stamped with the laptop's clock. The two clocks must be lined up. The first method took the smallest
(laptop arrival − board time) over all packets (the lower envelope). That only knows the offset up to the minimum
one-way BLE delay, so every latency read low by up to one connection interval (Moon et al. 1999; Paxson 1998).

## Decision

[`tools/feed.py`](../../tools/feed.py) sends clock pings over the serial link; the board answers each with its tick
(`@SYNC`), and the pairs are saved to `eval/results/sync_<node>.csv`. [`eval/latency.py`](../../eval/latency.py) uses
them when the file exists (±1.8 ms) and falls back to the envelope otherwise, printing which method it used.
Latency is per sample, from its sampling instant, so the batching wait counts.

## Consequences

- H1 run 3: median 259 ms, p95 478 ms; the envelope would have read 0.1–3 ms lower.
- Built-in boards need `feed.py --sync-only` during a run to keep this accuracy ([0003](0003-built-in-recording-build-time-option.md)).
- Remaining caveat: `t_db_ack` comes from the Docker VM clock (see [troubleshooting](../troubleshooting.md)).

**Sources:** [plans/realistic-injection-plan.md](../plans/realistic-injection-plan.md) (Research basis: latency start point); [progress.md](../progress.md) 2026-10-20 and 2026-10-22.
