"""Checks the MQTT -> JetStream bridge (decision 0012): MQTT is acknowledged only after the stream has the message, a crash between the two redelivers and never drops,
JetStream's duplicate window turns the redelivery into a no-op, unreadable messages are kept (not lost, not looped), and the stream/consumer configuration is what the design says."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend import bus
from backend.bridge.bridge import Bridge
from backend.mqtt_env import mqtt_settings


class FakeJetStream:
    """Mimics the documented behaviour: a message with a Nats-Msg-Id already seen inside the duplicate window is acknowledged but not stored again."""

    def __init__(self):
        self.stored, self.ids, self.fail = [], set(), False

    async def publish(self, subject, payload, headers):
        if self.fail:
            raise TimeoutError("no response from the stream")
        mid = headers.get("Nats-Msg-Id")
        if mid and mid in self.ids:
            return SimpleNamespace(duplicate=True)
        if mid:
            self.ids.add(mid)
        self.stored.append((subject, payload, headers))
        return SimpleNamespace(duplicate=False)


def packet(seq=40, node="EHG-93C0", t_inject=655360):
    return json.dumps({"node_id": node, "seq": seq, "t_inject": t_inject, "samples": [[1, 2, 3]]}).encode()


def deliver(bridge, topic, payload, acks):
    """One broker delivery: acks gets a mark only if the bridge calls ack()."""
    asyncio.run(bridge.handle(topic, payload, lambda: acks.append(topic)))


def test_ack_only_after_the_stream_has_it():
    js, acks = FakeJetStream(), []
    order = []
    orig = js.publish

    async def spy(*a):
        r = await orig(*a)
        order.append("stored")
        return r
    js.publish = spy
    b = Bridge(js.publish)
    asyncio.run(b.handle("ehg/EHG-93C0/pkt", packet(), lambda: order.append("acked")))
    assert order == ["stored", "acked"]
    subject, _, headers = js.stored[0]
    assert subject == "EHG.PKT.EHG-93C0" and headers == {"Nats-Msg-Id": "EHG-93C0:40:655360"}


def test_stream_failure_means_no_ack_so_the_broker_redelivers():
    js, acks = FakeJetStream(), []
    js.fail = True
    b = Bridge(js.publish)
    with pytest.raises(TimeoutError):
        deliver(b, "ehg/EHG-93C0/pkt", packet(), acks)
    assert acks == [] and js.stored == [] and b.counters["errors"] == 1
    js.fail = False  # the broker redelivers; now it works
    deliver(b, "ehg/EHG-93C0/pkt", packet(), acks)
    assert len(acks) == 1 and len(js.stored) == 1


def test_crash_between_publish_and_ack_redelivers_without_storing_twice():
    js, acks = FakeJetStream(), []
    b = Bridge(js.publish)

    def crashing_ack():
        raise ConnectionError("process died before the MQTT ack")
    with pytest.raises(ConnectionError):
        asyncio.run(b.handle("ehg/EHG-93C0/pkt", packet(), crashing_ack))
    assert len(js.stored) == 1  # it IS in the stream, so nothing is lost
    deliver(b, "ehg/EHG-93C0/pkt", packet(), acks)  # the broker redelivers the same message
    assert len(js.stored) == 1 and len(acks) == 1 and b.counters["duplicates"] == 1, "same Nats-Msg-Id: stored once, now acknowledged"


def test_same_seq_after_a_node_reboot_is_a_different_message():
    js, acks = FakeJetStream(), []
    b = Bridge(js.publish)
    deliver(b, "ehg/EHG-93C0/pkt", packet(seq=0, t_inject=100), acks)
    deliver(b, "ehg/EHG-93C0/pkt", packet(seq=0, t_inject=999), acks)
    assert len(js.stored) == 2 and b.counters["duplicates"] == 0


@pytest.mark.parametrize("payload, subject", [
    (b"not json at all", "EHG.BAD.not_json"),
    (json.dumps({"seq": 1}).encode(), "EHG.BAD.missing_fields"),
    (json.dumps([1, 2, 3]).encode(), "EHG.BAD.missing_fields"),
])
def test_unreadable_packets_are_kept_under_bad_and_acknowledged(payload, subject):
    js, acks = FakeJetStream(), []
    b = Bridge(js.publish)
    deliver(b, "ehg/EHG-93C0/pkt", payload, acks)
    assert js.stored[0][0] == subject and js.stored[0][1] == payload, "the original bytes are preserved for inspection"
    assert len(acks) == 1, "acknowledged, otherwise the broker would redeliver a message that can never succeed"
    assert b.counters["bad"] == 1 and b.counters["published"] == 0


def test_gateway_status_and_unexpected_topics_are_routed():
    js, acks = FakeJetStream(), []
    b = Bridge(js.publish)
    deliver(b, "ehg/_gw/ehg-gw-01/status", b"online", acks)
    deliver(b, "other/topic", b"x", acks)
    assert [s for s, _, _ in js.stored] == ["EHG.GW.ehg-gw-01", "EHG.BAD.unexpected_topic"] and len(acks) == 2


def test_metrics_mirror_the_counters():
    inc = []
    metrics = {k: SimpleNamespace(inc=lambda k=k: inc.append(k)) for k in ("rx", "published", "duplicates", "bad", "errors")}
    js, acks = FakeJetStream(), []
    b = Bridge(js.publish, metrics=metrics)
    deliver(b, "ehg/EHG-93C0/pkt", packet(), acks)
    deliver(b, "ehg/EHG-93C0/pkt", packet(), acks)
    assert inc == ["rx", "published", "rx", "duplicates"]


def test_stream_and_consumer_configuration_follow_the_design():
    s = bus.stream_config()
    assert s.name == "EHG" and set(s.subjects) == {"EHG.>"}
    assert s.storage.value == "file" and s.retention.value == "limits" and s.discard.value == "old"
    assert s.duplicate_window == 3600 and s.max_age == 7 * 86400 and s.max_bytes == 8 * 2**30
    c = bus.consumer_config("ts-writer")
    assert (c.durable_name, c.filter_subject, c.ack_policy.value, c.max_deliver, c.ack_wait) == ("ts-writer", "EHG.PKT.>", "explicit", 5, 30)
    assert bus.DEAD_LETTER_ADVISORY == "$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.EHG.>"


def test_mqtt_env_defaults_and_refusal_of_half_tls():
    assert mqtt_settings({}) == ("localhost", 1883, None)
    for env in ({"MQTT_TLS": "1"}, {"MQTT_CA": "x"}, {"MQTT_TLS": "1", "MQTT_CA": "a", "MQTT_CERT": "b"}):
        with pytest.raises(SystemExit):
            mqtt_settings(env)


# ---------------------------------------------------------------- subject injection and field checks (automated security review, 2026-11-03)

@pytest.mark.parametrize("node", ["a.b", "EHG-1 x", ">", "*", "EHG-1\n", "x" * 65, "", "EHG.PKT.EHG-1", "ünï"])
def test_a_node_id_that_could_change_the_subject_is_refused_and_kept_under_bad(node):
    js, acks = FakeJetStream(), []
    b = Bridge(js.publish)
    deliver(b, "ehg/EHG-1/pkt", json.dumps({"node_id": node, "seq": 1, "t_inject": 2}).encode(), acks)
    assert [s for s, _, _ in js.stored] == ["EHG.BAD.invalid_node"] and len(acks) == 1 and b.counters["bad"] == 1


def test_the_node_in_the_payload_must_match_the_node_in_the_topic():
    js, acks = FakeJetStream(), []
    b = Bridge(js.publish)
    deliver(b, "ehg/EHG-1/pkt", packet(node="EHG-2"), acks)
    assert [s for s, _, _ in js.stored] == ["EHG.BAD.node_mismatch"] and len(acks) == 1


@pytest.mark.parametrize("seq, t_inject", [("40", 1), (1.5, 1), (True, 1), (None, 1), (1, "9"), (1, 2.5), (2**40, 1), (-1, 1), (1, -5), (1, 2**33)])
def test_sequence_and_tick_fields_must_be_integers_in_range(seq, t_inject):
    js, acks = FakeJetStream(), []
    b = Bridge(js.publish)
    deliver(b, "ehg/EHG-1/pkt", json.dumps({"node_id": "EHG-1", "seq": seq, "t_inject": t_inject}).encode(), acks)
    assert [s for s, _, _ in js.stored] == ["EHG.BAD.invalid_field"] and len(acks) == 1


@pytest.mark.parametrize("gateway", ["gw.1", "gw 1", ">", "*", "g" * 65])
def test_a_gateway_id_that_could_change_the_subject_is_refused(gateway):
    js, acks = FakeJetStream(), []
    b = Bridge(js.publish)
    deliver(b, f"ehg/_gw/{gateway}/status", b"online", acks)
    assert [s for s, _, _ in js.stored] == ["EHG.BAD.invalid_gateway"] and len(acks) == 1


def test_valid_ids_with_dashes_and_underscores_still_route_normally():
    js, acks = FakeJetStream(), []
    b = Bridge(js.publish)
    deliver(b, "ehg/SIM-0000/pkt", packet(node="SIM-0000"), acks)
    deliver(b, "ehg/_gw/ehg-gw-01/status", b"online", acks)
    assert [s for s, _, _ in js.stored] == ["EHG.PKT.SIM-0000", "EHG.GW.ehg-gw-01"]


def subject_is_subset(filter_: str, stream_subject: str) -> bool:
    """NATS's rule for a consumer filter: every subject the filter can match must also be matched by the stream subject (`*` is one token, `>` is the rest)."""
    f, t = filter_.split("."), stream_subject.split(".")
    for i, tok in enumerate(t):
        if tok == ">":
            return len(f) > i  # the stream subject takes everything from here on, so any filter that is at least this long is inside it
        if i >= len(f) or f[i] == ">" or (f[i] == "*" and tok != "*") or (tok != "*" and f[i] != tok):
            return False
    return len(f) == len(t)


def test_the_subset_rule_itself():
    assert subject_is_subset("EHG.PKT.>", "EHG.>") and subject_is_subset("EHG.PKT.node1", "EHG.PKT.>") and subject_is_subset("EHG.>", "EHG.>")
    assert not subject_is_subset("EHG.>", "EHG.PKT.>") and not subject_is_subset("EHG.PKT.>", "EHG.GW.>") and not subject_is_subset("OTHER.>", "EHG.>")


def test_every_consumer_filter_in_the_code_is_inside_the_streams_subjects():
    """JetStream refuses to create a consumer whose filter is not a subset of the stream's subjects (found by reading, never run against a server: the triage consumer filtered on EHG.> while the stream held only the three sub-trees)."""
    import re
    from pathlib import Path
    root = Path(__file__).parents[1]
    filters = {"ts-writer": f"{bus.PKT_PREFIX}.>", "triage": "EHG.>"}
    for path in ("backend/ts_writer/writer.py", "backend/triage/stream.py", "backend/ingest/ingest.py"):
        for found in re.findall(r"pull_subscribe\(\s*f?\"([^\"]+)\"", (root / path).read_text()):
            filters[path] = found.replace("{bus.PKT_PREFIX}", bus.PKT_PREFIX)
    assert len(filters) >= 4, filters
    subjects = bus.stream_config().subjects
    for who, f in filters.items():
        assert any(subject_is_subset(f, s) for s in subjects), f"{who}: filter {f!r} is not inside the stream subjects {subjects}"

