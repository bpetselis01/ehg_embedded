"""Opt-in check against a REAL HAPI FHIR R5 server (needs Docker; set EHG_INTEGRATION=1). Never run in the default suite and NEVER run so far (Docker was not available when it was written).
It starts hapiproject/hapi:v7.6.0 with the in-memory H2 default database on a free loopback port, so it needs neither PostgreSQL nor our application.yaml, and proves two things the fake cannot:
the server accepts our transaction (conditional create + conditional update, SampledData with "E" values), and sending the same transaction twice leaves ONE Device and ONE Observation."""

import os
import shutil
import socket
import subprocess
import time

import httpx
import numpy as np
import pytest
from datetime import datetime, timezone

from backend.fhir_sync.worker import FhirClient, transaction
from tools.fhir_export import bundle

def docker_daemon_is_up() -> bool:
    """True only if a Docker daemon answers. This never starts one (Docker Desktop on this Mac also runs another project)."""
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except subprocess.TimeoutExpired:
        return False


pytestmark = pytest.mark.skipif(os.environ.get("EHG_INTEGRATION") != "1" or not docker_daemon_is_up(), reason="needs EHG_INTEGRATION=1 and a running Docker daemon")

SRC = {"fs": np.float64(20.0), "gain": np.array([13107.0]), "baseline": np.array([0]), "channels": np.array(["1"])}


def test_hapi_accepts_the_transaction_and_it_is_idempotent():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    name = f"ehg-hapi-test-{port}"
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:8080", "-e", "hapi.fhir.fhir_version=R5", "hapiproject/hapi:v7.6.0"], check=True, capture_output=True)
    try:
        base = f"http://127.0.0.1:{port}/fhir"
        for _ in range(120):  # HAPI takes a minute or two to start
            try:
                if httpx.get(base + "/metadata", timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        else:
            pytest.fail("HAPI did not start in four minutes")
        client = FhirClient(base)
        tx = transaction(bundle({0: [1093], 1: [1145], 3: [1078]}, SRC, "EHG-T", "rec", 0, datetime(2026, 11, 3, tzinfo=timezone.utc)), "EHG-T", "EHG-T/0/0")
        client.send(tx)
        client.send(tx)
        devices = httpx.get(base + "/Device", params={"identifier": "urn:thesisb:ehg/node-id|EHG-T"}).json()
        observations = httpx.get(base + "/Observation", params={"identifier": "urn:thesisb:ehg/obs-key|EHG-T/0/0"}).json()
        assert devices["total"] == 1 and observations["total"] == 1
        data = observations["entry"][0]["resource"]["component"][0]["valueSampledData"]["data"]
        assert data == "1093 1145 E 1078"
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True)
