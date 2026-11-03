# 0009. Durable gateway outbox (store and forward)

**Date:** 2026-11-03

## Status

Proposed (awaiting Checkpoint 1 approval). Implemented by Unit 3 (Python) and Unit 15 (Swift).

## Context

The gateway publishes MQTT QoS 1 through an in-memory, unbounded paho queue. If the process dies or the broker is unreachable for a long time,
queued packets are lost, and the board's 256-packet history ([decision 0004](0004-end-to-end-loss-recovery.md)) only covers about two minutes.
Over a cellular link, outages are expected, not exceptional.

## Decision

- `gateway/outbox.py` keeps pending messages in SQLite (WAL, `synchronous=NORMAL`, file mode 0600). `publish()` writes the row **first**, then
  publishes if connected. A row is deleted only when the PUBACK arrives. Oldest first, at most 100 in flight, backoff 1 s to 60 s with jitter.
- Replayed messages keep their original receive time, get a fresh publish time and carry `"replayed": true`. Ingest stores the flag;
  `eval/latency.py` can exclude replayed messages. Duplicate delivery is already harmless because writes are keyed on (node, seq).
- A disk cap (`--outbox-max-mb`, default 512) drops the oldest rows loudly and counts them, so a loss stays identifiable.
- The outbox is **off by default** (`--outbox PATH` turns it on). Whether to enable it by default is Byron's decision at Checkpoint 2.
- The BLE callback never blocks on the outbox. A retained status topic `ehg/_gw/<gateway_id>/status` with a Last Will marks the gateway online or offline.
- The iOS gateway gets the same behaviour: SQLite in a file with Data Protection class `completeUntilFirstUserAuthentication`.

## Consequences

- The outbox is plain SQLite, protected by file mode and disk encryption (FileVault, iOS Data Protection). SQLCipher was rejected for awkward
  macOS wheels; this is a documented gap against encrypted-at-rest storage.
- Disk writes sit on the hot path, so Unit 3 measures the overhead (a benchmark recorded in `docs/progress.md`, not a CI assertion).
- An outbox protects against broker outages and restarts, not against a long outage while the iOS app is not running.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §2 (row on store and forward), §9 Unit 3, Unit 15b; [`gateway/gateway.py`](../../gateway/gateway.py); [decision 0004](0004-end-to-end-loss-recovery.md).
