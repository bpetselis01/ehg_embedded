"""Durable store-and-forward queue for the gateway's MQTT messages (decision 0009).

PURPOSE: Keeps every packet on disk until the broker confirms it, so a broker outage or a gateway restart loses nothing.
INPUT: add(node_id, seq, t_inject, topic, payload) once per packet, payload = the packet's JSON WITHOUT t_mqtt_pub;
  set_connected(True/False) from the MQTT client's connect/disconnect callbacks; acked(token) from its PUBACK callback.
  e.g. add("EHG-93C0", 40, 655360, "ehg/EHG-93C0/pkt", '{"node_id": "EHG-93C0", "seq": 40, ..., "t_gw_rx": 1791243068.46}')
OUTPUT: transport.send(topic, payload) calls, oldest first, at most max_inflight unacknowledged at once. The payload sent is the stored
  JSON plus `"replayed": true` (only when the message did not go out the moment it was added) and a fresh `"t_mqtt_pub": <now>`.
  e.g. live:    ... "t_gw_rx": 1791243068.46, "t_mqtt_pub": 1791243068.47}
       replayed: ... "t_gw_rx": 1791243068.46, "replayed": true, "t_mqtt_pub": 1791243131.9}   (original receive time kept)
  The file (SQLite, WAL, mode 0600): table outbox(id, node_id, seq, t_inject, topic, payload, created, attempts). attempts counts RE-sends only.
HOW: add() writes the row FIRST, then sends if connected. A row is deleted only when its PUBACK arrives (acked). On disconnect nothing is
  deleted: the unacknowledged rows are simply sent again after the next connect (a duplicate is harmless, ingest keys on node, seq, t_inject).
  A restart finds the leftover rows and sends them before anything new. If the file grows past max_bytes the OLDEST rows are deleted and
  logged loudly; if the disk write fails the message is still sent (just not durable) and the error is counted and logged.
  One lock guards everything because add() runs in the BLE/asyncio thread while the callbacks run in paho's thread.
ROLE IN THESIS: Gateway resilience beyond the board's 256-packet history (H2): an outage longer than about two minutes is recovered from
  disk instead of being lost, and replayed rows are marked so H1 can exclude them (eval/latency.py --exclude-replayed).
BEFORE -> AFTER: Before: gateway/gateway.py handed each JSON to paho's in-memory queue. After (the default since Checkpoint 2; --no-outbox
  restores the old path): it hands it here, and this module hands it to paho when the link is up. Only one process may own a file (a lock file
  next to it), so two gateways started from the same folder cannot share one by accident.
"""

import fcntl
import os
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL, seq INTEGER NOT NULL, t_inject INTEGER NOT NULL,
  topic TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)"""


class OutboxLockedError(RuntimeError):
    """Another gateway process already owns this outbox file."""


class Outbox:
    """On-disk queue; `transport.send(topic, payload)` returns a token (e.g. the MQTT message id) or None if it could not hand it over."""

    def __init__(self, path, transport, *, max_bytes: int = 512 * 2**20, max_inflight: int = 100, clock=time.time, log=print):
        path = self._path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # one process per outbox file: two would each send the other's rows. The lock goes with the process, so a crash never leaves it stuck.
        self._lock_file = os.fdopen(os.open(f"{path}.lock", os.O_RDWR | os.O_CREAT, 0o600), "w")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            raise OutboxLockedError(f"{path} is in use by another gateway process; give this one its own file (--outbox PATH) or turn the outbox off (--no-outbox)") from None
        os.close(os.open(path, os.O_RDWR | os.O_CREAT, 0o600))
        os.chmod(path, 0o600)  # SQLite gives the -wal and -shm files the same permission bits
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute(SCHEMA)
        self._transport, self._max_bytes, self._max_inflight, self._clock, self._log = transport, max_bytes, max_inflight, clock, log
        self._lock = threading.RLock()  # re-entrant: a transport may call acked() while send() is running (the fakes in tests do)
        self._connected, self._closed = False, False
        self._inflight: dict = {}  # token -> row id, for rows handed over and not yet acknowledged
        self._inflight_ids: set[int] = set()
        self.dropped = self.errors = self.stale_acks = 0

    # ------------------------------------------------------------ public API

    def add(self, node_id: str, seq: int, t_inject: int, topic: str, payload: str) -> None:
        """Write the message to disk, then send it if the link is up. Never raises: a disk problem falls back to a plain send."""
        with self._lock:
            try:
                row_id = self._db.execute(
                    "INSERT INTO outbox(node_id, seq, t_inject, topic, payload, created) VALUES (?,?,?,?,?,?)",
                    (node_id, seq, t_inject, topic, payload, self._clock())).lastrowid
            except sqlite3.Error as e:
                self.errors += 1
                self._log(f"[outbox] ERROR writing {self._path}: {e}; sending {node_id} seq {seq} WITHOUT durability")
                if self._connected:
                    self._transport.send(topic, self._wire(payload, False))
                return
            self._enforce_cap()
            self._drain(fresh=row_id)

    def set_connected(self, connected: bool) -> None:
        """The link went up (send what is waiting, oldest first) or down (forget what was in flight; it is sent again next time)."""
        with self._lock:
            self._connected = connected
            if connected:
                self._drain()
            else:
                self._inflight.clear()
                self._inflight_ids.clear()

    def acked(self, token) -> None:
        """The broker confirmed this message: delete its row and send the next waiting one."""
        with self._lock:
            row_id = self._inflight.pop(token, None)
            if row_id is None:
                self.stale_acks += 1  # PUBACK of a connection that no longer exists
                return
            self._inflight_ids.discard(row_id)
            try:
                self._db.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
            except sqlite3.Error as e:
                self.errors += 1
                self._log(f"[outbox] ERROR deleting row {row_id}: {e}")
            self._drain()

    def pending(self) -> int:
        """Rows still on disk (not yet confirmed by the broker)."""
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def stats(self) -> dict:
        """Counters for the 10 s status line."""
        with self._lock:
            try:
                pending = self.pending()
            except sqlite3.Error:
                pending = None  # the disk is the problem being reported; the status line must not raise too
            return {"pending": pending, "inflight": len(self._inflight), "dropped": self.dropped,
                    "errors": self.errors, "stale_acks": self.stale_acks}

    def close(self) -> None:
        """Close the file; leftover rows stay on disk for the next start."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._db.close()
                self._lock_file.close()  # releases the lock

    # ------------------------------------------------------------ internals

    def _wire(self, payload: str, replayed: bool) -> str:
        """Stored JSON + replayed flag + fresh publish time, spliced in as text (no parse/dump on the hot path)."""
        return payload[:-1] + (', "replayed": true' if replayed else "") + f', "t_mqtt_pub": {self._clock()!r}}}'

    def _next_unsent(self):
        """Oldest row that is not already in flight. In-flight rows are always the oldest, so looking at inflight+1 rows is enough."""
        rows = self._db.execute("SELECT id, topic, payload FROM outbox ORDER BY id LIMIT ?", (len(self._inflight_ids) + 1,)).fetchall()
        return next((r for r in rows if r[0] not in self._inflight_ids), None)

    def _drain(self, fresh: int | None = None) -> None:
        """Send waiting rows, oldest first, until the in-flight limit or the link is hit. `fresh` = the row added a moment ago (not a replay)."""
        try:
            while self._connected and len(self._inflight) < self._max_inflight:
                row = self._next_unsent()
                if row is None:
                    return
                row_id, topic, payload = row
                replayed = row_id != fresh
                token = self._transport.send(topic, self._wire(payload, replayed))
                if token is None:
                    return  # the client would not take it; the next set_connected(True) tries again
                self._inflight[token] = row_id
                self._inflight_ids.add(row_id)
                if replayed:
                    self._db.execute("UPDATE outbox SET attempts = attempts + 1 WHERE id = ?", (row_id,))
        except sqlite3.Error as e:
            self.errors += 1
            self._log(f"[outbox] ERROR reading the queue: {e}")

    def _used_bytes(self) -> int:
        """Database size in use (pages minus free pages): deleting rows lowers it even though the file does not shrink."""
        q = lambda p: self._db.execute(f"PRAGMA {p}").fetchone()[0]  # noqa: E731
        return (q("page_count") - q("freelist_count")) * q("page_size")

    def _enforce_cap(self) -> None:
        """Over the cap: delete the oldest rows (in batches) down to 90 %, and say so loudly with the lost seq range."""
        try:
            if self._used_bytes() <= self._max_bytes:
                return
            lost: dict[str, list[int]] = {}
            while self._used_bytes() > 0.9 * self._max_bytes:
                rows = self._db.execute("SELECT id, node_id, seq FROM outbox ORDER BY id LIMIT 50").fetchall()
                if not rows:
                    break
                self._db.execute("DELETE FROM outbox WHERE id <= ?", (rows[-1][0],))
                for _, node, seq in rows:
                    lost.setdefault(node, []).append(seq)
                self.dropped += len(rows)
            for node, seqs in lost.items():
                self._log(f"[outbox] FULL (cap {self._max_bytes} bytes): dropped {len(seqs)} oldest unsent packets of {node} (seq {min(seqs)}..{max(seqs)}); "
                          f"{self.dropped} dropped in total. They are LOST; eval/integrity.py will show the gap")
        except sqlite3.Error as e:
            self.errors += 1
            self._log(f"[outbox] ERROR enforcing the size cap: {e}")
