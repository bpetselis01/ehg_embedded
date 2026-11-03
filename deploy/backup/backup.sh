#!/bin/sh
# PURPOSE: One consistent backup of the EHG databases: roles, every database as a pg_dump custom-format file, the audit-chain head, and a checksum list. Optionally sent to an encrypted restic repository.
# INPUT: PostgreSQL connection through the standard PG* variables (PGHOST, PGPORT, PGUSER, PGPASSWORD) or --pg-prefix "docker compose exec -T postgres" to run the PostgreSQL programs inside the container.
#   Options: --out DIR (where backups go, default /srv/ehg-data/backups), --databases "ehg hapi keycloak", --audit-db ehg (the database holding triage.audit_log), --keep N (local copies kept, default 7), --extra PATH (repeatable: more files for restic only, e.g. /opt/ehg/etc),
#   --metrics-dir DIR (default /var/lib/ehg-metrics), --dry-run (print the plan, change nothing).
#   Restic: RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE (the key is NOT on the VM: it is held by Byron; losing it loses the backups).
#   e.g. backup.sh --out /srv/ehg-data/backups --pg-prefix "docker compose -f deploy/compose/docker-compose.cloud.yml exec -T postgres" --dry-run
# OUTPUT: DIR/ehg-backup-<UTC timestamp>/ with roles.sql, <db>.dump, audit_head.txt, SHA256SUMS; the Prometheus file <metrics-dir>/backup.prom (ehg_backup_last_success_timestamp_seconds, ehg_backup_size_bytes) written ONLY after
#   everything succeeded, so a failed run leaves the old timestamp and the BackupStale alert fires. Exit status 0 only on full success.
# HOW: pg_dump per database (consistent snapshot of each), pg_dumpall --globals-only for roles (contains password hashes: mode 700 directory, and the files are only as safe as where they go; use restic for off-VM copies).
#   A TimescaleDB database restores with timescaledb_pre_restore()/post_restore() (restore_drill.sh does this). NOT covered, on purpose and stated in docs/dr.md: the NATS stream (in-flight messages), Keycloak's own database is covered but not its
#   realm export, point-in-time recovery (pgBackRest was not built; decision 0017 notes this). Declared RPO is about 24 h, which a nightly run meets.
# STATUS: the dump, checksum, rotation and metrics logic is tested against a throwaway local PostgreSQL (tests/test_backup_scripts.py). NEVER run against the VM, the TimescaleDB image, or a real restic repository (restic is not installed here).
# ROLE IN THESIS: Disaster recovery of docs/production-topology.md; the restore is proven by restore_drill.sh.
# BEFORE -> AFTER: Before: nothing was backed up. After: a nightly job (cron/systemd timer, set up by hand: see docs/dr.md) and a freshness metric.
set -eu

OUT=/srv/ehg-data/backups
DATABASES="ehg hapi keycloak"
AUDIT_DB=ehg
KEEP=7
METRICS_DIR=/var/lib/ehg-metrics
PG_PREFIX=""
DRY=0
EXTRAS=""

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --databases) DATABASES="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    --audit-db) AUDIT_DB="$2"; shift 2 ;;
    --metrics-dir) METRICS_DIR="$2"; shift 2 ;;
    --pg-prefix) PG_PREFIX="$2"; shift 2 ;;
    --extra) EXTRAS="$EXTRAS $2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$KEEP" in ''|*[!0-9]*) echo "--keep must be a number" >&2; exit 2 ;; esac
for db in $DATABASES $AUDIT_DB; do
  case "$db" in *[!A-Za-z0-9_]*|'') echo "unsafe database name: $db" >&2; exit 2 ;; esac
done

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$OUT/ehg-backup-$STAMP"
run() { # the plan line, then (unless dry) the command
  if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi
}
pg() { # a PostgreSQL program, optionally inside the container
  # shellcheck disable=SC2086
  $PG_PREFIX "$@"
}

if [ "$DRY" = 1 ]; then
  echo "[dry-run] would create $DEST (mode 700)"
  echo "[dry-run] would run: pg_dumpall --globals-only > $DEST/roles.sql"
  for db in $DATABASES; do echo "[dry-run] would run: pg_dump --format=custom $db > $DEST/$db.dump"; done
  echo "[dry-run] would record the audit-chain head from database $AUDIT_DB (table triage.audit_log)"
  echo "[dry-run] would write $DEST/SHA256SUMS and keep the newest $KEEP local backups"
  if [ -n "${RESTIC_REPOSITORY:-}" ]; then echo "[dry-run] would run: restic backup $DEST$EXTRAS, then restic forget --keep-daily 7 --keep-weekly 4 --prune"; else echo "[dry-run] RESTIC_REPOSITORY is not set: local copy only (NOT encrypted, NOT off the VM)"; fi
  echo "[dry-run] would write $METRICS_DIR/backup.prom after success"
  exit 0
fi

umask 077
mkdir -p "$DEST"
pg pg_dumpall --globals-only > "$DEST/roles.sql"
for db in $DATABASES; do
  pg pg_dump --format=custom "$db" > "$DEST/$db.dump"
done
# the audit chain's head (id and hash) so a restore can prove nothing was lost or altered; "none" when the table does not exist yet
pg psql --no-psqlrc -At -d "$AUDIT_DB" -c "SELECT id || ' ' || hash FROM triage.audit_log ORDER BY id DESC LIMIT 1" > "$DEST/audit_head.txt" 2>/dev/null || true
[ -s "$DEST/audit_head.txt" ] || echo none > "$DEST/audit_head.txt"
(
  cd "$DEST"
  if command -v sha256sum >/dev/null 2>&1; then sha256sum ./*.dump roles.sql audit_head.txt > SHA256SUMS; else shasum -a 256 ./*.dump roles.sql audit_head.txt > SHA256SUMS; fi
)

if [ -n "${RESTIC_REPOSITORY:-}" ]; then
  # shellcheck disable=SC2086
  restic backup "$DEST" $EXTRAS
  restic forget --keep-daily 7 --keep-weekly 4 --prune
else
  echo "warning: RESTIC_REPOSITORY is not set: this copy is local, unencrypted and on the same machine" >&2
fi

# keep the newest $KEEP local backups; only directories that carry our name are ever removed
ls -1d "$OUT"/ehg-backup-* 2>/dev/null | sort | awk -v keep="$KEEP" '{ a[NR] = $0 } END { for (i = 1; i <= NR - keep; i++) print a[i] }' | while read -r old; do rm -rf "$old"; done

SIZE=$(du -sk "$DEST" | awk '{ print $1 * 1024 }')
mkdir -p "$METRICS_DIR"
TMP="$METRICS_DIR/backup.prom.$$"
{
  echo "# TYPE ehg_backup_last_success_timestamp_seconds gauge"
  echo "ehg_backup_last_success_timestamp_seconds $(date -u +%s)"
  echo "# TYPE ehg_backup_size_bytes gauge"
  echo "ehg_backup_size_bytes $SIZE"
} > "$TMP"
mv "$TMP" "$METRICS_DIR/backup.prom"
echo "backup complete: $DEST"
