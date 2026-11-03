"""Base schema: ehg_raw, ehg_pkt, ehg_health as plain PostgreSQL tables (works on any PostgreSQL 14 or later).

PURPOSE: The enterprise store's tables, mirroring the InfluxDB measurements field for field (docs/interface-spec.md 5). TimescaleDB features are a separate revision (0002) so that this one
  can be tested on a plain PostgreSQL and so that a missing extension fails loudly there, not here.
INPUT: an empty database. OUTPUT: tables ehg_raw (one row per frame and channel, raw integer samples), ehg_pkt (one row per packet, timing fields), ehg_health (one row per node per minute, written by the triage worker).
  e.g. ehg_raw row: (2026-10-06 02:31:08.018676+00, 'EHG-93C0', '0', 0, 0, 0, 1093)
HOW: Primary keys make replays idempotent through ON CONFLICT DO NOTHING; each includes `time`, which a TimescaleDB hypertable requires. Samples are `integer`, never converted units.
ROLE IN THESIS: The schema the writers, the triage service and eval/backend_client.py (--backend timescale) agree on.
BEFORE -> AFTER: Before: nothing. After: three empty tables.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the three tables and the read indexes."""
    op.execute("""
        CREATE TABLE ehg_raw (
            time timestamptz NOT NULL, node text NOT NULL, record_id text NOT NULL, seq integer NOT NULL,
            frame_idx integer NOT NULL, ch smallint NOT NULL, v integer NOT NULL,
            PRIMARY KEY (node, seq, ch, time))""")
    op.execute("CREATE INDEX ehg_raw_node_time ON ehg_raw (node, time DESC)")
    op.execute("""
        CREATE TABLE ehg_pkt (
            time timestamptz NOT NULL, node text NOT NULL, seq integer NOT NULL, n_frames integer NOT NULL,
            retrans boolean NOT NULL, replayed boolean NOT NULL DEFAULT false, t_inject_dev bigint NOT NULL,
            t_inject_host double precision, t_gw_rx double precision, t_mqtt_pub double precision,
            t_ingest_rx double precision, t_db_ack double precision,
            PRIMARY KEY (node, seq, time))""")
    op.execute("CREATE INDEX ehg_pkt_node_time ON ehg_pkt (node, time DESC)")
    op.execute("""
        CREATE TABLE ehg_health (
            time timestamptz NOT NULL, node text NOT NULL, delivery_pct double precision, p95_latency_ms double precision,
            rx integer, missing integer, unrecovered integer, flatline_channels integer, saturated_pct double precision, score integer,
            PRIMARY KEY (node, time))""")


def downgrade() -> None:
    """Drop the three tables (and with them every stored sample: only for throwaway databases)."""
    op.execute("DROP TABLE ehg_health")
    op.execute("DROP TABLE ehg_pkt")
    op.execute("DROP TABLE ehg_raw")
