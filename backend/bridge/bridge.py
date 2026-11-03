"""MQTT -> NATS JetStream bridge: puts every broker message on the internal bus, and tells the broker "got it" only after JetStream has it.

PURPOSE: Decouple the edge broker from the stores. Mosquitto keeps the message until this service confirms it is safely in the stream, so a crash anywhere in between
  redelivers instead of losing (docs/decisions/0012-timescaledb-nats-data-platform.md).
INPUT: MQTT topics ehg/+/pkt (QoS 1, persistent session, manual acknowledgement) and ehg/_gw/+/status; MQTT_* and NATS_URL environment variables.
  e.g. topic ehg/EHG-93C0/pkt, payload {"node_id": "EHG-93C0", "seq": 40, "t_inject": 655360, ...}
OUTPUT: JetStream messages: EHG.PKT.EHG-93C0 with header Nats-Msg-Id: EHG-93C0:40:655360; EHG.GW.<gateway> for status; EHG.BAD.<reason> for anything unreadable
  (stored, not dropped). A /metrics page (Prometheus text) on BRIDGE_METRICS_PORT (default 9201) with counters rx, published, duplicates, bad, errors.
  e.g. counters {"rx": 3, "published": 2, "duplicates": 1, "bad": 0, "errors": 0} after one message arrived twice.
HOW: handle() publishes to JetStream, waits for the PubAck, and only then calls ack(). If publishing fails, ack() is not called, the exception is counted and re-raised, so the
  MQTT client never acknowledges and the broker redelivers. If the process dies between the PubAck and ack(), the redelivery carries the same Nats-Msg-Id and JetStream drops it
  as a duplicate: at-least-once in, exactly-once stored. A message that is not valid JSON, or lacks node_id/seq/t_inject, goes to EHG.BAD.* and IS acknowledged, otherwise it would
  be redelivered forever.
ROLE IN THESIS: The new first stage of the cloud data path (docs/production-topology.md).
BEFORE -> AFTER: Before: ingest.py read MQTT and wrote InfluxDB directly. After: this reads MQTT; the writers read the stream.
"""

import asyncio
import json
import os

from backend import bus
from backend.mqtt_env import mqtt_settings

REQUIRED = ("node_id", "seq", "t_inject")


class Bridge:
    """The ack-after-publish rule, free of MQTT and NATS so it can be tested: `publish(subject, payload, headers)` returns an object with `.duplicate`."""

    def __init__(self, publish, metrics=None):
        self._publish, self._metrics = publish, metrics or {}
        self.counters = {"rx": 0, "published": 0, "duplicates": 0, "bad": 0, "errors": 0}

    def _count(self, name: str) -> None:
        """Add one to a counter (and to its Prometheus twin when metrics are wired)."""
        self.counters[name] += 1
        if name in self._metrics:
            self._metrics[name].inc()

    async def handle(self, topic: str, payload: bytes, ack) -> None:
        """Put one broker message on the stream, then (and only then) call ack(). Any failure raises and leaves ack() uncalled."""
        self._count("rx")
        try:
            subject, headers = self._route(topic, payload)
            result = await self._publish(subject, payload, headers)
        except Exception:
            self._count("errors")
            raise
        if subject.startswith(bus.BAD_PREFIX):
            self._count("bad")
        elif getattr(result, "duplicate", False):
            self._count("duplicates")
        else:
            self._count("published")
        ack()

    def _route(self, topic: str, payload: bytes) -> tuple[str, dict]:
        """Subject and headers for a topic/payload; unreadable packets go to EHG.BAD.<reason> instead of being dropped."""
        parts = topic.split("/")
        if len(parts) == 4 and parts[:2] == ["ehg", "_gw"] and parts[3] == "status":
            return f"{bus.GW_PREFIX}.{parts[2]}", {}
        if len(parts) == 3 and parts[0] == "ehg" and parts[2] == "pkt":
            try:
                msg = json.loads(payload)
                if not isinstance(msg, dict) or any(k not in msg for k in REQUIRED):
                    return f"{bus.BAD_PREFIX}.missing_fields", {}
            except ValueError:
                return f"{bus.BAD_PREFIX}.not_json", {}
            return bus.subject_for(msg), {"Nats-Msg-Id": bus.msg_id(msg)}
        return f"{bus.BAD_PREFIX}.unexpected_topic", {}


async def run() -> None:
    """Connect MQTT (manual ack, persistent session) and NATS, then bridge until stopped."""
    import nats
    import paho.mqtt.client as mqtt
    from prometheus_client import Counter, start_http_server

    nc = await nats.connect(os.environ.get("NATS_URL", "nats://nats:4222"), name="ehg-bridge", max_reconnect_attempts=-1)
    js = nc.jetstream()
    await js.add_stream(bus.stream_config())  # idempotent: succeeds unchanged if it already exists with this configuration
    start_http_server(int(os.environ.get("BRIDGE_METRICS_PORT", "9201")))
    counters = {k: Counter(f"ehg_bridge_{k}", f"bridge counter {k}") for k in ("rx", "published", "duplicates", "bad", "errors")}

    async def publish(subject, payload, headers):
        """Publish to JetStream and wait for the stream's acknowledgement."""
        return await js.publish(subject, payload, headers=headers, timeout=10)

    bridge = Bridge(publish, metrics=counters)
    loop = asyncio.get_running_loop()
    host, port, tls = mqtt_settings()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ehg-bridge", clean_session=False, manual_ack=True)
    if tls:
        c.tls_set_context(tls)
    c.on_connect = lambda cl, u, f, rc, p: (print(f"bridge: MQTT connected ({rc})"), cl.subscribe([("ehg/+/pkt", 1), ("ehg/_gw/+/status", 1)]))
    c.on_connect_fail = lambda cl, u: print("bridge: cannot reach the MQTT broker (TLS handshake or network); retrying")

    def on_message(client, userdata, msg):
        """Called in paho's thread: hand the message to the event loop; acknowledge MQTT only when the stream has it."""
        fut = asyncio.run_coroutine_threadsafe(bridge.handle(msg.topic, msg.payload, lambda: client.ack(msg.mid, msg.qos)), loop)

        def done(f):
            """Report a failure; the broker will redeliver because ack() was not called."""
            if f.exception():
                print(f"bridge: NOT acknowledged ({type(f.exception()).__name__}: {f.exception()}); the broker will redeliver {msg.topic}")
        fut.add_done_callback(done)

    c.on_message = on_message
    c.connect_async(host, port)
    c.loop_start()
    print("bridge running")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run())
