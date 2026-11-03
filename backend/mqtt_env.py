"""MQTT connection settings from environment variables, shared by the bridge and the triage worker.

PURPOSE: The same MQTT_* variables as backend/ingest/ingest.py and viewer/server.py, in one importable place for services that run with the repository root as build context.
INPUT: MQTT_HOST, MQTT_PORT, MQTT_TLS (1/true/yes), MQTT_CA, MQTT_CERT, MQTT_KEY.
  e.g. MQTT_HOST=mosquitto MQTT_TLS=1 MQTT_CA=/pki/ca-chain.crt MQTT_CERT=/pki/ehg-bridge.crt MQTT_KEY=/pki/ehg-bridge.key
OUTPUT: mqtt_settings(env) -> (host, port, SSLContext or None). Defaults: localhost:1883, no TLS.
HOW: Half a TLS setup raises SystemExit instead of falling back to plain MQTT; the context requires TLS 1.3 and checks the broker name.
ROLE IN THESIS: The client side of docs/decisions/0008-mqtt-mtls-and-topic-acls.md for the bridge.
BEFORE -> AFTER: Before: ingest and the viewer each carry a copy (they run alone, outside this package). After: services built from the repo root import this one.
"""

import os
import ssl


def mqtt_settings(env=os.environ):
    """(host, port, TLS context or None); refuses a half-set TLS configuration."""
    host = env.get("MQTT_HOST", "localhost")
    files = [env.get(k) for k in ("MQTT_CA", "MQTT_CERT", "MQTT_KEY")]
    if env.get("MQTT_TLS", "").lower() not in ("1", "true", "yes"):
        if any(files):
            raise SystemExit("MQTT_CA/MQTT_CERT/MQTT_KEY are set but MQTT_TLS is not: refusing to fall back to plain MQTT")
        return host, int(env.get("MQTT_PORT", 1883)), None
    if not all(files):
        raise SystemExit("MQTT_TLS is on, so MQTT_CA, MQTT_CERT and MQTT_KEY must all be set")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_verify_locations(cafile=files[0])
    ctx.load_cert_chain(certfile=files[1], keyfile=files[2])
    return host, int(env.get("MQTT_PORT", 8883)), ctx
