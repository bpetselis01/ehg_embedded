#!/bin/sh
# PURPOSE: Prove that a backup restores: verify its checksums, recreate roles and databases on a CLEAN PostgreSQL, and time the whole thing. The measured time is the RTO of docs/dr.md (with the caveat that it ran on this target).
# INPUT: --backup DIR (one ehg-backup-<timestamp> directory made by backup.sh); the TARGET through PGHOST, PGPORT, PGUSER, PGPASSWORD or --pg-prefix "docker exec -i -u postgres ehg-postgres-1"-style prefix (plain docker exec, see backup.sh); --databases "ehg hapi keycloak";
#   --result FILE (default DIR/drill-result.txt); --dry-run.
#   e.g. PGHOST=127.0.0.1 PGPORT=55432 PGUSER=postgres restore_drill.sh --backup /srv/ehg-data/backups/ehg-backup-20261104T020000Z
# OUTPUT: prints each step, then "RESTORE DRILL OK in N s" and writes the same line to the result file. Exit status 1 on ANY failure (checksum mismatch, a database that already exists, a failed restore).
#   The audit-chain check that used to follow the restore was removed on 2026-11-11 with the triage service that wrote the chain.
# HOW: REFUSES to run when a target database of the same name already exists (it never overwrites): run it on a fresh server, never on the VM's live database. Roles are applied from roles.sql (errors for roles that already exist, such as the superuser, are expected and ignored).
#   When the TimescaleDB extension is present in a dump, timescaledb_pre_restore() is called before pg_restore and timescaledb_post_restore() after (RUN once on 2026-11-04 against the real timescale/timescaledb-ha image, see docs/progress.md).
# STATUS: tested against a throwaway local PostgreSQL (tests/test_backup_scripts.py). The Timescale branch and a Docker target ran once for real (2026-11-04); the restic restore path was never run.
# ROLE IN THESIS: The timed restore drill of decision 0017. An untested backup is not counted as a backup.
# BEFORE -> AFTER: Before: nothing showed a backup could be used. After: a script that does it and says how long it took.
set -eu

BACKUP=""
DATABASES="ehg hapi keycloak"
PG_PREFIX=""
RESULT=""
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --backup) BACKUP="$2"; shift 2 ;;
    --databases) DATABASES="$2"; shift 2 ;;
    --pg-prefix) PG_PREFIX="$2"; shift 2 ;;
    --result) RESULT="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -n "$BACKUP" ] || { echo "--backup DIR is required" >&2; exit 2; }
for db in $DATABASES; do
  case "$db" in *[!A-Za-z0-9_]*|'') echo "unsafe database name: $db" >&2; exit 2 ;; esac
done
[ -n "$RESULT" ] || RESULT="$BACKUP/drill-result.txt"

pg() {
  # shellcheck disable=SC2086
  $PG_PREFIX "$@"
}

if [ "$DRY" = 1 ]; then
  echo "[dry-run] would check $BACKUP/SHA256SUMS"
  echo "[dry-run] would refuse if any of these databases already exists on the target: $DATABASES"
  echo "[dry-run] would apply roles.sql, then for each database: createdb, (timescaledb_pre_restore if present), pg_restore, (timescaledb_post_restore)"
  echo "[dry-run] would write $RESULT"
  exit 0
fi

START=$(date +%s)
[ -d "$BACKUP" ] || { echo "no such backup directory: $BACKUP" >&2; exit 1; }

echo "1/4 checking checksums"
( cd "$BACKUP" && if command -v sha256sum >/dev/null 2>&1; then sha256sum -c SHA256SUMS; else shasum -a 256 -c SHA256SUMS; fi ) >/dev/null || { echo "CHECKSUM MISMATCH: the backup is damaged or altered" >&2; exit 1; }

echo "2/4 checking the target is clean"
for db in $DATABASES; do
  found=$(pg psql --no-psqlrc -At -d postgres -c "SELECT 1 FROM pg_database WHERE datname = '$db'")
  [ -z "$found" ] || { echo "REFUSING: database $db already exists on the target; this script never overwrites" >&2; exit 1; }
done

echo "3/4 restoring roles"
pg psql --no-psqlrc -q -d postgres < "$BACKUP/roles.sql" >/dev/null 2>&1 || true  # already-existing roles (the superuser) make psql complain; the databases below fail loudly if a needed role is missing

echo "4/4 restoring databases"
for db in $DATABASES; do
  pg createdb "$db"
  has_ts=$(pg pg_restore --list < "$BACKUP/$db.dump" | grep -c "EXTENSION - timescaledb" || true)
  if [ "$has_ts" != 0 ]; then pg psql --no-psqlrc -q -d "$db" -c "CREATE EXTENSION IF NOT EXISTS timescaledb" -c "SELECT timescaledb_pre_restore()" >/dev/null; fi
  pg pg_restore --no-owner --exit-on-error -d "$db" < "$BACKUP/$db.dump" || { echo "RESTORE FAILED for $db" >&2; exit 1; }
  if [ "$has_ts" != 0 ]; then pg psql --no-psqlrc -q -d "$db" -c "SELECT timescaledb_post_restore()" >/dev/null; fi
  echo "   restored $db"
done

END=$(date +%s)
LINE="RESTORE DRILL OK in $((END - START)) s"
echo "$LINE"
echo "$LINE" > "$RESULT"
