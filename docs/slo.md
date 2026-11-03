# Service-level objectives

**What this is.** Targets the cloud core is *measured* against, as Prometheus recording rules ([`deploy/observability/rules/slo.yml`](../deploy/observability/rules/slo.yml)) and a dashboard ([`pipeline-overview.json`](../deploy/observability/grafana/dashboards/pipeline-overview.json)).
They are objectives for a research prototype on one free VM. **No SLA is offered or implied.** Status words follow the project rule: *measured* (a number from a real run), *inferred* (follows from a design or a test), *not verified* (written, never run).
Nothing below has been measured yet: the stack has never started (decision 0016, [progress.md](progress.md)).

| # | Objective | Source series | Alert | Where the bound comes from | Status |
|---|---|---|---|---|---|
| 1 | Packet delivery ratio at least **99 %**, fleet-wide, 5-minute window | `ehg:delivery_ratio:5m` (sum of received over received + still missing, from the triage engine's per-minute counts) | `EhgDeliveryBelowObjective` (10 min) | The H2 bound of [decision 0004](decisions/0004-end-to-end-loss-recovery.md) | not verified |
| 2 | Worst-node 60 s p95 latency under **2 s** (home path) | `ehg:latency_p95_seconds:5m` | `EhgLatencyAboveObjective` (10 min) | The H1 bound; the validated home-path figures were 259 ms (p50) and 478 ms (p95) without the cellular hop | not verified |
| 3 | Bus consumer backlog under **100 messages** | `ehg:consumer_pending:max` (`ehg_consumer_pending`, from JetStream consumer info) | `EhgConsumerBacklog` (5 min); `EhgConsumerLagUnknown` when the broker cannot be asked | "Under 5 s": 100 messages is about 5 s at the nominal 2 packets/s per node for ten nodes. A count, not a time | not verified |
| 4 | Triage API availability | `ehg:api_availability:1h` (`up{job="triage"}`) | `EhgTargetDown` | None set; reported, not promised | not verified |
| 5 | Backup no older than **26 h** | `ehg:backup_age_seconds:max` (from the file `backup.sh` writes) | `EhgBackupStale`, `EhgBackupNeverReported` | Nightly run plus a 2 h margin; the declared RPO is about 24 h ([dr.md](dr.md)) | the freshness logic is tested against a real PostgreSQL; the schedule is not set up |

## What each number does not show

- **Delivery ratio** counts packets the *triage worker* saw against sequence gaps it saw. A node that is switched off sends nothing and is not "missing" packets: that case is `NODE_OFFLINE` and `EhgNoTelemetry`, not a bad ratio. An empty window gives *no value* (0/0), never 100 %.
  The authoritative H2 figure is still `eval/integrity.py` against the database.
- **Latency** is the worker's approximation (decision 0011). The authoritative H1 figure is `eval/latency.py`. Across hosts it also depends on clock offset between the phone, the Mac and the VM, which Unit 16 is to measure.
- **Consumer backlog** shows that consumers keep up, not that every message was stored: for that, run `eval/parity.py` or `eval/integrity.py`.
- **Availability** is "Prometheus could scrape the API", not "a person could sign in" (Keycloak and Caddy are separate).
- **Unknown is never zero.** A backlog or latency that cannot be read is NaN, a gap on the chart and an alert, so silence is not mistaken for health.

## Not built

Prometheus exporters for Mosquitto and NATS (their health is seen through the consumers and the writer), distributed tracing, per-node SLOs, error-budget burn alerts, an OCI "VM down" alarm check (the Terraform alarm exists and is unapplied).
