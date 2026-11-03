"""Triage and registry schemas: alerts, their history, the hash-chained audit log, and the device/subject registry (plain PostgreSQL).

PURPOSE: The relational half of the enterprise store (decisions 0011, 0013): operator alerts with a lifecycle, a tamper-EVIDENT audit trail, and who/what is enrolled. Plain PostgreSQL only,
  so it is tested on any PostgreSQL 14+ (the TimescaleDB features are revision 0003).
INPUT: a database at revision 0001, and a login role named ehg_triage (created by deploy/postgres/10-init-databases.sh; the migration FAILS if it does not exist, rather than skipping the grants).
OUTPUT: schema triage: alerts (one ACTIVE alert per node+rule, enforced by a partial unique index), alert_events, audit_log. Schema registry: gateway, gateway_cert (EVERY certificate issued, so a revocation can reach them all), node, subject (a pseudonymous research label, no personal data).
  audit_log: id, ts, actor, role, action, target, detail_json, prev_hash, hash; triggers raise an error on UPDATE, DELETE and TRUNCATE. ehg_triage may INSERT and SELECT audit_log, and nothing else on it.
HOW: This is tamper-EVIDENT, not write-once: a database superuser can still rewrite the table, so the chain head hash is shipped off the VM with the backups (decision 0017) and `python -m backend.triage.audit --verify` recomputes the chain.
ROLE IN THESIS: Alert management, audit and the registry behind the triage page and the enrolment API.
BEFORE -> AFTER: Before: only time-series tables. After: alerts, audit and registry tables.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the schemas, tables, triggers and grants."""
    op.execute("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ehg_triage') THEN RAISE EXCEPTION 'role ehg_triage does not exist: create it first (deploy/postgres/10-init-databases.sh)'; END IF; END $$")
    op.execute("CREATE SCHEMA registry")
    op.execute("""CREATE TABLE registry.gateway (
        id text PRIMARY KEY CHECK (id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'), kind text NOT NULL CHECK (kind IN ('python', 'ios')),
        cert_serial text, enrolled_at timestamptz, revoked_at timestamptz, last_seen timestamptz, app_version text)""")
    op.execute("""CREATE TABLE registry.gateway_cert (
        serial text PRIMARY KEY, gateway_id text NOT NULL REFERENCES registry.gateway (id), issued_at timestamptz NOT NULL, not_after timestamptz NOT NULL, revoked_at timestamptz, ca_revoked_at timestamptz)""")
    op.execute("""CREATE TABLE registry.subject (
        id text PRIMARY KEY, pseudonym text NOT NULL UNIQUE, source_dataset text NOT NULL,
        consent_status text NOT NULL DEFAULT 'none' CHECK (consent_status IN ('none', 'active', 'withdrawn')))""")
    op.execute("""CREATE TABLE registry.node (
        id text PRIMARY KEY, fw_version text, record text, fs_hz integer, channels integer,
        assigned_gateway text REFERENCES registry.gateway (id), subject_id text REFERENCES registry.subject (id), last_seen timestamptz)""")
    op.execute("CREATE SCHEMA triage")
    op.execute("""CREATE TABLE triage.alerts (
        id bigserial PRIMARY KEY, node text NOT NULL, rule text NOT NULL, severity text NOT NULL CHECK (severity IN ('warning', 'critical')),
        state text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'acknowledged', 'escalated', 'resolved')),
        opened_at timestamptz NOT NULL, condition_cleared_at timestamptz, resolved_at timestamptz)""")
    op.execute("CREATE UNIQUE INDEX alerts_one_active ON triage.alerts (node, rule) WHERE state <> 'resolved'")
    op.execute("""CREATE TABLE triage.alert_events (
        id bigserial PRIMARY KEY, alert_id bigint NOT NULL REFERENCES triage.alerts (id), ts timestamptz NOT NULL, actor text NOT NULL, role text NOT NULL,
        action text NOT NULL, from_state text, to_state text, note text)""")
    op.execute("""CREATE TABLE triage.audit_log (
        id bigint PRIMARY KEY, ts timestamptz NOT NULL, actor text NOT NULL, role text NOT NULL, action text NOT NULL, target text NOT NULL,
        detail_json jsonb NOT NULL, prev_hash text NOT NULL, hash text NOT NULL UNIQUE)""")
    op.execute("CREATE SEQUENCE triage.audit_log_id_seq OWNED BY triage.audit_log.id")
    op.execute("""CREATE FUNCTION triage.audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'triage.audit_log is append-only (% refused)', TG_OP; END $$""")
    op.execute("CREATE TRIGGER audit_no_update_delete BEFORE UPDATE OR DELETE ON triage.audit_log FOR EACH ROW EXECUTE FUNCTION triage.audit_append_only()")
    op.execute("CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON triage.audit_log FOR EACH STATEMENT EXECUTE FUNCTION triage.audit_append_only()")
    op.execute("GRANT USAGE ON SCHEMA triage, registry TO ehg_triage")
    op.execute("GRANT SELECT, INSERT, UPDATE ON triage.alerts TO ehg_triage")
    op.execute("GRANT SELECT, INSERT ON triage.alert_events, triage.audit_log TO ehg_triage")
    op.execute("GRANT USAGE ON SEQUENCE triage.alerts_id_seq, triage.alert_events_id_seq, triage.audit_log_id_seq TO ehg_triage")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA registry TO ehg_triage")
    op.execute("GRANT SELECT ON ehg_pkt, ehg_raw TO ehg_triage")
    op.execute("GRANT SELECT, INSERT ON ehg_health TO ehg_triage")


def downgrade() -> None:
    """Drop both schemas (and with them every alert and audit row: only for throwaway databases)."""
    op.execute("DROP SCHEMA triage CASCADE")
    op.execute("DROP SCHEMA registry CASCADE")
