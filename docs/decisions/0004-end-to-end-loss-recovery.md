# 0004. End-to-end loss detection and recovery

**Date:** 2026-09-29 (seq, history, resend, QoS 1) and 2026-10-20 (wire v2 CRC, commit `ca54a5c`)

## Status

Accepted

## Context

H2 asks for more than 99 % delivery **and** that every loss can be identified. BLE's link layer retransmits, but
packets can still be lost when buffers overflow or the link drops, and nothing above BLE would notice. MQTT QoS 1 can
deliver a message twice. Fault-injection runs also corrupt packets on purpose.

## Decision

- Every packet carries a **sequence number** (`seq`) and the frame number of its first frame (`frame_idx`), so any gap
  is visible to the gateway and to [`eval/integrity.py`](../../eval/integrity.py).
- The firmware keeps the last `CONFIG_EHG_HISTORY_PACKETS` (256) packets. The gateway's gap tracker
  ([`gateway/tracking.py`](../../gateway/tracking.py)) sends `RESEND <seq> <count>`, and re-asks every 1 s, up to 5 times.
- **Wire v2** adds a CRC-16 trailer: the gateway drops a corrupted packet, which turns corruption into a gap that RESEND recovers.
- Acquisition continues through disconnects, so an outage is a recoverable gap, not a new run.
- MQTT **QoS 1** with a persistent session; ingest drops duplicates by (node, seq).

## Consequences

- Run 3 (2026-10-20, 5 % burst loss, 1 % corruption, a disconnect every 120 s): 100.0000 % delivery (8410/8410), 0 mismatches.
- Losses that cannot be recovered (copy already overwritten) are counted (`resend_miss`) and still identified by seq.
- The wire format has two copies (C and Python); [`tests/test_docs.py`](../../tests/test_docs.py) checks they agree.

**Sources:** [design spec](../design/codebase-design.md) §3, §5; [plans/realistic-injection-plan.md](../plans/realistic-injection-plan.md) (Corruption); [progress.md](../progress.md) 2026-10-20.
