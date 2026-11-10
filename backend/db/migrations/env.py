"""Alembic environment: connects with the URL given by migrate.py (or EHG_DB_URL) and runs each migration in a transaction.

PURPOSE: Lets `alembic` apply the hand-written SQL revisions in versions/ to any PostgreSQL (docs/decisions/0012-timescaledb-nats-data-platform.md).
INPUT: config.attributes["url"] (set by backend/db/migrate.py) or the environment variable EHG_DB_URL, e.g. postgresql+psycopg://ehg:***@postgres:5432/ehg.
OUTPUT: schema changes. No autogenerate: every revision is SQL written by hand, because hypertables, compression and continuous aggregates are not SQLAlchemy objects.
ROLE IN THESIS: Schema versioning for the enterprise store.
BEFORE -> AFTER: Before: nothing created the tables. After: `python -m backend.db.migrate upgrade` does.
"""

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def run_migrations_online() -> None:
    """Connect and run the pending revisions."""
    url = config.attributes.get("url") or os.environ["EHG_DB_URL"]
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
