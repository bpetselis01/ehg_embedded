# One small image for the two Python services left in the cloud stack: the migration job and the FHIR sync worker (tools/fhir_export.py and eval/backend_client.py are copied in for it).
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
