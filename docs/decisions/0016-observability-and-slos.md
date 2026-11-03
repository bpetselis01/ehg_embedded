# 0016. Observability stack and service-level objectives

**Date:** 2026-11-03

## Status

Accepted (Checkpoint 1, 2026-11-03; status of the build is stated in each record). Implemented by Unit 12.

## Context

A cloud chain that nobody watches fails silently, which the project rules out. The thesis also needs numbers (delivery, latency, lag) from the VM, not only from the Mac.

## Decision

- Metrics: Prometheus (15 days) with Alertmanager, scraping node_exporter, cAdvisor, postgres_exporter and the Mosquitto, NATS, Keycloak, HAPI and triage metrics endpoints. Logs: Loki (7 days) fed by Grafana Alloy as structured container logs.
  Dashboards are code (Grafana provisioning): pipeline overview, delivery and latency, consumer lag, alerts, resource use, backup freshness.
- Alerts reach Byron through Grafana alerting to an ntfy or Telegram webhook (no sensitive content in messages), plus an OCI Monitoring alarm for "VM down" as the out-of-band path.
- **SLOs** are defined as Prometheus recording rules and are measured, not contractual: ingest delivery ratio at least 99 % (the H2 bound), end-to-end p95 latency under 2 s on the home path (the H1 bound),
  consumer lag under 5 s, API availability, backup age under 26 h. No SLA is claimed.
- Each failure drill (broker, NATS, Postgres, HAPI, VM reboot, gateway offline, revoked and expired certificates, Keycloak down) gets a blameless note in `docs/dr.md` with the observed recovery.
- Distributed tracing (Tempo, OpenTelemetry) is design-only.

## Consequences

- The stack's resident memory (about 1.25 GB in the budget) competes with the data services; retention caps are required, and real `docker stats` numbers replace the estimates after Checkpoint 6.
- The triage worker's latency is an approximation; the authoritative H1 figure still comes from `eval/latency.py`, and cross-host clock offset must be measured (Unit 16).
- Each SLO is reported as measured, inferred or not verified.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §5b, §5d, §9 Units 12 and 16; [decision 0004](0004-end-to-end-loss-recovery.md) (the H2 bound).
