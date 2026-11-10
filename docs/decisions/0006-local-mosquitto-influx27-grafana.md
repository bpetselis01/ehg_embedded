# 0006. Backend: Mosquitto, InfluxDB 2.7 and Grafana in local Docker

**Date:** 2026-09-29 (commit `12426ec`)

## Status

Accepted

## Context

The report's architecture (Fig. 10) needs a message broker, a time-series database, dashboards and alerts. The report
asks for "an earlier, fixed version" of InfluxDB rather than 3 Core. Cloud hosting needs accounts and spending
decisions outside the thesis's control.

## Decision

[`backend/docker-compose.yml`](../../backend/docker-compose.yml) runs eclipse-mosquitto 2 (QoS 1, queue kept on disk),
**InfluxDB 2.7 (pinned)**, the ingest service ([`backend/ingest/ingest.py`](../../backend/ingest/ingest.py)) and Grafana
(datasource and dashboard provisioned from files, port 3300), all on the laptop. Cloud deployment is documented, not provisioned.

## Consequences

- One command (`docker compose up -d`) brings the backend up the same way every time; nothing depends on a cloud account.
- InfluxDB 2.7 is the stable OSS line with first-class Grafana support; queries are Flux.
- Latency includes no internet hop, so H1 measures a local backend; a cloud backend would add its own delay.
- Docker Desktop on the development Mac is slow to start containers ([troubleshooting](../troubleshooting.md)).

**Sources:** [design spec](../design/codebase-design.md) assumptions A2, A3, §4; [README](../../README.md) step 2.
