# 0007. Enterprise-style evolution, with InfluxDB kept as the reference store

**Date:** 2026-11-03

## Status

Proposed (awaiting Checkpoint 1 approval). Nothing in this record is built yet.

## Context

The validated results H1 to H4 were measured on a prototype topology: laptop gateway, anonymous local broker, one InfluxDB 2.7, Grafana
([decision 0006](0006-local-mosquitto-influx27-grafana.md)). The thesis now wants the same chain shown as an enterprise-style remote
monitoring architecture (sensor node, BLE, mobile gateway, cellular and MQTT, cloud core, EHR and dashboard) using free services only.
Changing the storage or transport under the recorded results would make them impossible to reproduce.

## Decision

- Add the enterprise layers **beside** the prototype, never in place of it. Every new capability sits behind a flag, a compose profile or an
  override file. `cd backend && docker compose up -d` still starts the same 4 containers on the same ports, and
  `python -m gateway.gateway [--simulate N]` behaves as today unless a new flag is passed.
- InfluxDB 2.7 with Flux stays the **reference store** for the local stack and for H1 to H4. The cloud adds PostgreSQL with TimescaleDB
  ([decision 0012](0012-timescaledb-nats-data-platform.md)).
- Moving any result to the new store needs a **parity proof**: identical row sets, bit-identical samples and the same `eval/` answers from
  either store. Until it passes, the cloud profile does not use the new store as primary.
- The wire format v2 does not change. The recorded H1 to H4 numbers are never rewritten; new measurements are added as separate rows.
- Hops that cannot be real on free services (multi-region availability, a real LTE-M modem, write-once storage) are written up as design
  documents and labelled **design-only** wherever they appear ([decision 0017](0017-backup-dr-and-design-only-ha.md)).

## Consequences

- Two stores exist for a while, so the data platform needs a dual-write path and a parity tool, which costs effort and some VM memory.
- The default developer experience is unchanged, and the test suite must keep passing without Docker, hardware, network or Xcode.
- Every claim in the thesis about the enterprise chain must say whether a hop is built, partly built or design-only.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §1 (invariants 3 and 4), §3, §4b; [decision 0006](0006-local-mosquitto-influx27-grafana.md); [progress.md](../progress.md) 2026-10-20.
