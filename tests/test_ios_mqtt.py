"""The iPhone gateway's MQTT client against a REAL Mosquitto (the repository's TLS config and ACL): TLS 1.3 with a client certificate, QoS 1 through the outbox, replay of messages written before the link existed,
the ACL still applying, and the last will firing on an unclean drop. The Swift side is ios/EHGWire/Tests/EHGGatewayCoreTests/MQTTIntegrationTests.swift; this file starts the broker, builds the PKCS#12 identity, runs `swift test`
and then checks what the broker actually delivered. Needs EHG_INTEGRATION=1, mosquitto, OpenSSL 3 and the Swift toolchain (command line)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_mqtt_tls_config import broker, collect, needs_broker, needs_openssl  # noqa: F401  (broker is a fixture)

ROOT = Path(__file__).parents[1]
needs_swift = pytest.mark.skipif(shutil.which("swift") is None, reason="Swift toolchain not installed")


@needs_openssl
@needs_broker
@needs_swift
def test_outbox_to_a_real_broker_over_tls13_with_a_client_certificate(broker, tmp_path):
    pki = broker.pki
    p12 = tmp_path / "gw.p12"
    subprocess.run(["openssl", "pkcs12", "-export", "-inkey", str(pki / "clients/ehg-gw-01.key"), "-in", str(pki / "clients/ehg-gw-01.crt"), "-certfile", str(pki / "ca-chain.crt"),
                    "-keypbe", "PBE-SHA1-3DES", "-certpbe", "PBE-SHA1-3DES", "-macalg", "sha1", "-out", str(p12), "-passout", "pass:test"], check=True, capture_output=True)
    env = {**os.environ, "EHG_MQTT_PORT": str(broker.port), "EHG_MQTT_CA": str(pki / "ca-chain.crt"), "EHG_MQTT_P12": str(p12), "EHG_MQTT_ID": "ehg-gw-01"}
    result = {}

    def run_swift():
        r = subprocess.run(["swift", "test", "--filter", "MQTTIntegrationTests"], cwd=ROOT / "ios/EHGWire", env=env, capture_output=True, text=True, timeout=600)
        result["r"] = r

    got = collect(broker, "ehg-bridge", ["ehg/+/pkt", "ehg/_gw/+/status"], 3, run_swift)
    r = result["r"]
    assert r.returncode == 0 and "Executed 1 test" in r.stdout + r.stderr, (r.stdout + r.stderr)[-1500:]
    pkts = [json.loads(p) for t, p in got if t == "ehg/EHG-IT/pkt"]
    assert sorted({m["seq"] for m in pkts}) == [0, 1, 2, 3, 4], "all five arrived, including the two written to the outbox BEFORE the link existed"
    replayed = {m["seq"] for m in pkts if m.get("replayed")}
    assert {0, 1} <= replayed, "the messages that waited on disk are marked replayed"
    assert all("t_mqtt_pub" in m for m in pkts)
    status = [p.decode() for t, p in got if t == "ehg/_gw/ehg-gw-01/status"]
    assert status[:1] == ["online"] and status[-1:] == ["offline"], f"online on connect, then the last will after the unclean drop: {status}"
    assert not any(t == "ehg/_gw/someone-else/status" for t, _ in got), "the ACL still drops another gateway's status topic"
