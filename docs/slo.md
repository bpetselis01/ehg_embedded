# Service-level objectives

**What this is.** Targets the cloud core is *measured* against, as Prometheus recording rules ([`deploy/observability/rules/slo.yml`](../deploy/observability/rules/slo.yml)) and a dashboard ([`pipeline-overview.json`](../deploy/observability/grafana/dashboards/pipeline-overview.json)).
They are objectives for a research prototype on one free VM. **No SLA is offered or implied.** Status words follow the project rule: *measured* (a number from a real run), *inferred* (follows from a design or a test), *not verified* (written, never run).
Nothing below has been measured yet: the stack has never started (decision 0016, [progress.md](progress.md)).

**Trimmed 11 Nov 2026.** Objectives for delivery, latency, bus backlog and the triage API were removed with the services that produced their numbers. H1 and H2 are measured on the home path by `eval/latency.py` and `eval/integrity.py`; live alerts on that path are Grafana's own rules (`backend/grafana/provisioning/alerting/`).

| # | Objective | Source series | Alert | Where the bound comes from | Status |
|---|---|---|---|---|---|
| 5 | Backup no older than **26 h** | `ehg:backup_age_seconds:max` (from the file `backup.sh` writes) | `EhgBackupStale`, `EhgBackupNeverReported` | Nightly run plus a 2 h margin; the declared RPO is about 24 h ([dr.md](dr.md)) | the freshness logic is tested against a real PostgreSQL; the schedule is not set up |

## What each number does not show

- **Backup age** says a backup finished, not that it restores: that is the drill in [dr.md](dr.md).
- **Unknown is never zero.** A backup time that has never been reported raises `EhgBackupNeverReported`, so silence is not mistaken for health.

## Not built

A Mosquitto exporter (`sapcc/mosquitto-exporter` is the standard one; not configured here), distributed tracing, per-node SLOs, error-budget burn alerts, an OCI "VM down" alarm check (the Terraform alarm exists and is unapplied).
