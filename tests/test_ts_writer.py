"""Checks the TimescaleDB writer (decision 0012): rows are exactly the samples (raw integers), writes are idempotent, a batch is atomic, messages are acknowledged only after the
commit, a database outage keeps them in NATS and shows as degraded, and unparseable messages are terminated visibly. SQL tests use a throwaway local PostgreSQL (tests/conftest.py)."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.db import migrate
from backend.ts_writer.writer import PgStore, Writer, rows_for, ts


def message(seq=40, node="EHG-93C0", frames=10, n_ch=3, **extra):
    m = {"node_id": node, "seq": seq, "frame_idx": seq * frames, "t_inject": seq * 16384, "t_inject_host": 1791243068.018676 + seq * 0.5, "n_ch": n_ch,
         "fs_hz": 20, "record_id": 0, "flags": 0, "t_gw_rx": 1791243068.47 + seq * 0.5, "t_mqtt_pub": 1791243068.48 + seq * 0.5,
         "samples": [[1093 + f, 171 - f, -748 - 3 * f][:n_ch] for f in range(frames)]}
    m.update(extra)
    return m


class FakeMsg:
    def __init__(self, m, delivered=1, subject="EHG.PKT.EHG-93C0", raw=None):
        self.data = raw if raw is not None else json.dumps(m).encode()
        self.subject, self.metadata, self.calls = subject, SimpleNamespace(num_delivered=delivered), []

    async def ack(self):
        self.calls.append("ack")

    async def nak(self, delay=None):
        self.calls.append(("nak", delay))

    async def term(self):
        self.calls.append("term")


class FakeStore:
    def __init__(self):
        self.batches, self.fail, self.new_pkt = [], None, None

    def write(self, batch):
        if self.fail:
            raise self.fail
        self.batches.append(batch)
        return sum(len(r) for r, _, _ in batch), len(batch) if self.new_pkt is None else self.new_pkt


def test_rows_are_one_per_frame_and_channel_with_raw_integers():
    raw, pkt = rows_for(message(seq=40))
    assert len(raw) == 30 and pkt["n_frames"] == 10
    t0, node, rec, seq, fidx, ch, v = raw[0]
    assert (node, rec, seq, fidx, ch, v) == ("EHG-93C0", "0", 40, 400, 0, 1093) and t0 == ts(1791243068.018676 + 20.0)
    assert [r[6] for r in raw[:3]] == [1093, 171, -748] and all(isinstance(r[6], int) for r in raw)
    assert raw[3][0] - raw[0][0] == ts(0.05) - ts(0), "frames are 1/fs apart"
    assert (pkt["retrans"], pkt["replayed"]) == (False, False)


def test_flags_and_replayed_are_stored():
    assert rows_for(message(flags=1, replayed=True))[1]["retrans"] is True
    assert rows_for(message(flags=1, replayed=True))[1]["replayed"] is True
    assert rows_for(message(flags=3))[1]["retrans"] is True


def test_24_bit_samples_are_kept_exactly():
    m = message(frames=1)
    m["samples"] = [[8388607, -8388608, 0]]
    assert [r[6] for r in rows_for(m)[0]] == [8388607, -8388608, 0]


@pytest.mark.parametrize("bad", [{"samples": "x"}, {"fs_hz": 0}, {"t_inject_host": None}])
def test_malformed_messages_raise(bad):
    m = message()
    m.update(bad)
    with pytest.raises((KeyError, ValueError, TypeError, ZeroDivisionError)):
        rows_for(m)


def run(w, msgs):
    asyncio.run(w.process(msgs))


def test_messages_are_acknowledged_only_after_the_write_succeeds():
    store, logs = FakeStore(), []
    w = Writer(store, log=logs.append)
    msgs = [FakeMsg(message(seq=s)) for s in range(3)]
    run(w, msgs)
    assert [m.calls for m in msgs] == [["ack"]] * 3 and len(store.batches) == 1 and len(store.batches[0]) == 3, "one batch, one transaction"
    assert w.counters["rx"] == 3 and w.counters["rows"] == 90 and w.counters["degraded"] == 0


def test_database_down_keeps_messages_in_nats_and_reports_degraded_then_recovers():
    store, logs = FakeStore(), []
    w = Writer(store, log=logs.append, backoff=(1, 2, 4))
    store.fail = ConnectionError("connection refused")
    msgs = [FakeMsg(message(seq=s)) for s in range(2)]
    run(w, msgs)
    assert all(m.calls == [("nak", 1)] for m in msgs), "not acknowledged: NATS redelivers after the delay"
    assert w.counters["degraded"] == 1 and w.counters["db_errors"] == 1 and any("DEGRADED" in str(x) for x in logs)
    run(w, [FakeMsg(message(seq=9))])  # still down: the delay grows
    assert w.counters["db_errors"] == 2
    store.fail = None
    redelivered = [FakeMsg(message(seq=s), delivered=2) for s in range(2)]
    run(w, redelivered)
    assert all(m.calls == ["ack"] for m in redelivered) and w.counters["degraded"] == 0 and w.counters["redelivered"] == 2


def test_unparseable_message_is_terminated_and_logged_not_retried_forever():
    store, logs = FakeStore(), []
    w = Writer(store, log=logs.append)
    bad, good = FakeMsg(None, raw=b"{not json"), FakeMsg(message())
    run(w, [bad, good])
    assert bad.calls == ["term"] and good.calls == ["ack"] and w.counters["terminated"] == 1 and any("cannot parse" in str(x) for x in logs)


def test_already_stored_packets_are_counted_as_duplicates_and_still_acknowledged():
    store = FakeStore()
    store.new_pkt = 0  # the database says: nothing new
    w = Writer(store, log=lambda *_: None)
    msg = FakeMsg(message())
    run(w, [msg])
    assert msg.calls == ["ack"] and w.counters["duplicate_packets"] == 1


# ---------------------------------------------------------------- real SQL on a throwaway PostgreSQL

@pytest.fixture
def store(pg_url):
    import psycopg
    migrate.main(["upgrade", "0001", "--url", pg_url])
    dsn = pg_url.replace("+psycopg", "")
    s = PgStore(lambda: psycopg.connect(dsn))
    s.dsn = dsn
    return s


def batch_of(*msgs):
    return [(*rows_for(m), 1791243100.0) for m in msgs]


def count(store, table):
    import psycopg
    with psycopg.connect(store.dsn) as c:
        return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_sql_write_stores_every_sample_exactly_and_is_idempotent(store):
    import psycopg
    new_raw, new_pkt = store.write(batch_of(message(seq=1)))
    assert (new_raw, new_pkt) == (30, 1) and count(store, "ehg_raw") == 30 and count(store, "ehg_pkt") == 1
    assert store.write(batch_of(message(seq=1))) == (0, 0), "the same packet again changes nothing"
    assert count(store, "ehg_raw") == 30
    with psycopg.connect(store.dsn) as c:
        vals = c.execute("SELECT v FROM ehg_raw WHERE node='EHG-93C0' AND seq=1 AND frame_idx=10 ORDER BY ch").fetchall()
        ack, rx = c.execute("SELECT t_db_ack, t_ingest_rx FROM ehg_pkt").fetchone()
    assert [v[0] for v in vals] == [1093, 171, -748] and ack >= rx


def test_sql_batch_is_atomic(store):
    bad = message(seq=2)
    bad["samples"][0][0] = 2**40  # does not fit an integer column
    with pytest.raises(Exception):
        store.write(batch_of(message(seq=1), bad))
    assert count(store, "ehg_raw") == 0 and count(store, "ehg_pkt") == 0, "nothing from a failed batch may remain"
    assert store.write(batch_of(message(seq=1))) == (30, 1), "and the store reconnects and works on the next batch"


def test_sql_two_batches_overlapping_store_the_union_once(store):
    store.write(batch_of(message(seq=1), message(seq=2)))
    store.write(batch_of(message(seq=2), message(seq=3)))
    assert count(store, "ehg_pkt") == 3 and count(store, "ehg_raw") == 90


# ---------------------------------------------------------------- poison messages (automated security review, 2026-11-03)

POISON = [
    {"fs_hz": 0}, {"fs_hz": 0.5}, {"fs_hz": -20}, {"fs_hz": float("nan")}, {"fs_hz": 10**9}, {"t_inject_host": 1e30}, {"t_inject_host": float("nan")}, {"t_inject_host": float("inf")},
    {"t_inject_host": 5.0}, {"t_gw_rx": "soon"}, {"seq": 2**40}, {"seq": 1.5}, {"seq": True}, {"seq": "40"}, {"t_inject": -1}, {"t_inject": 2**33}, {"flags": 999},
    {"node_id": "bad.node"}, {"node_id": "x" * 65}, {"node_id": "a\x00b"}, {"node_id": 7}, {"record_id": "r" * 100},
    {"samples": [[1.5, 2, 3]]}, {"samples": [[True, 2, 3]]}, {"samples": [[2**40, 0, 0]]}, {"samples": [[1, 2], [1, 2, 3]]}, {"samples": []}, {"samples": [[0] * 65]},
    {"samples": [[0, 0, 0]] * 20000},
]


@pytest.mark.parametrize("bad", POISON, ids=lambda b: str(b)[:40])
def test_every_kind_of_malformed_message_is_terminated_and_never_stops_its_neighbours(bad):
    store, logs = FakeStore(), []
    w = Writer(store, log=logs.append)
    broken = message(seq=40)
    broken.update(bad)
    poison, good = FakeMsg(broken), FakeMsg(message(seq=41))
    run(w, [poison, good])
    assert poison.calls == ["term"] and good.calls == ["ack"], "only the offending message is terminated"
    assert w.counters["terminated"] == 1 and len(store.batches) == 1 and len(store.batches[0]) == 1


def test_not_a_json_object_is_terminated_too():
    w = Writer(FakeStore(), log=lambda *_: None)
    msgs = [FakeMsg(None, raw=b"[1, 2, 3]"), FakeMsg(None, raw=b"null"), FakeMsg(None, raw=b"\xff\xfe"), FakeMsg(None, raw=b'{"node_id": 1}')]
    run(w, msgs)
    assert [m.calls for m in msgs] == [["term"]] * 4


class OneBadRow(FakeStore):
    """Rejects any batch containing seq 13 as bad DATA (not a connection problem)."""

    def write(self, batch):
        from backend.ts_writer.writer import BadData
        if any(pkt["seq"] == 13 for _, pkt, _ in batch):
            raise BadData("value out of range")
        return super().write(batch)


def test_a_data_error_is_retried_one_message_at_a_time_and_only_the_bad_one_is_terminated():
    store, logs = OneBadRow(), []
    w = Writer(store, log=logs.append)
    msgs = [FakeMsg(message(seq=s)) for s in (12, 13, 14)]
    run(w, msgs)
    assert [m.calls for m in msgs] == [["ack"], ["term"], ["ack"]], "the neighbours of the poison message are stored and acknowledged"
    assert w.counters["degraded"] == 0 and w.counters["db_errors"] == 0, "bad data is not an outage"
    assert any("rejected by the database" in str(x) for x in logs)


def test_sql_data_error_becomes_baddata_and_the_connection_stays_usable(store):
    from backend.ts_writer.writer import BadData
    bad = message(seq=2)
    bad["samples"][0][0] = 2**40  # bypasses rows_for validation on purpose: the database itself must refuse and the store must say "bad data"
    raw, pkt = rows_for(message(seq=2))
    raw[0] = raw[0][:6] + (2**40,)
    with pytest.raises(BadData):
        store.write([(raw, pkt, 1.0)])
    assert store.write(batch_of(message(seq=1))) == (30, 1)
    assert count(store, "ehg_raw") == 30
