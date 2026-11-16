"""Alembic environment: connects with the URL given by migrate.py (or EHG_DB_URL) and runs each migration in a transaction."""

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
