"""MQTT connection settings from environment variables, shared by the bridge and the triage worker."""

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
