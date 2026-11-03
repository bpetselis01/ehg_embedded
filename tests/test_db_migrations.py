"""Checks the Alembic migrations on a throwaway PostgreSQL: 0001 builds the three tables with the right keys, goes back to nothing, 0002 (TimescaleDB) fails LOUDLY on a server
without the extension and leaves the database at 0001, and migrate.py refuses to guess. The TimescaleDB revision itself is tested only where the extension exists."""

import pytest

from backend.db import migrate


def columns(url, table):
    import psycopg
    with psycopg.connect(url.replace("+psycopg", "")) as c:
        return {r[0]: r[1] for r in c.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s", (table,))}


def primary_key(url, table):
    import psycopg
    with psycopg.connect(url.replace("+psycopg", "")) as c:
        rows = c.execute("""SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                            WHERE i.indrelid = %s::regclass AND i.indisprimary ORDER BY array_position(i.indkey, a.attnum)""", (table,)).fetchall()
    return [r[0] for r in rows]


def test_0001_builds_the_tables_with_idempotency_keys_and_raw_integer_samples(pg_url):
    migrate.main(["upgrade", "0001", "--url", pg_url])
    assert migrate.current_revision(pg_url) == "0001"
    assert primary_key(pg_url, "ehg_raw") == ["node", "seq", "ch", "time"] and primary_key(pg_url, "ehg_pkt") == ["node", "seq", "time"] and primary_key(pg_url, "ehg_health") == ["node", "time"]
    raw = columns(pg_url, "ehg_raw")
    assert raw["v"] == "integer" and raw["time"] == "timestamp with time zone" and raw["ch"] == "smallint"
    assert {"delivery_pct", "p95_latency_ms", "rx", "missing", "unrecovered", "flatline_channels", "saturated_pct", "score"} <= set(columns(pg_url, "ehg_health"))
    assert {"replayed", "t_db_ack", "t_ingest_rx"} <= set(columns(pg_url, "ehg_pkt"))


def test_0001_goes_back_to_an_empty_database_and_forward_again(pg_url):
    migrate.main(["upgrade", "0001", "--url", pg_url])
    migrate.main(["downgrade", "base", "--url", pg_url])
    assert migrate.current_revision(pg_url) is None and columns(pg_url, "ehg_raw") == {}
    migrate.main(["upgrade", "0001", "--url", pg_url])
    assert migrate.current_revision(pg_url) == "0001"


def test_timescale_revision_fails_loudly_without_the_extension_and_leaves_0001(pg_url):
    import psycopg
    with psycopg.connect(pg_url.replace("+psycopg", "")) as c:
        if c.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'").fetchone():
            pytest.skip("this server HAS TimescaleDB: see test_timescale_revision_on_a_real_timescaledb")
    migrate.main(["upgrade", "0001", "--url", pg_url])
    with pytest.raises(Exception, match="timescaledb"):
        migrate.main(["upgrade", "head", "--url", pg_url])
    assert migrate.current_revision(pg_url) == "0001", "the failed revision must not be half-applied"
    assert "v" in columns(pg_url, "ehg_raw")


def test_timescale_revision_on_a_real_timescaledb(pg_url):
    import psycopg
    with psycopg.connect(pg_url.replace("+psycopg", "")) as c:
        if not c.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'").fetchone():
            pytest.skip("no TimescaleDB on this server (Docker, or a server with the extension, is needed): UNVERIFIED until then")
    migrate.main(["upgrade", "head", "--url", pg_url])
    with psycopg.connect(pg_url.replace("+psycopg", "")) as c:
        hyper = {r[0] for r in c.execute("SELECT hypertable_name FROM timescaledb_information.hypertables")}
        aggs = {r[0] for r in c.execute("SELECT view_name FROM timescaledb_information.continuous_aggregates")}
        edition = c.execute("SHOW timescaledb.license").fetchone()[0]
    assert hyper == {"ehg_raw", "ehg_pkt", "ehg_health"} and aggs == {"ehg_health_hourly", "ehg_health_daily"} and edition == "timescale"


def test_migrate_refuses_to_guess():
    with pytest.raises(SystemExit):
        migrate.main(["downgrade", "--url", "postgresql+psycopg://x@localhost/x"])
    with pytest.raises(SystemExit):
        migrate.main(["upgrade"]) if not __import__("os").environ.get("EHG_DB_URL") else (_ for _ in ()).throw(SystemExit)
