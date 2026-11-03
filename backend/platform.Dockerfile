# PURPOSE: One small image for the Python services of the data platform: the bridge, the TimescaleDB writer and the migration job (the triage service joins in Unit 8).
# INPUT: build with the REPOSITORY ROOT as context (docker compose does this via backend/docker-compose.platform.yml), because the code is imported as backend.* .
# OUTPUT: image `ehg-platform` running as the unprivileged user 10001; the command is chosen per service (python -m backend.bridge.bridge, python -m backend.ts_writer.writer, python -m backend.db.migrate upgrade).
# HOW: python:3.13-slim (multi-arch, so it builds natively on the arm64 VM) plus the libraries, version-bounded. Only backend/ is copied in: no data, no keys, no .env.
# STATUS: UNVERIFIED, never built (Docker was not running).
# ROLE IN THESIS: Packaging of docs/decisions/0012-timescaledb-nats-data-platform.md.
# BEFORE -> AFTER: Before: only the ingest image existed. After: the platform services share this one.
FROM python:3.13-slim
RUN pip install --no-cache-dir "nats-py>=2.16,<3" "psycopg[binary]>=3.3,<4" "alembic>=1.20,<2" "sqlalchemy>=2.1,<3" "prometheus-client>=0.26,<1" "paho-mqtt>=2.1,<3" \
    && useradd --system --uid 10001 --no-create-home ehg
WORKDIR /app
COPY backend /app/backend
ENV PYTHONUNBUFFERED=1
USER 10001
