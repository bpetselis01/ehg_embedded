"""NATS JetStream layout shared by the bridge, the writers and the triage worker: stream, subjects, consumers, message ids.

PURPOSE: One definition of the internal bus (decision 0012), so the services cannot disagree about subject names, duplicate suppression or redelivery limits.
INPUT: a decoded packet message (the JSON the gateway publishes, docs/interface-spec.md 4).
  e.g. {"node_id": "EHG-93C0", "seq": 40, "t_inject": 655360, ...}
OUTPUT: valid_id(x) / valid_int(x, lo, hi) (input checks shared with the bridge and the writer: a message that fails them is never turned into a subject); subject_for(msg) -> "EHG.PKT.EHG-93C0"; msg_id(msg) -> "EHG-93C0:40:655360" (the Nats-Msg-Id header); stream_config() and consumer_config(name) for nats-py.
  Subjects: EHG.PKT.<node> = packets, EHG.GW.<gateway> = gateway status (online/offline), EHG.BAD.<reason> = messages that could not be understood (kept, never dropped).
HOW: Packets are keyed node:seq:t_inject, the same key ingest uses to drop duplicates, so JetStream's duplicate window is a second layer of the same rule.
  The stream is on FILE storage with age and size limits. Consumers are durable, pull-based, explicit-ack, 30 s ack wait, at most 5 deliveries; a message that hits the
  limit raises a JetStream advisory (DEAD_LETTER_ADVISORY) which the triage worker surfaces. JetStream's duplicate-window behaviour is UNVERIFIED against a real server
  (no NATS server was available when this was written); tests/test_bridge.py checks the configuration, the fake server in the tests mimics the documented behaviour.
ROLE IN THESIS: The seam between the edge broker and the stores (docs/decisions/0012-timescaledb-nats-data-platform.md).
BEFORE -> AFTER: Before: backend/ingest/ingest.py subscribed to MQTT directly. After: the bridge puts every message on this stream; any number of consumers read it.
"""

import re

from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, DiscardPolicy, RetentionPolicy, StorageType, StreamConfig

STREAM = "EHG"
PKT_PREFIX, GW_PREFIX, BAD_PREFIX = "EHG.PKT", "EHG.GW", "EHG.BAD"
DEAD_LETTER_ADVISORY = f"$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.{STREAM}.>"
TERMINATED_ADVISORY = f"$JS.EVENT.ADVISORY.CONSUMER.MSG_TERMINATED.{STREAM}.>"  # published when a consumer term()s a message (the writers do, for anything unparseable)
MAX_DELIVER, ACK_WAIT_S = 5, 30
DUPLICATE_WINDOW_S = 3600  # an hour: a gateway replaying after an outage resends within this window
MAX_AGE_S, MAX_BYTES = 7 * 86400, 8 * 2**30  # a week or 8 GB of replay history; the stores hold the long-term data


ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")  # one NATS subject token: no dots, wildcards, spaces or control characters


def valid_id(value) -> bool:
    """True for a node or gateway id that is safe to put in a NATS subject (and in a database column)."""
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def valid_int(value, lo: int, hi: int) -> bool:
    """True for a real integer (not a bool, not a float, not a string) within lo..hi."""
    return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi


def subject_for(msg: dict) -> str:
    """Subject of a packet message: EHG.PKT.<node_id>."""
    return f"{PKT_PREFIX}.{msg['node_id']}"


def msg_id(msg: dict) -> str:
    """Nats-Msg-Id of a packet message: node:seq:t_inject (t_inject disambiguates seq reuse after a node reboot)."""
    return f"{msg['node_id']}:{msg['seq']}:{msg['t_inject']}"


def stream_config() -> StreamConfig:
    """The EHG stream: file storage, limits retention, old messages discarded first, a one-hour duplicate window.

    One subject, `EHG.>`, that covers EHG.PKT.*, EHG.GW.* and EHG.BAD.*: a JetStream consumer's filter must lie INSIDE the stream's subjects, and the triage consumer watches everything (`EHG.>`),
    which was NOT inside the three separate sub-trees this stream used to list."""
    return StreamConfig(name=STREAM, subjects=[f"{STREAM}.>"], storage=StorageType.FILE,
                        retention=RetentionPolicy.LIMITS, discard=DiscardPolicy.OLD, max_age=MAX_AGE_S, max_bytes=MAX_BYTES,
                        duplicate_window=DUPLICATE_WINDOW_S)


UNLIMITED = -1


def consumer_config(durable: str, subject: str = f"{PKT_PREFIX}.>", max_deliver: int = MAX_DELIVER) -> ConsumerConfig:
    """A durable pull consumer with explicit acks and redelivery after ACK_WAIT_S. `max_deliver` is the hard limit of tries (MAX_DELIVER by default).

    The STORE writers pass UNLIMITED: while their database is down they nak, and every nak counts as a delivery, so after five tries (about 30 s of outage) JetStream would stop offering
    the message to that consumer for good (it stays in the stream, unseen: found by an independent review). A message that can never be stored is term()-ed by the writer itself, so nothing loops forever."""
    return ConsumerConfig(durable_name=durable, filter_subject=subject, ack_policy=AckPolicy.EXPLICIT, deliver_policy=DeliverPolicy.ALL,
                          ack_wait=ACK_WAIT_S, max_deliver=max_deliver)


async def fetch_batch(sub, batch: int, timeout: float = 1) -> list:
    """One pull from a durable consumer; an EMPTY list when nothing arrived in `timeout` seconds.

    nats-py's fetch() raises the BUILT-IN TimeoutError when the stream is idle, which is NOT caught by `except nats.errors.TimeoutError` (that class is a
    subclass of it, so the relation points the wrong way). The three consumers did exactly that and crashed on an idle stream; first seen on the first start of the
    cloud compose file (2026-11-04), invisible in tests that raised nats.errors.TimeoutError themselves. Catching the built-in class covers both."""
    try:
        return await sub.fetch(batch, timeout=timeout)
    except TimeoutError:
        return []

