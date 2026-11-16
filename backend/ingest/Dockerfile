# Recipe for the ingest container: a small Python image + two libraries + ingest.py.
FROM python:3.13-slim
RUN pip install --no-cache-dir "paho-mqtt>=2.1,<3" "influxdb-client>=1.46,<2" "nats-py>=2.16,<3"
COPY ingest.py /app/ingest.py
# not root: it needs to write nothing (trivy DS-0002); the TLS override mounts its key read-only
USER 10001
CMD ["python", "-u", "/app/ingest.py"]
