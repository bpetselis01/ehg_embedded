"""TimescaleDB: hypertables, compression, retention and the health roll-ups (needs the timescaledb extension; fails loudly without it).

PURPOSE: Turn the plain tables of 0001 into time-series tables: chunks of one day, compression after one day, retention (default 90 days, EHG_RETENTION_DAYS), and hourly and daily
  roll-ups of ehg_health for the 7-day sparklines and the 24 h / 7 d / 30 d / 90 d ranges (docs/decisions/0012-timescaledb-nats-data-platform.md).
INPUT: a database migrated to 0002 on a server with the timescaledb extension (the Community edition: compression and continuous aggregates need it, see docs/licences.md).
OUTPUT: hypertables ehg_raw, ehg_pkt, ehg_health; policies; continuous aggregates ehg_health_hourly and ehg_health_daily (columns bucket, node, delivery_pct, p95_latency_ms, rx, missing, score).
HOW: CREATE EXTENSION raises an error if TimescaleDB is not installed, which is the point: no silent half-migration. Continuous aggregates cannot be created inside a transaction,
  so they use autocommit_block. STATUS: UNVERIFIED, never run against a TimescaleDB (none was available; Docker was not running). tests/test_db_migrations.py runs it only when
  the extension is available. Downgrade removes the policies and aggregates but leaves the tables as ordinary hypertables (TimescaleDB cannot turn a hypertable back into a table).
ROLE IN THESIS: The long-term, compressed store and the data behind the triage page's trends.
BEFORE -> AFTER: Before: plain tables. After: hypertables with compression and roll-ups.
"""

import os

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

RETENTION_DAYS = int(os.environ.get("EHG_RETENTION_DAYS", "90"))


def upgrade() -> None:
    """Hypertables, policies, continuous aggregates."""
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    for table, segment, compress_after, keep in (("ehg_raw", "node, ch", "1 day", RETENTION_DAYS), ("ehg_pkt", "node", "1 day", RETENTION_DAYS),
                                                 ("ehg_health", "node", "7 days", max(RETENTION_DAYS, 400))):
        op.execute(f"SELECT create_hypertable('{table}', 'time', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE, migrate_data => TRUE)")
        op.execute(f"ALTER TABLE {table} SET (timescaledb.compress, timescaledb.compress_segmentby = '{segment}', timescaledb.compress_orderby = 'time')")
        op.execute(f"SELECT add_compression_policy('{table}', INTERVAL '{compress_after}', if_not_exists => TRUE)")
        op.execute(f"SELECT add_retention_policy('{table}', INTERVAL '{keep} days', if_not_exists => TRUE)")
    with op.get_context().autocommit_block():
        for name, bucket, start, schedule in (("ehg_health_hourly", "1 hour", "3 hours", "5 minutes"), ("ehg_health_daily", "1 day", "3 days", "1 hour")):
            op.execute(f"""
                CREATE MATERIALIZED VIEW IF NOT EXISTS {name} WITH (timescaledb.continuous) AS
                SELECT time_bucket('{bucket}', time) AS bucket, node, avg(delivery_pct) AS delivery_pct, max(p95_latency_ms) AS p95_latency_ms,
                       sum(rx) AS rx, sum(missing) AS missing, max(score) AS score
                FROM ehg_health GROUP BY bucket, node WITH NO DATA""")
            op.execute(f"SELECT add_continuous_aggregate_policy('{name}', start_offset => INTERVAL '{start}', end_offset => INTERVAL '1 minute', "
                       f"schedule_interval => INTERVAL '{schedule}', if_not_exists => TRUE)")


def downgrade() -> None:
    """Remove the roll-ups and policies (the tables stay hypertables)."""
    with op.get_context().autocommit_block():
        op.execute("DROP MATERIALIZED VIEW IF EXISTS ehg_health_daily")
        op.execute("DROP MATERIALIZED VIEW IF EXISTS ehg_health_hourly")
    for table in ("ehg_raw", "ehg_pkt", "ehg_health"):
        op.execute(f"SELECT remove_retention_policy('{table}', if_exists => TRUE)")
        op.execute(f"SELECT remove_compression_policy('{table}', if_exists => TRUE)")
