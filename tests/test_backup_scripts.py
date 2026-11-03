"""Checks deploy/backup/backup.sh and restore_drill.sh against two throwaway local PostgreSQL clusters (no Docker): a real dump, a real restore on a CLEAN second server, the checksum guard, the audit-chain proof,
the never-overwrite rule, rotation that touches only its own directories, and that a failed backup does NOT refresh the freshness metric. NOT covered: TimescaleDB, restic, a Docker target (see docs/dr.md)."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
import pytest

from backend.db import migrate
from backend.triage.audit import AuditLog

ROOT = Path(__file__).parents[1]
BACKUP, DRILL = ROOT / "deploy/backup/backup.sh", ROOT / "deploy/backup/restore_drill.sh"


def pg_bin(name):
    return shutil.which(name) or (f"/opt/homebrew/bin/{name}" if os.path.exists(f"/opt/homebrew/bin/{name}") else None)


@pytest.fixture(scope="module")
def clean_cluster():
    """A second, empty PostgreSQL server: the restore target."""
    initdb, pg_ctl = pg_bin("initdb"), pg_bin("pg_ctl")
    if not (initdb and pg_ctl and pg_bin("pg_dump") and pg_bin("pg_restore")):
        pytest.skip("PostgreSQL client and server programs not installed")
    root = tempfile.mkdtemp(prefix="ehgbk", dir="/tmp")
    subprocess.run([initdb, "-D", f"{root}/data", "-U", "postgres", "-A", "trust", "--no-sync", "-E", "UTF8"], check=True, capture_output=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    subprocess.run([pg_ctl, "-D", f"{root}/data", "-o", f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories={root} -c fsync=off", "-l", f"{root}/log", "-w", "start"], check=True, capture_output=True)
    yield "127.0.0.1", port
    subprocess.run([pg_ctl, "-D", f"{root}/data", "-m", "immediate", "stop"], capture_output=True)
    shutil.rmtree(root, ignore_errors=True)


def env_for(host, port, **extra):
    d = {**os.environ, "PGHOST": host, "PGPORT": str(port), "PGUSER": "postgres", "PATH": os.path.dirname(pg_bin("pg_dump")) + os.pathsep + os.environ["PATH"]}
    d.update(extra)
    return d


def sh(script, *args, env, check=False):
    r = subprocess.run(["sh", str(script), *args], env=env, capture_output=True, text=True, cwd=ROOT)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


@pytest.fixture
def source(pg_url, pg_server):
    """A database with the triage schema and three audit rows; returns (db name, backup env)."""
    migrate.main(["upgrade", "0002", "--url", pg_url])
    name = pg_url.rsplit("/", 1)[1]
    dsn = pg_url.replace("+psycopg", "")
    audit = AuditLog(lambda: psycopg.connect(dsn), emit=lambda *_: None)
    for i in range(3):
        audit.append("tester", "admin", "test.event", f"thing:{i}", {"i": i})
    return name, env_for(*pg_server)


def test_dry_run_prints_the_plan_and_changes_nothing(tmp_path, source):
    name, env = source
    r = sh(BACKUP, "--dry-run", "--out", str(tmp_path / "b"), "--databases", name, "--audit-db", name, "--metrics-dir", str(tmp_path / "m"), env=env, check=True)
    assert "[dry-run]" in r.stdout and "local copy only" in r.stdout
    assert not (tmp_path / "b").exists() and not (tmp_path / "m").exists()
    d = sh(DRILL, "--dry-run", "--backup", str(tmp_path / "nothing"), env=env, check=True)
    assert "[dry-run]" in d.stdout


def test_backup_then_restore_on_a_clean_server_proves_the_audit_chain(tmp_path, source, clean_cluster):
    name, env = source
    sh(BACKUP, "--out", str(tmp_path / "b"), "--databases", name, "--audit-db", name, "--metrics-dir", str(tmp_path / "m"), env=env, check=True)
    (dest,) = list((tmp_path / "b").glob("ehg-backup-*"))
    assert {p.name for p in dest.iterdir()} == {"roles.sql", f"{name}.dump", "audit_head.txt", "SHA256SUMS"}
    assert oct(dest.stat().st_mode & 0o777) == "0o700", "the directory (roles.sql holds password hashes) is private"
    assert (dest / "audit_head.txt").read_text().startswith("3 ")
    prom = (tmp_path / "m/backup.prom").read_text()
    assert "ehg_backup_last_success_timestamp_seconds " in prom and "ehg_backup_size_bytes " in prom

    host, port = clean_cluster
    target = env_for(host, port, EHG_TRIAGE_DSN=f"host={host} port={port} user=postgres dbname={name}")
    verify = tmp_path / "verify.sh"  # the drill splits --verify-cmd on spaces, and this checkout's path has some
    verify.write_text(f'#!/bin/sh\ncd "{ROOT}" && exec "{sys.executable}" -m backend.triage.audit --verify\n')
    r = sh(DRILL, "--backup", str(dest), "--databases", name, "--audit-db", name, "--verify-cmd", f"sh {verify}", env=target, check=True)
    assert "RESTORE DRILL OK in" in r.stdout and "OK: 3 rows" in r.stdout
    assert "RESTORE DRILL OK" in (dest / "drill-result.txt").read_text()
    with psycopg.connect(f"host={host} port={port} user=postgres dbname={name}") as c:
        assert c.execute("SELECT count(*) FROM triage.audit_log").fetchone()[0] == 3
    again = sh(DRILL, "--backup", str(dest), "--databases", name, "--audit-db", name, env=target)
    assert again.returncode == 1 and "REFUSING" in again.stderr, "a second run never overwrites what the first restored"


def test_a_damaged_backup_is_refused_before_anything_is_restored(tmp_path, source, clean_cluster):
    name, env = source
    sh(BACKUP, "--out", str(tmp_path / "b"), "--databases", name, "--audit-db", name, "--metrics-dir", str(tmp_path / "m"), env=env, check=True)
    (dest,) = list((tmp_path / "b").glob("ehg-backup-*"))
    dump = dest / f"{name}.dump"
    data = bytearray(dump.read_bytes())
    data[len(data) // 2] ^= 0xFF
    dump.write_bytes(bytes(data))
    host, port = clean_cluster
    r = sh(DRILL, "--backup", str(dest), "--databases", name, "--audit-db", name, env=env_for(host, port))
    assert r.returncode == 1 and "CHECKSUM MISMATCH" in r.stderr
    with psycopg.connect(f"host={host} port={port} user=postgres dbname=postgres") as c:
        assert c.execute("SELECT count(*) FROM pg_database WHERE datname = %s", (name,)).fetchone()[0] == 0


def test_a_failed_audit_check_after_restore_fails_the_drill(tmp_path, source, clean_cluster):
    name, env = source
    sh(BACKUP, "--out", str(tmp_path / "b"), "--databases", name, "--audit-db", name, "--metrics-dir", str(tmp_path / "m"), env=env, check=True)
    (dest,) = list((tmp_path / "b").glob("ehg-backup-*"))
    host, port = clean_cluster
    r = sh(DRILL, "--backup", str(dest), "--databases", name, "--audit-db", name, "--verify-cmd", "false", env=env_for(host, port))
    assert r.returncode == 1 and "AUDIT CHAIN VERIFICATION FAILED" in r.stderr and not (dest / "drill-result.txt").exists()


def test_a_failed_backup_does_not_refresh_the_freshness_metric(tmp_path, source):
    name, env = source
    m = tmp_path / "m"
    m.mkdir()
    (m / "backup.prom").write_text("ehg_backup_last_success_timestamp_seconds 1\n")
    r = sh(BACKUP, "--out", str(tmp_path / "b"), "--databases", f"{name} no_such_database", "--audit-db", name, "--metrics-dir", str(m), env=env)
    assert r.returncode != 0
    assert (m / "backup.prom").read_text() == "ehg_backup_last_success_timestamp_seconds 1\n", "the old timestamp stays, so EhgBackupStale fires"


def test_rotation_removes_only_old_backup_directories_of_its_own_name(tmp_path, source):
    name, env = source
    out = tmp_path / "b"
    for stamp in ("20200101T000000Z", "20200102T000000Z", "20200103T000000Z"):
        (out / f"ehg-backup-{stamp}").mkdir(parents=True)
    (out / "keep-me").mkdir()
    sh(BACKUP, "--out", str(out), "--databases", name, "--audit-db", name, "--keep", "2", "--metrics-dir", str(tmp_path / "m"), env=env, check=True)
    left = sorted(p.name for p in out.iterdir())
    assert "keep-me" in left and len([n for n in left if n.startswith("ehg-backup-")]) == 2
    assert "ehg-backup-20200101T000000Z" not in left and "ehg-backup-20200102T000000Z" not in left


def test_unsafe_names_and_options_are_refused(tmp_path):
    env = {**os.environ}
    for bad in ("a;b", "a b$(x)", "x'y"):
        assert sh(BACKUP, "--dry-run", "--databases", bad, env=env).returncode == 2
    assert sh(BACKUP, "--keep", "x", "--dry-run", env=env).returncode == 2 and sh(BACKUP, "--nope", env=env).returncode == 2
    assert sh(DRILL, "--dry-run", env=env).returncode == 2, "--backup is required"
