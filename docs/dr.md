# Backups, restore drill and failure drills

**Status (2026-11-04).** The backup and restore scripts are written and tested against throwaway local PostgreSQL servers. Nothing has been run on the VM, against TimescaleDB, against a restic repository or in Docker. **No failure drill has been run**, so every "observed" cell below is empty on purpose.

## What is backed up, and how

| What | How | Where it goes | Status |
|---|---|---|---|
| Roles (with password hashes) | `pg_dumpall --globals-only` in [`backup.sh`](../deploy/backup/backup.sh) | the backup directory (mode 700) | tested locally |
| Databases `ehg`, `hapi`, `keycloak` | `pg_dump --format=custom` each | the backup directory | tested locally (plain PostgreSQL 14; not TimescaleDB) |
| Audit-chain head (last id and hash) | recorded in `audit_head.txt`, compared after restore | the backup directory | tested locally |
| Everything above, encrypted and off the VM | `restic backup` when `RESTIC_REPOSITORY` and `RESTIC_PASSWORD_FILE` are set; `--extra PATH` adds configuration | OCI Object Storage and the Mac | **never run** (restic is not installed here) |
| Freshness metric | `/var/lib/ehg-metrics/backup.prom`, read by node-exporter, written only after full success | Prometheus | tested locally |

**Deliberately not covered:**
- **NATS stream.** It is a transit buffer. A message the MQTT bridge acknowledged but no writer had stored yet is lost with the VM. The databases hold everything already stored.
- **Point-in-time recovery.** [Decision 0017](decisions/0017-backup-dr-and-design-only-ha.md) named pgBackRest; it was **not built** because pgBackRest with the TimescaleDB image cannot be checked here and a nightly logical dump already meets the declared RPO.
- **Keycloak realm export** and step-ca's own state: the realm is code (`deploy/keycloak/realm-ehg.json`); users and the CA's database are not backed up yet. Losing the CA key means re-enrolling every gateway.
- **The restic key.** It is held by Byron and is not on the VM. Losing it loses the encrypted backups.

**Declared objectives.** RPO about **24 hours** (nightly). RTO: *not measured*; the drill reports the time it took on its target and that is the only number to quote, with the caveat that the target is not the VM.

## Nightly schedule (by hand, once)

On the VM, as the `ehg` user, a cron entry or systemd timer running `deploy/backup/backup.sh --pg-prefix "docker exec -i -u postgres ehg-postgres-1"` at about 02:00, with `RESTIC_REPOSITORY` and `RESTIC_PASSWORD_FILE` in its environment. Not installed by cloud-init: run `--dry-run` first and read the plan.

## Restore drill

Run on a **clean** PostgreSQL (the Mac's Docker, never the VM's live database; the script refuses if a database of the same name exists):

```sh
deploy/backup/restore_drill.sh --backup <backup directory> --dry-run
PGHOST=... PGPORT=... PGUSER=postgres EHG_TRIAGE_DSN="host=... port=... user=postgres dbname=ehg" deploy/backup/restore_drill.sh --backup <backup directory>
```

It checks checksums, applies roles, restores each database (calling `timescaledb_pre_restore()` and `timescaledb_post_restore()` when the dump contains the extension: **untested**), compares the restored audit head with the recorded one, runs `python -m backend.triage.audit --verify`, prints the elapsed time and writes `drill-result.txt`.
After a real restore also run `eval/parity.py` between the restored store and the InfluxDB reference.

**Tested** (`tests/test_backup_scripts.py`): dump and restore on a second clean server with the audit chain verified; a flipped byte is refused before anything is restored; a second run never overwrites; a failed audit check fails the drill; a failed backup keeps the old freshness timestamp (so the alert fires); rotation removes only its own old directories; unsafe names are refused.

## Failure drills (to run after the first deployment; blameless notes go in the last column)

| Drill | Expected behaviour (design) | Alert expected | Observed |
|---|---|---|---|
| Stop Mosquitto | Gateways keep packets in their durable outbox and replay on return; no loss | `EhgNoTelemetry`, `EhgTargetDown` | not run |
| Stop NATS | Bridge cannot publish, so it does not acknowledge MQTT; the broker holds QoS 1 messages | `EhgConsumerLagUnknown` | not run |
| Stop PostgreSQL | Writer reports `ehg_writer_degraded 1` and does not acknowledge; NATS holds the messages; no loss while retention lasts | `EhgWriterDegraded` | not run |
| Stop HAPI | The sync worker logs and retries; the facade answers 502; the pipeline is unaffected | none today (add a HAPI scrape) | not run |
| Reboot the VM | Containers restart (`unless-stopped`); gateways reconnect with backoff | `EhgTargetDown` for a while | not run |
| Gateway offline | `GATEWAY_OFFLINE` alert (the silence is the gateway's, not the node's) | triage alert | not run |
| Revoked certificate | The CRL is read at broker start or SIGHUP only; `deploy/crl-sync.sh` (cron, every 5 minutes) fetches, verifies and installs it and sends SIGHUP: written and tested, never run on the VM. A connection that is already open is not dropped by a reload | none | not run; known gap |
| Expired certificate | The gateway cannot connect and must re-enrol (7-day life) | `EhgNoTelemetry` | not run |
| Keycloak down | Existing tokens work until they expire (5 minutes); no new sign-in | none today | not run |

## High availability

**Design-only.** One free VM has no second zone. Broker clustering, a replicated database and multi-region failover are described, not built. Recovery means rebuilding from Terraform, cloud-init and a backup, which is what the drill measures.
