"""Prometheus exporter for the Mosquitto broker: turns its $SYS topics into metrics (NATS and the databases have exporters; the broker had none).

PURPOSE: See the broker's own numbers (clients connected, message and byte counters, dropped publishes, stored messages, uptime) in Prometheus and Grafana, and know when the exporter cannot reach it (`ehg_mosquitto_up` 0).
INPUT: the usual MQTT_HOST / MQTT_PORT / MQTT_TLS / MQTT_CA / MQTT_CERT / MQTT_KEY (backend/mqtt_env.py); the identity is `ehg-exporter`, which the ACL allows to READ `$SYS/#` and nothing else. EXPORTER_PORT (default 9203).
OUTPUT: GET /metrics with ehg_mosquitto_up and ehg_mosquitto_<name> gauges, e.g. ehg_mosquitto_clients_connected 3, ehg_mosquitto_messages_received_count 18211 (a cumulative value as the broker reports it; use rate()).
HOW: subscribes once on every (re)connect (the broker publishes $SYS every `sys_interval` seconds, retained), parses the leading number of each payload ("123 seconds" -> 123) and ignores anything else. The metric set is a fixed allowlist: a topic the broker adds later is not exported by accident.
STATUS: tested against a real Mosquitto 2.1.2 with the repository's TLS config and ACL (tests/test_mqtt_exporter.py); not run on the VM.
ROLE IN THESIS: Observability of the one component without an exporter (docs/slo.md).
BEFORE -> AFTER: Before: the broker was visible only as "container up". After: its load and loss are metrics.
"""

import os
import re

import paho.mqtt.client as mqtt
from prometheus_client import CollectorRegistry, Gauge, start_http_server

from backend.mqtt_env import mqtt_settings

TOPICS = {
    "$SYS/broker/clients/connected": "clients_connected",
    "$SYS/broker/clients/total": "clients_total",
    "$SYS/broker/messages/received": "messages_received_count",
    "$SYS/broker/messages/sent": "messages_sent_count",
    "$SYS/broker/bytes/received": "bytes_received_count",
    "$SYS/broker/bytes/sent": "bytes_sent_count",
    "$SYS/broker/publish/messages/dropped": "publish_dropped_count",
    "$SYS/broker/store/messages/count": "stored_messages",
    "$SYS/broker/uptime": "uptime_seconds",
}


def parse(payload: bytes):
    """The leading number of a $SYS payload, or None."""
    m = re.match(rb"\s*(-?\d+(?:\.\d+)?)", payload[:64])
    return float(m.group(1)) if m else None


class Exporter:
    def __init__(self, registry=None):
        self.registry = registry or CollectorRegistry()
        self.up = Gauge("ehg_mosquitto_up", "1 while the exporter is connected to the broker", registry=self.registry)
        self.gauges = {t: Gauge(f"ehg_mosquitto_{name}", f"Mosquitto {t}", registry=self.registry) for t, name in TOPICS.items()}

    def handle(self, topic: str, payload: bytes) -> bool:
        g, v = self.gauges.get(topic), parse(payload)
        if g is None or v is None:
            return False
        g.set(v)
        return True


def main() -> None:
    host, port, ctx = mqtt_settings()
    ex = Exporter()
    start_http_server(int(os.environ.get("EXPORTER_PORT", 9203)), registry=ex.registry)
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=os.environ.get("MQTT_CLIENT_ID", "ehg-exporter"))
    if ctx:
        c.tls_set_context(ctx)

    def on_connect(cl, u, f, rc, p):
        ex.up.set(0 if rc.is_failure else 1)
        if not rc.is_failure:
            cl.subscribe([(t, 0) for t in TOPICS])

    c.on_connect = on_connect
    c.on_disconnect = lambda *a: ex.up.set(0)
    c.on_message = lambda cl, u, m: ex.handle(m.topic, m.payload)
    c.reconnect_delay_set(1, 30)
    ex.up.set(0)
    c.connect_async(host, port)
    print("mqtt exporter running")
    c.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
