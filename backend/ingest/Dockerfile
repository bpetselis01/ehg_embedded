FROM python:3.13-slim
RUN pip install --no-cache-dir "paho-mqtt>=2.1,<3" "influxdb-client>=1.46,<2"
COPY ingest.py /app/ingest.py
CMD ["python", "-u", "/app/ingest.py"]
