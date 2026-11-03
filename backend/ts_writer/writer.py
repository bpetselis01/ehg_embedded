"""NATS JetStream -> PostgreSQL/TimescaleDB writer: stores every packet exactly once, and says so when it cannot.

PURPOSE: The enterprise store's counterpart of backend/ingest/ingest.py: same rows, same timing fields, but from the durable bus and with idempotent writes
  (docs/decisions/0012-timescaledb-nats-data-platform.md). Samples are stored as raw integers; nothing is scaled or filtered (H2/H4).
INPUT: JetStream consumer `ts-writer` on EHG.PKT.> (durable, pull, explicit ack); EHG_DB_DSN (a libpq connection string); WRITER_METRICS_PORT (default 9202).
  e.g. message {"node_id": "EHG-93C0", "seq": 40, "frame_idx": 400, "t_inject": 655360, "t_inject_host": 1791243068.01, "n_ch": 3, "fs_hz": 20, "record_id": 0, "flags": 0,
       "samples": [[1093, 171, -748], ...10 frames...], "t_gw_rx": ..., "t_mqtt_pub": ...}
OUTPUT: table ehg_raw, one row per frame AND channel: (time, node, record_id, seq, frame_idx, ch, v) with time = t_inject_host + i / fs_hz, primary key (node, seq, ch, time);
  table ehg_pkt, one row per packet with the timing fields plus t_ingest_rx and t_db_ack (stamped after the raw rows are committed, like ingest.py). Writes use
  ON CONFLICT DO NOTHING, so a redelivered message changes nothing. Metrics: ehg_writer_{rx,rows,duplicate_packets,redelivered,db_errors} and ehg_writer_degraded (0/1).
  e.g. one packet of 10 frames x 3 channels = 30 ehg_raw rows + 1 ehg_pkt row.
HOW: rows_for() turns a message into rows (pure). Writer.process() writes a batch in one transaction and acknowledges the messages only after COMMIT. If PostgreSQL is
  unreachable the batch is NOT acknowledged (nak with a growing delay), the writer reports degraded=1, and NATS keeps the messages: nothing is lost while the database is down.
  A message that cannot be parsed is terminated (term) and logged; JetStream's advisory then surfaces it as a dead letter.
ROLE IN THESIS: The path to the enterprise store, proven equivalent to the InfluxDB reference by eval/parity.py before the cloud profile may rely on it (decision 0007).
BEFORE -> AFTER: Before: ingest.py wrote InfluxDB. After: this also fills TimescaleDB from the same stream; ingest.py stays as the reference writer.
"""

import asyncio
import json
import math
import os
import time
from datetime import datetime, timezone

from backend import bus

EPOCH_MIN, EPOCH_MAX = 946_684_800, 4_102_444_800  # 2000-01-01 .. 2100-01-01: anything else is a broken clock or garbage
MAX_FRAMES, MAX_CHANNELS, INT32 = 10_000, 64, 2**31 - 1


class BadData(Exception):
    """The database rejected the DATA (not the connection): retrying the same message can never succeed."""

RAW_SQL = ("INSERT INTO ehg_raw (time, node, record_id, seq, frame_idx, ch, v) VALUES (%s, %s, %s, %s, %s, %s, %s) "
           "ON CONFLICT DO NOTHING")
PKT_SQL = ("INSERT INTO ehg_pkt (time, node, seq, n_frames, retrans, replayed, t_inject_dev, t_inject_host, t_gw_rx, t_mqtt_pub, t_ingest_rx, t_db_ack) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING")


def ts(t: float) -> datetime:
    """Seconds since 1970 -> timezone-aware datetime (microsecond resolution: ehg_raw rows are 1/fs apart, at least 5 ms)."""
    return datetime.fromtimestamp(t, tz=timezone.utc)


def _epoch(v, name: str) -> float:
    """A finite time in seconds since 1970, between 2000 and 2100."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not EPOCH_MIN <= v <= EPOCH_MAX:
        raise ValueError(f"{name} must be seconds since 1970 between 2000 and 2100, got {v!r}")
    return float(v)


def validate(m: dict) -> None:
    """Raise ValueError unless every field the writer uses is present, of the right type and in range (so no later step can raise or store garbage)."""
    if not isinstance(m, dict):
        raise ValueError("message is not a JSON object")
    if not bus.valid_id(m.get("node_id")):
        raise ValueError(f"node_id {m.get('node_id')!r} is not a safe id")
    for name, lo, hi in (("seq", 0, INT32), ("frame_idx", 0, INT32 - MAX_FRAMES), ("t_inject", 0, 2**32 - 1), ("flags", 0, 255)):
        if not bus.valid_int(m.get(name), lo, hi):
            raise ValueError(f"{name} must be an integer in {lo}..{hi}, got {m.get(name)!r}")
    fs = m.get("fs_hz")
    if isinstance(fs, bool) or not isinstance(fs, (int, float)) or not math.isfinite(fs) or not 1 <= fs <= 100_000:
        raise ValueError(f"fs_hz must be a number from 1 to 100000, got {fs!r}")
    for name in ("t_inject_host", "t_gw_rx", "t_mqtt_pub"):
        _epoch(m.get(name), name)
    if not isinstance(m.get("record_id"), (str, int)) or isinstance(m.get("record_id"), bool) or len(str(m["record_id"])) > 64:
        raise ValueError("record_id must be a short string or integer")
    try:
        text = str(m["record_id"]).encode("utf-8").decode("utf-8")  # a lone surrogate cannot be encoded
    except UnicodeError:
        raise ValueError("record_id is not valid text") from None
    if "\x00" in text:
        raise ValueError("record_id contains a NUL character")
    samples = m.get("samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_FRAMES:
        raise ValueError(f"samples must be 1..{MAX_FRAMES} frames")
    width = len(samples[0]) if isinstance(samples[0], list) else 0
    if not 1 <= width <= MAX_CHANNELS:
        raise ValueError(f"each frame needs 1..{MAX_CHANNELS} channels")
    for frame in samples:
        if not isinstance(frame, list) or len(frame) != width or not all(bus.valid_int(v, -INT32 - 1, INT32) for v in frame):
            raise ValueError("every frame must be a list of the same number of integer samples (32-bit range)")


def rows_for(m: dict) -> tuple[list[tuple], dict]:
    """(ehg_raw rows, the ehg_pkt fields without t_ingest_rx/t_db_ack) for one message. Raises ValueError if the message is malformed in any way."""
    validate(m)
    node, seq, fs = m["node_id"], int(m["seq"]), float(m["fs_hz"])
    raw = [(ts(m["t_inject_host"] + i / fs), node, str(m["record_id"]), seq, int(m["frame_idx"]) + i, ch, int(v))
           for i, frame in enumerate(m["samples"]) for ch, v in enumerate(frame)]
    pkt = {"time": ts(m["t_inject_host"]), "node": node, "seq": seq, "n_frames": len(m["samples"]), "retrans": bool(int(m["flags"]) & 1),
           "replayed": bool(m.get("replayed", False)), "t_inject_dev": int(m["t_inject"]), "t_inject_host": float(m["t_inject_host"]),
           "t_gw_rx": float(m["t_gw_rx"]), "t_mqtt_pub": float(m["t_mqtt_pub"])}
    return raw, pkt


class PgStore:
    """The database side: write a batch in ONE transaction. `connect()` returns a psycopg connection; a lost connection is reopened on the next batch."""

    def __init__(self, connect):
        self._connect, self._conn = connect, None

    def write(self, batch: list[tuple[list[tuple], dict, float]]) -> tuple[int, int]:
        """Insert raw rows, stamp t_db_ack, insert packet rows, COMMIT. Returns (raw rows newly written, packet rows newly written)."""
        if self._conn is None or self._conn.closed:
            self._conn = self._connect()
        import psycopg
        try:
            with self._conn.transaction():
                cur = self._conn.cursor()
                new_raw = 0
                for raw, _, _ in batch:
                    cur.executemany(RAW_SQL, raw)
                    new_raw += cur.rowcount if cur.rowcount > 0 else 0
                t_ack = time.time()  # the samples are in the database as far as any reader of this transaction can tell
                new_pkt = 0
                for _, pkt, t_rx in batch:
                    cur.execute(PKT_SQL, (pkt["time"], pkt["node"], pkt["seq"], pkt["n_frames"], pkt["retrans"], pkt["replayed"], pkt["t_inject_dev"],
                                          pkt["t_inject_host"], pkt["t_gw_rx"], pkt["t_mqtt_pub"], t_rx, t_ack))
                    new_pkt += cur.rowcount
            return new_raw, new_pkt
        except (psycopg.errors.DataError, UnicodeError) as e:  # the transaction was rolled back; the connection is fine, the DATA is not (a text that cannot be encoded counts too)
            raise BadData(f"{type(e).__name__}: {e}") from e
        except Exception:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None
            raise


class Writer:
    """Batches messages from a consumer into the store. `store.write(batch)` may raise; messages are acknowledged only after it returns."""

    def __init__(self, store, metrics=None, log=print, backoff=(1, 2, 4, 8, 16, 30)):
        self.store, self.log, self._backoff, self._fails = store, log, backoff, 0
        self.m = metrics or {}
        self.counters = {"rx": 0, "rows": 0, "duplicate_packets": 0, "redelivered": 0, "db_errors": 0, "terminated": 0, "degraded": 0}

    def _inc(self, name: str, by: int = 1) -> None:
        """Count (and mirror to Prometheus when wired)."""
        self.counters[name] += by
        if name in self.m and name != "degraded":
            self.m[name].inc(by)

    def _degraded(self, value: int) -> None:
        """Set the degraded flag (1 while PostgreSQL is unreachable)."""
        self.counters["degraded"] = value
        if "degraded" in self.m:
            self.m["degraded"].set(value)

    async def _one_by_one(self, good, batch) -> None:
        """After a data error: store each message alone, terminate only the ones the database refuses, acknowledge the rest. A connection problem still means 'degraded'."""
        for i, (msg, item) in enumerate(zip(good, batch)):
            try:
                new_raw, new_pkt = await asyncio.to_thread(self.store.write, [item])
            except BadData as e:
                self._inc("terminated")
                self.log(f"writer: message on {msg.subject} rejected by the database ({e}); terminated (see the dead-letter advisory)")
                await msg.term()
                continue
            except Exception as e:  # noqa: BLE001
                self._inc("db_errors")
                self._degraded(1)
                self.log(f"writer: DEGRADED while retrying singly ({type(e).__name__}: {e}); {len(good) - i} messages kept in NATS")
                for rest in good[i:]:
                    await rest.nak(delay=self._backoff[0])
                return
            self._inc("rows", new_raw)
            self._inc("duplicate_packets", 1 - new_pkt)
            await msg.ack()

    async def process(self, msgs) -> None:
        """Parse, write and acknowledge one batch. Unparseable -> term; database error -> nak with backoff, nothing acknowledged."""
        batch, good = [], []
        for msg in msgs:
            self._inc("rx")
            if msg.metadata.num_delivered > 1:
                self._inc("redelivered")
            try:
                raw, pkt = rows_for(json.loads(msg.data))
            except Exception as e:  # noqa: BLE001  (ANY failure to understand one message ends that message, never the loop or its neighbours)
                self._inc("terminated")
                self.log(f"writer: cannot parse a message on {msg.subject} ({type(e).__name__}: {e}); terminated (see the dead-letter advisory)")
                await msg.term()
                continue
            batch.append((raw, pkt, time.time()))
            good.append(msg)
        if not batch:
            return
        try:
            new_raw, new_pkt = await asyncio.to_thread(self.store.write, batch)
        except BadData as e:
            self.log(f"writer: a batch of {len(good)} was rejected by the database as bad data ({e}); retrying one message at a time")
            await self._one_by_one(good, batch)
            return
        except Exception as e:  # noqa: BLE001  (any database failure: keep the messages, say so)
            self._inc("db_errors")
            self._degraded(1)
            delay = self._backoff[min(self._fails, len(self._backoff) - 1)]
            self._fails += 1
            self.log(f"writer: DEGRADED, database write failed ({type(e).__name__}: {e}); {len(good)} messages kept in NATS, retry in {delay} s")
            for msg in good:
                await msg.nak(delay=delay)
            return
        self._fails = 0
        self._degraded(0)
        self._inc("rows", new_raw)
        self._inc("duplicate_packets", len(good) - new_pkt)
        for msg in good:
            await msg.ack()


async def run() -> None:
    """Connect NATS and PostgreSQL and process forever."""
    import nats
    import psycopg
    from prometheus_client import Counter, Gauge, start_http_server

    from backend import bus

    dsn = os.environ["EHG_DB_DSN"]
    nc = await nats.connect(os.environ.get("NATS_URL", "nats://nats:4222"), name="ehg-ts-writer", max_reconnect_attempts=-1)
    js = nc.jetstream()
    await js.add_stream(bus.stream_config())
    sub = await js.pull_subscribe(f"{bus.PKT_PREFIX}.>", durable="ts-writer", stream=bus.STREAM, config=bus.consumer_config("ts-writer", max_deliver=bus.UNLIMITED))
    start_http_server(int(os.environ.get("WRITER_METRICS_PORT", "9202")))
    metrics = {k: Counter(f"ehg_writer_{k}", f"writer counter {k}") for k in ("rx", "rows", "duplicate_packets", "redelivered", "db_errors", "terminated")}
    metrics["degraded"] = Gauge("ehg_writer_degraded", "1 while the database is unreachable")
    writer = Writer(PgStore(lambda: psycopg.connect(dsn, connect_timeout=5)), metrics)
    from prometheus_client import REGISTRY

    from backend.triage.slis import ConsumerLag
    lag, last_lag = ConsumerLag(REGISTRY), 0.0
    print("ts-writer running")
    while True:
        if time.monotonic() - last_lag >= 10:  # how far behind the bus this consumer is (ehg_consumer_pending{consumer="ts-writer"})
            last_lag = time.monotonic()
            await lag.observe("ts-writer", sub)
        try:
            msgs = await sub.fetch(100, timeout=1)
        except nats.errors.TimeoutError:
            continue
        await writer.process(msgs)


if __name__ == "__main__":
    asyncio.run(run())
