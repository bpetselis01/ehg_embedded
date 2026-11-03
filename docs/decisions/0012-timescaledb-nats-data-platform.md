# 0012. Cloud data platform: Mosquitto, NATS JetStream and PostgreSQL with TimescaleDB

**Date:** 2026-11-03

## Status

Accepted (Checkpoint 1, 2026-11-03). Code written in Unit 6 on 2026-11-03 (bridge, writer, migrations, `--backend`, `eval/parity.py`, compose profile `platform`); tested with fakes and a throwaway PostgreSQL 14, NOT yet against a real NATS server or TimescaleDB. Becomes the primary cloud store only after Checkpoint 4 (the parity proof, which needs Docker).

## Context

The cloud core needs an internal bus with durable, replayable consumers (several services read the same packets) and an enterprise store for
time series plus relational data (registry, alerts, audit). It must fit beside two JVM services on 12 GB, with free licences.

## Decision

- **Mosquitto stays the edge broker.** A bridge worker moves messages into **NATS JetStream** (stream `EHG`, file storage, memory cap, duplicate
  window using `Nats-Msg-Id` = `node:seq:t_inject`; the duplicate-window behaviour is **unverified** on the pinned version). The bridge acknowledges MQTT only
  after the JetStream publish is acknowledged, so a crash redelivers and never drops.
- Consumers are durable, pull-based, explicit-ack, with a maximum delivery count and a dead-letter subject surfaced by the triage worker. Writes are idempotent.
- **PostgreSQL 16 with TimescaleDB Community edition** is the cloud store: hypertables `ehg_raw` and `ehg_pkt` mirror the Influx fields, primary key
  `(node, seq, ch, time)` with `ON CONFLICT DO NOTHING`; compression after 1 day; 90-day retention (configurable); continuous aggregates for health history.
  Migrations use Alembic with hand-written SQL for the Timescale parts. Samples stay raw integers.
- **Dual write with a parity proof** (`eval/parity.py`): both stores receive the same simulated stream and must give identical sets, bit-identical samples and
  the same `eval/` answers. Failure stops the switch ([decision 0007](0007-enterprise-evolution-keep-influx-as-reference.md)).
- Rejected: EMQX (5.9+ is Business Source Licence; clustering is paid), Redpanda (BSL), Kafka (two JVMs' worth of memory next to HAPI and Keycloak),
  InfluxDB in the cloud (0.5 to 1 GB extra). Kafka is the documented scale-up path; broker clustering is design-only.
- The TimescaleDB Community edition uses the Tiger Data licence, which is not OSI-approved. Own-use self-hosting is allowed and offering it as a service is not.
  Unit 6 confirms the edition with `SHOW timescaledb.license;` and records it in [licences.md](../licences.md).

## Built and checked in Unit 6 (2026-11-03)

- Checked with tests: the bridge acknowledges MQTT only after the stream's acknowledgement; a failure leaves it unacknowledged; a crash between publish and ack redelivers and the (fake) duplicate window stores it once; unreadable messages go to `EHG.BAD.*` and are acknowledged;
  the writer's rows equal the samples (raw integers, 24-bit kept), batches are atomic and idempotent on a real PostgreSQL 14, an outage is `degraded` with the messages kept in NATS, and revision 0002 **fails loudly** (and leaves the database at 0001) on a server without TimescaleDB.
- **NOT verified:** JetStream's duplicate window and `Nats-Msg-Id` behaviour on a real server (the fake mimics the documented behaviour); `nak(delay=...)` redelivery pacing; revision 0002 (hypertables, compression, continuous aggregates, `SHOW timescaledb.license`) on a real TimescaleDB; the `timescaledb-ha` image's init script and data path; the whole parity proof.
  psycopg 3 is LGPL-3.0 (recorded in the licence register).
- **Deviation:** the writer stores one row per frame **and channel** (the prompt's schema) where InfluxDB stores one point per frame; `eval/backend_client.py` regroups them, and `eval/parity.py` proves the regrouped data are identical.

## Consequences

- One more moving part (the stream) buys replay, independent consumers and a second dedup layer.
- arm64 availability of the non-HA `timescale/timescaledb` image is unconfirmed; the HA image is preferred until checked (Unit 4).
- Compression and retention protect the 200 GB disk; growth is documented rather than assumed.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §2, §5, §5b, §6 (time-series schema), §9 Unit 6; [decision 0006](0006-local-mosquitto-influx27-grafana.md). Licence and image facts are unverified until the units that use them.
