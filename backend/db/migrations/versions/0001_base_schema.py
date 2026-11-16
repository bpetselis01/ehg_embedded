"""Base schema: ehg_raw, ehg_pkt, ehg_health as plain PostgreSQL tables (works on any PostgreSQL 14 or later)."""

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
