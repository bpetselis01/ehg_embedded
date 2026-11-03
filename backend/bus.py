"""NATS JetStream layout shared by the bridge, the writers and the triage worker: stream, subjects, consumers, message ids.

PURPOSE: One definition of the internal bus (decision 0012), so the services cannot disagree about subject names, duplicate suppression or redelivery limits.
INPUT: a decoded packet message (the JSON the gateway publishes, docs/interface-spec.md 4).
  e.g. {"node_id": "EHG-93C0", "seq": 40, "t_inject": 655360, ...}
OUTPUT: subject_for(msg) -> "EHG.PKT.EHG-93C0"; msg_id(msg) -> "EHG-93C0:40:655360" (the Nats-Msg-Id header); stream_config() and consumer_config(name) for nats-py.
  Subjects: EHG.PKT.<node> = packets, EHG.GW.<gateway> = gateway status (online/offline), EHG.BAD.<reason> = messages that could not be understood (kept, never dropped).
HOW: Packets are keyed node:seq:t_inject, the same key ingest uses to drop duplicates, so JetStream's duplicate window is a second layer of the same rule.
  The stream is on FILE storage with age and size limits. Consumers are durable, pull-based, explicit-ack, 30 s ack wait, at most 5 deliveries; a message that hits the
  limit raises a JetStream advisory (DEAD_LETTER_ADVISORY) which the triage worker surfaces. JetStream's duplicate-window behaviour is UNVERIFIED against a real server
  (no NATS server was available when this was written); tests/test_bridge.py checks the configuration, the fake server in the tests mimics the documented behaviour.
ROLE IN THESIS: The seam between the edge broker and the stores (docs/decisions/0012-timescaledb-nats-data-platform.md).
BEFORE -> AFTER: Before: backend/ingest/ingest.py subscribed to MQTT directly. After: the bridge puts every message on this stream; any number of consumers read it.
"""

from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, DiscardPolicy, RetentionPolicy, StorageType, StreamConfig

STREAM = "EHG"
PKT_PREFIX, GW_PREFIX, BAD_PREFIX = "EHG.PKT", "EHG.GW", "EHG.BAD"
DEAD_LETTER_ADVISORY = f"$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.{STREAM}.>"
MAX_DELIVER, ACK_WAIT_S = 5, 30
DUPLICATE_WINDOW_S = 3600  # an hour: a gateway replaying after an outage resends within this window
MAX_AGE_S, MAX_BYTES = 7 * 86400, 8 * 2**30  # a week or 8 GB of replay history; the stores hold the long-term data


def subject_for(msg: dict) -> str:
    """Subject of a packet message: EHG.PKT.<node_id>."""
    return f"{PKT_PREFIX}.{msg['node_id']}"


def msg_id(msg: dict) -> str:
    """Nats-Msg-Id of a packet message: node:seq:t_inject (t_inject disambiguates seq reuse after a node reboot)."""
    return f"{msg['node_id']}:{msg['seq']}:{msg['t_inject']}"


def stream_config() -> StreamConfig:
    """The EHG stream: file storage, limits retention, old messages discarded first, a one-hour duplicate window."""
    return StreamConfig(name=STREAM, subjects=[f"{PKT_PREFIX}.>", f"{GW_PREFIX}.>", f"{BAD_PREFIX}.>"], storage=StorageType.FILE,
                        retention=RetentionPolicy.LIMITS, discard=DiscardPolicy.OLD, max_age=MAX_AGE_S, max_bytes=MAX_BYTES,
                        duplicate_window=DUPLICATE_WINDOW_S)


def consumer_config(durable: str, subject: str = f"{PKT_PREFIX}.>") -> ConsumerConfig:
    """A durable pull consumer with explicit acks, redelivery after ACK_WAIT_S and a hard limit of MAX_DELIVER tries."""
    return ConsumerConfig(durable_name=durable, filter_subject=subject, ack_policy=AckPolicy.EXPLICIT, deliver_policy=DeliverPolicy.ALL,
                          ack_wait=ACK_WAIT_S, max_deliver=MAX_DELIVER)
