"""Apply or undo the database migrations (Alembic, hand-written SQL).

  uv run python -m backend.db.migrate upgrade              # to the latest revision (0003 needs TimescaleDB)
  uv run python -m backend.db.migrate upgrade 0001         # only the plain-PostgreSQL schema
  uv run python -m backend.db.migrate downgrade 0001       # one step back; "base" removes everything

PURPOSE: One command that brings the enterprise store's schema to a known revision, the same way on the laptop, in CI and on the VM.
INPUT: command (upgrade | downgrade | current), an optional revision (default head / required for downgrade), and the environment variable EHG_DB_URL
  (SQLAlchemy URL, e.g. postgresql+psycopg://ehg:SECRET@localhost:5432/ehg) or --url.
OUTPUT: Alembic's own lines ("Running upgrade  -> 0001, ..."), then the current revision. Exit status 0 only if the database is at the requested revision. A failure leaves the
  failed revision unapplied (each revision runs in its own transaction), never half-applied.
  e.g. `current` prints "0003 (head)" or "0002" or "(no revision: empty database)".
HOW: Builds an Alembic Config in code (no alembic.ini) pointing at backend/db/migrations.
ROLE IN THESIS: The migration step of the restore drill and of the cloud deployment (docs/decisions/0017).
BEFORE -> AFTER: Before: no schema. After: tables from revisions 0001 and 0002, hypertables and roll-ups from 0003.
"""

import argparse
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

SCRIPTS = Path(__file__).parent / "migrations"


def config_for(url: str) -> Config:
    """Alembic configuration pointing at this folder, with the database URL attached (not written to any file)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(SCRIPTS))
    cfg.attributes["url"] = url
    return cfg


def current_revision(url: str):
    """The revision the database is at, or None if it has never been migrated."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def main(argv=None) -> int:
    """Run the requested command; returns the process exit code."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["upgrade", "downgrade", "current"])
    ap.add_argument("revision", nargs="?", help="target revision (upgrade: default head; downgrade: required, or base)")
    ap.add_argument("--url", default=os.environ.get("EHG_DB_URL"), help="SQLAlchemy URL (default: $EHG_DB_URL)")
    a = ap.parse_args(argv)
    if not a.url:
        ap.error("no database: set EHG_DB_URL or pass --url")
    if a.command == "downgrade" and not a.revision:
        ap.error("downgrade needs a target revision (0001 or base); it will not guess")
    cfg = config_for(a.url)
    if a.command == "upgrade":
        command.upgrade(cfg, a.revision or "head")
    elif a.command == "downgrade":
        command.downgrade(cfg, a.revision)
    rev = current_revision(a.url)
    print(rev or "(no revision: empty database)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
