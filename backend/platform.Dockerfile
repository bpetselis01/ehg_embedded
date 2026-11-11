# PURPOSE: One small image for the two Python services left in the cloud stack: the migration job and the FHIR sync worker (tools/fhir_export.py and eval/backend_client.py are copied in for it).
# INPUT: build with the REPOSITORY ROOT as context (deploy/compose/docker-compose.cloud.yml does this), because the code is imported as backend.* .
# OUTPUT: image `ehg-platform` running as the unprivileged user 10001; the command is chosen per service (python -m backend.db.migrate upgrade, python -m backend.fhir_sync.worker).
# HOW: python:3.13-slim (multi-arch, so it builds natively on the arm64 VM) plus the libraries, version-bounded. Only backend/ is copied in: no data, no keys, no .env.
# STATUS: an earlier, larger version was built and scanned with trivy on 2026-11-04. This trimmed version (2026-11-11) has not been built.
# ROLE IN THESIS: Packaging of docs/decisions/0012-timescaledb-nats-data-platform.md.
# BEFORE -> AFTER: Before: it also carried the bridge, writer, triage and enrolment services. After: those were removed in favour of standard tools (docs/licences.md).
FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1
RUN pip install --no-cache-dir "psycopg[binary]>=3.3,<4" "alembic>=1.20,<2" "sqlalchemy>=2.1,<3" "numpy>=2,<3" "httpx>=0.28,<1" \
    && pip uninstall -y pip \
    && useradd --system --uid 10001 --no-create-home ehg   # pip is removed again: its vendored msgpack/urllib3/setuptools carried known HIGH CVEs (trivy, 2026-11-04), and nothing needs pip at run time
WORKDIR /app
COPY backend /app/backend
COPY tools/fhir_export.py /app/tools/
COPY eval/backend_client.py /app/eval/
ENV PYTHONUNBUFFERED=1
USER 10001
