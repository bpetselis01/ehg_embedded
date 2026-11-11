"""Shared pytest fixtures: a throwaway local PostgreSQL (no Docker) for the tests that need real SQL.

PURPOSE: Run the writer, migration, audit and registry tests against a real PostgreSQL when `initdb` and `pg_ctl` are installed (Homebrew: postgresql@14 or later), and skip them otherwise,
  as CI without a database does. TimescaleDB is NOT available this way: its tests are separate and run only when the extension exists.
INPUT: the PostgreSQL programs on PATH (or in /opt/homebrew/bin).
OUTPUT: fixture `pg_server` (one cluster for the whole test session, TCP on 127.0.0.1, trust authentication, removed afterwards) and `pg_url` (a fresh empty database per test, as a
  SQLAlchemy URL for psycopg 3; `pg_url.replace("+psycopg", "")` is the libpq form).
HOW: initdb into a short /tmp directory, pg_ctl on a free port, CREATE DATABASE per test. Nothing outside that directory is touched.
ROLE IN THESIS: Real-SQL tests without making Docker a requirement.
BEFORE -> AFTER: Before: SQL could only be tested with Docker. After: with a local PostgreSQL it can.
"""

import os
import shutil
import socket
import subprocess
import tempfile
import uuid

import pytest


def pytest_collection_modifyitems(config, items):
    """Hardware tests (marker `hardware`) need the DK, Bluetooth (Terminal.app) and the local backend: run only with EHG_HIL=1."""
    if os.environ.get("EHG_HIL") == "1":
        return
    skip = pytest.mark.skip(reason="needs the board: EHG_HIL=1 uv run pytest -m hardware -s, in Terminal.app")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)


def _pg_bin(name: str) -> str | None:
    """Path of a PostgreSQL program, looking in PATH and in Homebrew's directory."""
    return shutil.which(name) or (f"/opt/homebrew/bin/{name}" if os.path.exists(f"/opt/homebrew/bin/{name}") else None)


@pytest.fixture(scope="session")
def pg_server():
    """Start a throwaway PostgreSQL cluster; yields (host, port)."""
    initdb, pg_ctl = _pg_bin("initdb"), _pg_bin("pg_ctl")
    if not (initdb and pg_ctl):
        pytest.skip("PostgreSQL (initdb, pg_ctl) not installed: brew install postgresql@14")
    root = tempfile.mkdtemp(prefix="ehgpg", dir="/tmp")
    data = os.path.join(root, "data")
    subprocess.run([initdb, "-D", data, "-U", "postgres", "-A", "trust", "--no-sync", "-E", "UTF8"], check=True, capture_output=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    opts = f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories={root} -c fsync=off -c synchronous_commit=off"
    start = subprocess.run([pg_ctl, "-D", data, "-o", opts, "-l", os.path.join(root, "log"), "-w", "start"], capture_output=True, text=True)
    if start.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        pytest.skip("could not start a throwaway PostgreSQL: " + start.stderr[-200:])
    yield "127.0.0.1", port
    subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def pg_url(pg_server):
    """A new empty database on the throwaway server, as postgresql+psycopg://postgres@127.0.0.1:PORT/NAME."""
    import psycopg
    host, port = pg_server
    name = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(f"host={host} port={port} user=postgres dbname=postgres", autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
        for role in ("ehg_triage", "ehg_enroll", "ehg_fhir"):
            c.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN CREATE ROLE {role} LOGIN; END IF; END $$")  # cluster-wide, like the init script
    return f"postgresql+psycopg://postgres@{host}:{port}/{name}"
