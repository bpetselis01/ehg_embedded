"""Checks the local data-platform compose override (Unit 6): behind a profile, so the default stack is exactly the original 4 containers; loopback-only ports that avoid Supabase's 5432;
pinned images; an unprivileged image that contains no secrets; and the Influx writer's JetStream mode."""

import asyncio
import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
BASE, PLATFORM, DOCKERFILE = ROOT / "backend/docker-compose.yml", ROOT / "backend/docker-compose.platform.yml", ROOT / "backend/platform.Dockerfile"
NEW = {"nats", "timescaledb", "migrate", "bridge", "ts-writer", "influx-writer"}
ENV = {k: "x" for k in ("INFLUX_ORG", "INFLUX_BUCKET", "INFLUX_TOKEN", "INFLUX_ADMIN_PASSWORD", "GRAFANA_ADMIN_PASSWORD")}


def services():
    return yaml.safe_load(PLATFORM.read_text())["services"]


def test_every_new_service_is_behind_the_platform_profile():
    assert set(services()) == NEW
    for name, svc in services().items():
        assert svc["profiles"] == ["platform"], f"{name} must not start with the default stack"


def test_default_stack_is_still_exactly_the_original_four():
    assert set(yaml.safe_load(BASE.read_text())["services"]) == {"mosquitto", "influxdb", "ingest", "grafana"}
    assert "platform" not in BASE.read_text()


@pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0, reason="docker compose CLI not installed")
def test_compose_config_shows_four_services_without_the_profile_and_ten_with_it():
    def names(*extra):
        r = subprocess.run(["docker", "compose", *extra, "-f", "docker-compose.yml", "-f", "docker-compose.platform.yml", "config", "--services"],
                           cwd=ROOT / "backend", capture_output=True, text=True, env={**os.environ, **ENV})
        assert r.returncode == 0, r.stderr
        return set(r.stdout.split())
    assert names() == {"mosquitto", "influxdb", "ingest", "grafana"}
    assert names("--profile", "platform") == {"mosquitto", "influxdb", "ingest", "grafana"} | NEW


def test_ports_are_loopback_only_and_postgres_avoids_the_supabase_port():
    for name, svc in services().items():
        for p in svc.get("ports", []):
            assert str(p).startswith("127.0.0.1:"), f"{name}: {p} must be bound to loopback"
    assert services()["timescaledb"]["ports"] == ["127.0.0.1:5433:5432"]


def test_third_party_images_are_pinned_by_digest():
    for name in ("nats", "timescaledb"):
        assert re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", services()[name]["image"]), name


def test_platform_image_is_unprivileged_and_carries_no_secrets():
    text = DOCKERFILE.read_text()
    assert "USER 10001" in text and "FROM python:3.13-slim" in text
    assert re.search(r"^COPY backend /app/backend$", text, re.M) and not re.search(r"COPY .*(\.env|data|pki|\.\.)", text)
    assert "context: .." in PLATFORM.read_text() and "dockerfile: backend/platform.Dockerfile" in PLATFORM.read_text()


def load_ingest(monkeypatch):
    for k, v in (("INFLUX_BUCKET", "b"), ("INFLUX_URL", "http://localhost:1"), ("INFLUX_TOKEN", "t"), ("INFLUX_ORG", "o")):
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("ingest_js", ROOT / "backend/ingest/ingest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ingest_reads_jetstream_only_when_asked_and_keeps_the_mqtt_default(monkeypatch):
    ing = load_ingest(monkeypatch)
    calls = []
    monkeypatch.setattr(asyncio, "run", lambda coro: (calls.append("jetstream"), coro.close()))
    monkeypatch.setenv("INGEST_SOURCE", "jetstream")
    ing.main()
    assert calls == ["jetstream"]
    assert ing.handle and ing.on_message, "the MQTT callback still exists and shares handle()"


def test_ingest_dockerfile_has_the_nats_client():
    assert "nats-py" in (ROOT / "backend/ingest/Dockerfile").read_text()
