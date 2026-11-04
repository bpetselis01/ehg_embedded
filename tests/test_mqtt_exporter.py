"""The Mosquitto exporter (backend/mqtt_exporter.py): parsing, the fixed allowlist, the ACL that matches it exactly (the repository allows no `#` grant), and a run against a REAL broker."""

import threading
import time

from test_mqtt_tls_config import ACL, acl_stanzas, broker, client_context, needs_broker, needs_openssl  # noqa: F401  (broker is a fixture)

from backend import mqtt_exporter
from backend.mqtt_exporter import TOPICS, Exporter, parse
from tools import gen_acl


def test_parse_takes_the_leading_number_only():
    assert parse(b"123 seconds") == 123 and parse(b"4") == 4 and parse(b"-1.5") == -1.5
    assert parse(b"") is None and parse(b"Mosquitto 2.1.2") is None and parse(b"\xff\xfe") is None


def test_only_allowlisted_topics_become_metrics():
    ex = Exporter()
    assert ex.handle("$SYS/broker/clients/connected", b"3") and not ex.handle("$SYS/broker/version", b"2.1.2")
    assert not ex.handle("$SYS/broker/clients/connected", b"nope") and not ex.handle("ehg/x/pkt", b"1")
    assert ex.registry.get_sample_value("ehg_mosquitto_clients_connected") == 3.0


def test_the_acl_grants_the_exporter_exactly_the_exported_topics_and_no_wildcard():
    for acl in (acl_stanzas()["ehg-exporter"], {"read": [l.split(" ", 2)[2] for l in gen_acl.render({}).splitlines() if l.startswith("topic read $SYS")], "write": []}):
        assert sorted(acl["read"]) == sorted(TOPICS) and acl["write"] == []
        assert not any("#" in t or "+" in t for t in acl["read"])


@needs_openssl
@needs_broker
def test_the_exporter_reads_a_real_brokers_numbers(broker, monkeypatch):  # noqa: F811  (broker is the imported fixture)
    import paho.mqtt.client as mqtt
    ex = Exporter()
    got = threading.Event()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ehg-exporter")
    c.tls_set_context(client_context(broker.pki, "ehg-exporter"))
    c.on_connect = lambda cl, u, f, rc, p: (ex.up.set(1), cl.subscribe([(t, 0) for t in TOPICS]))
    c.on_message = lambda cl, u, m: (ex.handle(m.topic, m.payload), got.set())
    c.connect("localhost", broker.port)
    c.loop_start()
    assert got.wait(15), "the broker publishes $SYS every sys_interval seconds"
    time.sleep(0.5)
    c.loop_stop()
    assert ex.registry.get_sample_value("ehg_mosquitto_up") == 1.0
    assert ex.registry.get_sample_value("ehg_mosquitto_clients_connected") >= 1.0
    assert mqtt_exporter.TOPICS
