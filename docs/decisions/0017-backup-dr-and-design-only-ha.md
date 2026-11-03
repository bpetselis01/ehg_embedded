# 0017. Backups, a tested restore drill, and design-only high availability

**Date:** 2026-11-03

## Status

Accepted (Checkpoint 1, 2026-11-03; status of the build is stated in each record). Backups and the restore drill are implemented by Unit 12; high availability is documentation only.

## Context

One free VM with no SLA cannot give multi-zone availability. What it can give is the ability to rebuild quickly and to prove that the backups restore, which is what a thesis can honestly measure.

## Decision

- Postgres (all three databases) is backed up with **pgBackRest** (full, incremental and WAL archiving for point-in-time recovery); pgBackRest with the TimescaleDB image is **unverified** and checked first.
  `restic` covers the NATS stream snapshot, the Keycloak realm export, configuration and the latest audit-chain head hash. Backups are encrypted with a key that is not on the VM and go to OCI Object Storage (20 GB cap) and to the Mac.
- Declared **RPO about 24 hours**. RTO is whatever the drill measures, not an estimate.
- `restore_drill.sh` rebuilds the stack from Terraform, cloud-init and a backup on a clean target (the Mac's Docker, never the VM), then runs the parity tool and the audit `--verify`, and times the whole thing.
- Shipping the audit chain's head hash off the VM is what lets someone detect a database superuser rewriting the chain.
- **Design-only, never described as built:** multi-AZ or multi-region deployment, a clustered broker (EMQX is BSL; HiveMQ is paid), Postgres replicas, restoring onto an Azure free VM, write-once (WORM) audit storage,
  fleet-wide staged firmware rollout, App Store distribution and a real LTE-M or NB-IoT modem. Each is written up with its prerequisites and honest limits in `docs/dr.md`.

## Consequences

- Recovery depends on backups and a key Byron holds; losing the key means losing the backups.
- The drill runs on a different machine class than production, so the RTO is only an indication of the VM's recovery time, stated with that caveat.
- The 20 GB object-storage cap and the 24-hour interval limit what can be backed up; the NATS snapshot and retention settings are sized to fit.

**Sources:** [hardening prompt](../claude-code-prompt-thesis-b-hardening.md) §4b (design-only list), §5, §9 Unit 12; [decision 0010](0010-oracle-always-free-cloud-target.md); [decision 0012](0012-timescaledb-nats-data-platform.md).
