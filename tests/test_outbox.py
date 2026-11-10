"""Checks the gateway's durable outbox (decision 0009): a message is written to disk BEFORE it is sent, deleted only when the broker's
PUBACK arrives, replayed oldest-first after an outage or restart, bounded by a disk cap, and the whole thing is off unless asked for."""

import json
import os
import sqlite3
import stat
from types import SimpleNamespace

import pytest

from gateway.outbox import Outbox


class FakeTransport:
    """Stands in for the MQTT client: records what was handed over; `up=False` = broker unreachable (send returns None)."""

    def __init__(self):
        self.up, self.mid, self.sent = True, 0, []

    def send(self, topic, payload):
        if not self.up:
            return None
        self.mid += 1
        self.sent.append((self.mid, topic, json.loads(payload)))
        return self.mid

    def seqs(self):
        return [m["seq"] for _, _, m in self.sent]


def message(seq):
    return json.dumps({"node_id": "EHG-T", "seq": seq, "t_inject": seq * 16384, "samples": [[seq, 0, 0]] * 10, "t_gw_rx": 100.0 + seq})


def add(ob, seq):
    ob.add("EHG-T", seq, seq * 16384, "ehg/EHG-T/pkt", message(seq))


@pytest.fixture
def make(tmp_path):
    made = []

    def factory(transport=None, name="outbox.db", **kw):
        t = transport or FakeTransport()
        ob = Outbox(tmp_path / name, t, log=lambda *_: None, **kw)
        made.append(ob)
        return ob, t
    yield factory
    for ob in made:
        ob.close()


def test_connected_message_is_sent_at_once_marked_live_and_kept_until_acked(make):
    ob, t = make()
    ob.set_connected(True)
    add(ob, 0)
    assert t.seqs() == [0] and "replayed" not in t.sent[0][2] and isinstance(t.sent[0][2]["t_mqtt_pub"], float)
    assert ob.pending() == 1, "a row is deleted only on PUBACK"
    ob.acked(t.sent[0][0])
    assert ob.pending() == 0


def test_broker_down_rows_accumulate_then_arrive_in_order_exactly_once(make):
    ob, t = make()
    for s in range(5):
        add(ob, s)                      # never connected: nothing may be sent, nothing may be lost
    assert t.sent == [] and ob.pending() == 5
    ob.set_connected(True)
    assert t.seqs() == [0, 1, 2, 3, 4]
    assert all(m["replayed"] is True for _, _, m in t.sent), "late messages are marked as replays"
    assert all(m["t_gw_rx"] == 100.0 + m["seq"] for _, _, m in t.sent), "replays keep their original receive time"
    for mid, _, _ in t.sent:
        ob.acked(mid)
    assert ob.pending() == 0 and t.seqs() == [0, 1, 2, 3, 4]


def test_replay_gets_a_fresh_publish_time(make):
    ob, t = make()
    add(ob, 0)
    ob.set_connected(True)
    assert t.sent[0][2]["t_mqtt_pub"] > t.sent[0][2]["t_gw_rx"]


def test_restart_replays_what_was_never_acknowledged(make):
    ob, t = make(name="a.db")
    for s in range(3):
        add(ob, s)
    ob.close()                          # the process dies with 3 messages unsent
    ob2, t2 = make(name="a.db")
    assert ob2.pending() == 3
    ob2.set_connected(True)
    assert t2.seqs() == [0, 1, 2] and all(m["replayed"] for _, _, m in t2.sent)


def test_pending_rows_go_out_before_new_traffic(make):
    ob, t = make(name="b.db")
    for s in range(3):
        add(ob, s)
    ob.close()
    ob2, t2 = make(name="b.db")
    ob2.set_connected(True)
    add(ob2, 3)
    assert t2.seqs() == [0, 1, 2, 3]


def test_at_most_max_inflight_messages_are_outstanding(make):
    ob, t = make(max_inflight=3)
    ob.set_connected(True)
    for s in range(10):
        add(ob, s)
    assert t.seqs() == [0, 1, 2]
    ob.acked(1)
    assert t.seqs() == [0, 1, 2, 3], "an acknowledgement frees a slot for the oldest waiting row"
    assert t.sent[-1][2]["replayed"] is True, "it waited, so it is a replay"


def test_disconnect_with_unacknowledged_messages_resends_them_and_ignores_stale_acks(make):
    ob, t = make()
    ob.set_connected(True)
    for s in range(3):
        add(ob, s)
    old = [mid for mid, _, _ in t.sent]
    ob.set_connected(False)
    ob.set_connected(True)
    assert t.seqs() == [0, 1, 2, 0, 1, 2]
    assert all(m["replayed"] for _, _, m in t.sent[3:])
    for mid in old:
        ob.acked(mid)                   # PUBACKs of the dead connection
    assert ob.pending() == 3 and ob.stats()["stale_acks"] == 3
    for mid, _, _ in t.sent[3:]:
        ob.acked(mid)
    assert ob.pending() == 0


def test_lost_puback_resends_the_same_message_which_ingest_then_ignores(make, monkeypatch):
    """The duplicate is real (same node, seq, t_inject, same samples). Ingest keys on (node, seq, t_inject), so it writes once."""
    ob, t = make()
    ob.set_connected(True)
    add(ob, 7)
    ob.set_connected(False)             # connection dropped before the PUBACK
    ob.set_connected(True)
    first, second = t.sent[0][2], t.sent[1][2]
    assert (first["node_id"], first["seq"], first["t_inject"], first["samples"]) == (second["node_id"], second["seq"], second["t_inject"], second["samples"])
    assert second["replayed"] is True and "replayed" not in first


def test_disk_cap_drops_the_oldest_loudly_and_keeps_the_newest(tmp_path):
    lines, t = [], FakeTransport()
    ob = Outbox(tmp_path / "cap.db", t, max_bytes=60_000, log=lines.append)
    for s in range(400):
        add(ob, s)                      # broker down the whole time
    st = ob.stats()
    assert st["dropped"] > 0 and ob.pending() + st["dropped"] == 400
    assert any("dropped" in str(line) for line in lines), "dropping data must never be silent"
    ob.set_connected(True)
    sent = t.seqs()
    assert sent == sorted(sent) and sent[-1] == 399, "what is kept is the newest, in order"
    assert sent == list(range(sent[0], 400)), "the kept rows are one contiguous run, so the gap is easy to identify"
    ob.close()


def test_database_error_does_not_lose_the_message_or_hide_the_problem(tmp_path):
    lines, t = [], FakeTransport()
    ob = Outbox(tmp_path / "e.db", t, log=lines.append)
    ob.set_connected(True)

    class Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O error")
    real, ob._db = ob._db, Broken()
    add(ob, 0)
    assert t.seqs() == [0], "best effort: still sent, just not durable"
    assert ob.stats()["errors"] == 1 and any("outbox" in str(line) for line in lines)
    ob._db = real
    ob.close()


def test_file_is_private_and_uses_wal(tmp_path):
    p = tmp_path / "private.db"
    ob = Outbox(p, FakeTransport(), log=lambda *_: None)
    add(ob, 0)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert sqlite3.connect(p).execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    ob.close()


# ---------------------------------------------------------------- wiring in gateway.make_publisher

class FakePaho:
    """Just enough of paho.mqtt.client.Client to see how make_publisher wires it."""
    instances = []

    def __init__(self, *a, **k):
        self.published, self.calls, self.mid = [], [], 0
        self.on_connect = self.on_disconnect = self.on_publish = self.on_connect_fail = None
        FakePaho.instances.append(self)

    def __getattr__(self, name):        # max_queued_messages_set, loop_start, connect, ...: record and ignore
        return lambda *a, **k: self.calls.append((name, a, k))

    def publish(self, topic, payload, qos=0, retain=False):
        self.mid += 1
        self.published.append((topic, payload, qos, retain))
        return SimpleNamespace(rc=0, mid=self.mid, wait_for_publish=lambda *a, **k: None)


@pytest.fixture
def fake_paho(monkeypatch, tmp_path):
    import paho.mqtt.client as mqtt
    monkeypatch.chdir(tmp_path)  # the default outbox path (gateway/outbox.db) is relative to the working directory
    FakePaho.instances = []
    monkeypatch.setattr(mqtt, "Client", FakePaho)
    return FakePaho


def args_for(**kw):
    from gateway import gateway
    flags = []
    for k, v in kw.items():
        flags += [f"--{k.replace('_', '-')}"] + ([] if v is True else [str(v)])
    return gateway.parse_args(["--simulate", "1", *flags])


def ok_connack():
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.reasoncodes import ReasonCode
    return ReasonCode(PacketTypes.CONNACK, "Success")


def test_outbox_is_on_by_default_and_holds_packets_until_the_broker_is_there(fake_paho, tmp_path):
    from gateway import gateway
    args = args_for()
    assert args.outbox == "gateway/outbox.db" and args.outbox_max_mb == 512 and args.no_outbox is False
    publish, close = gateway.make_publisher(args)
    c = fake_paho.instances[0]
    assert (tmp_path / "gateway/outbox.db").exists()
    publish("EHG-T", json.loads(message(1)))
    assert c.published == [], "not connected yet: held on disk, not in paho's memory"
    c.on_connect(c, None, None, ok_connack(), None)
    assert [json.loads(p)["seq"] for _, p, _, _ in c.published] == [1]
    close()


def test_no_outbox_restores_the_plain_publish_path(fake_paho, tmp_path):
    from gateway import gateway
    args = args_for(no_outbox=True)
    assert args.outbox is None
    publish, close = gateway.make_publisher(args)
    c = fake_paho.instances[0]
    publish("EHG-T", json.loads(message(1)))
    topic, payload, qos, retain = c.published[0]
    assert (topic, qos, retain) == ("ehg/EHG-T/pkt", 1, False) and "t_mqtt_pub" in json.loads(payload) and "replayed" not in json.loads(payload)
    assert c.on_connect is None and not list(tmp_path.rglob("*.db")), "no callbacks, no database"
    assert not any(name in ("will_set", "reconnect_delay_set", "connect_async") for name, _, _ in c.calls)
    assert ("connect", ("localhost", 1883), {}) in c.calls


def test_outbox_and_no_outbox_cannot_be_combined():
    with pytest.raises(SystemExit):
        args_for(outbox="x.db", no_outbox=True)


def test_second_process_on_the_same_outbox_file_is_refused(tmp_path):
    from gateway.outbox import OutboxLockedError
    first = Outbox(tmp_path / "shared.db", FakeTransport(), log=lambda *_: None)
    with pytest.raises(OutboxLockedError, match="another gateway process"):
        Outbox(tmp_path / "shared.db", FakeTransport(), log=lambda *_: None)
    first.close()
    Outbox(tmp_path / "shared.db", FakeTransport(), log=lambda *_: None).close()  # free again once the owner is gone


def test_gateway_exits_with_a_clear_message_when_the_outbox_is_taken(fake_paho, tmp_path):
    from gateway import gateway
    publish, close = gateway.make_publisher(args_for())
    with pytest.raises(SystemExit, match="another gateway process"):
        gateway.make_publisher(args_for())
    close()


def test_unreachable_broker_is_reported_not_retried_in_silence(fake_paho, capsys):
    from gateway import gateway
    publish, close = gateway.make_publisher(args_for(mqtt="broker.invalid"))
    c = fake_paho.instances[0]
    c.on_connect_fail(c, None)
    out = capsys.readouterr().out
    assert "cannot reach the broker at broker.invalid:1883" in out and "outbox" in out
    close()


def test_outbox_on_buffers_until_connected_and_backs_off_1_to_60_s(fake_paho, tmp_path):
    from gateway import gateway
    publish, close = gateway.make_publisher(args_for(outbox=tmp_path / "gw.db"))
    c = fake_paho.instances[0]
    assert ("reconnect_delay_set", (1, 60), {}) in c.calls
    assert any(name == "connect_async" for name, _, _ in c.calls), "a broker that is down at start-up must not stop the gateway"
    publish("EHG-T", json.loads(message(1)))
    assert c.published == [], "not connected yet: held in the outbox"
    c.on_connect(c, None, None, ok_connack(), None)
    assert [json.loads(p)["seq"] for _, p, _, _ in c.published] == [1] and json.loads(c.published[0][1])["replayed"] is True
    c.on_publish(c, None, 1, None, None)
    close()
    assert sqlite3.connect(tmp_path / "gw.db").execute("select count(*) from outbox").fetchone()[0] == 0


def test_gateway_id_sets_a_retained_last_will_and_announces_online(fake_paho):
    from gateway import gateway
    gateway.make_publisher(args_for(gateway_id="ehg-gw-01"))
    c = fake_paho.instances[0]
    assert ("will_set", ("ehg/_gw/ehg-gw-01/status", "offline"), {"qos": 1, "retain": True}) in c.calls
    c.on_connect(c, None, None, ok_connack(), None)
    assert ("ehg/_gw/ehg-gw-01/status", "online", 1, True) in c.published


def test_outbox_size_flag_is_in_megabytes():
    assert args_for(outbox="x.db").outbox_max_mb == 512
    assert args_for(outbox="x.db", outbox_max_mb=1).outbox_max_mb == 1


# ---------------------------------------------------------------- ingest and evaluation sides

def load_ingest(monkeypatch):
    """backend/ingest/ingest.py with the database replaced by a recorder (importing it needs only environment variables)."""
    import importlib.util
    from pathlib import Path
    for k, v in (("INFLUX_BUCKET", "b"), ("INFLUX_URL", "http://localhost:1"), ("INFLUX_TOKEN", "t"), ("INFLUX_ORG", "o")):
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("ingest_under_test", Path(__file__).parents[1] / "backend/ingest/ingest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.written = []
    mod.write = SimpleNamespace(write=lambda bucket, record: mod.written.append(record))
    return mod


def wire_message(seq, **extra):
    m = json.loads(message(seq))
    m.update(frame_idx=seq * 10, t_inject_host=1000.0 + seq, n_ch=3, fs_hz=20, record_id=0, flags=0, t_mqtt_pub=1000.5 + seq, **extra)
    return SimpleNamespace(payload=json.dumps(m).encode())


def test_ingest_stores_the_replayed_flag(monkeypatch):
    ing = load_ingest(monkeypatch)
    ing.on_message(None, None, wire_message(1))
    ing.on_message(None, None, wire_message(2, replayed=True))
    pkt_points = [r for r in ing.written if hasattr(r, "_fields")]
    assert [p._fields["replayed"] for p in pkt_points] == [False, True]


def test_ingest_ignores_a_message_delivered_twice(monkeypatch):
    """QoS 1 plus an outbox resend: the second copy must change nothing in the database."""
    ing = load_ingest(monkeypatch)
    ing.on_message(None, None, wire_message(5))
    once = len(ing.written)
    ing.on_message(None, None, wire_message(5, replayed=True))
    assert len(ing.written) == once == 2


def test_latency_can_drop_replayed_packets():
    from eval.latency import without_replayed
    pkts = [{"seq": 1}, {"seq": 2, "replayed": True}, {"seq": 3, "replayed": False}]
    assert [p["seq"] for p in without_replayed(pkts)] == [1, 3]


# ---------------------------------------------------------------- the whole thing against a real broker (opt-in: EHG_INTEGRATION=1 and mosquitto installed)

from test_mqtt_tls_config import broker, client_context, needs_broker, needs_openssl, spawn_broker  # noqa: E402,F401  (broker is a fixture)

SIM_RECORD = "data/converted/tpehgdb/tpehg1007.npz"


@needs_openssl
@needs_broker
def test_broker_outage_loses_nothing_and_replays_in_order(broker, tmp_path):  # noqa: F811  (broker is the imported fixture)
    """gateway --simulate 1 --outbox into the real TLS broker; the broker is stopped for 5 s mid-run and started again.
    A persistent reader must then see every seq from 0 with no gap, and the late ones must be marked replayed."""
    import signal
    import subprocess
    import sys
    import threading
    import time
    from pathlib import Path

    import paho.mqtt.client as mqtt

    root = Path(__file__).parents[1]
    if not (root / SIM_RECORD).exists():
        pytest.skip("data/converted not present (README step 0)")
    got = []
    reader = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ehg-viewer", clean_session=False)
    reader.tls_set_context(client_context(broker.pki, "ehg-viewer"))
    subscribed = threading.Event()
    reader.on_connect = lambda cl, u, f, rc, p: cl.subscribe("ehg/+/pkt", qos=1)
    reader.on_subscribe = lambda *a: subscribed.set()
    reader.on_message = lambda cl, u, m: got.append(json.loads(m.payload))
    reader.reconnect_delay_set(1, 2)
    reader.connect("localhost", broker.port)
    reader.loop_start()
    assert subscribed.wait(5)

    db = tmp_path / "gw.db"
    p = broker.pki
    cmd = [sys.executable, "-m", "gateway.gateway", "--simulate", "1", "--sim-record", SIM_RECORD, "--mqtt", "localhost", "--mqtt-port", str(broker.port),
           "--mqtt-tls", "--ca", str(p / "ca-chain.crt"), "--cert", str(p / "clients/ehg-gw-01.crt"), "--key", str(p / "clients/ehg-gw-01.key"),
           "--outbox", str(db), "--gateway-id", "ehg-gw-01"]
    gw = subprocess.Popen(cmd, cwd=root, env={**os.environ, "PYTHONUNBUFFERED": "1"}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def waiting() -> int:
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute("select count(*) from outbox").fetchone()[0]

    time.sleep(6)
    broker.proc.terminate()
    broker.proc.wait(10)
    time.sleep(5)                       # about 10 packets (2 per second) pile up on disk
    queued = waiting()
    broker.proc = spawn_broker(broker.conf, broker.port)
    deadline = time.time() + 45
    while waiting() > 3 and time.time() < deadline:  # a few packets are always in flight in a live stream
        time.sleep(0.5)
    time.sleep(2)
    gw.send_signal(signal.SIGINT)
    out = gw.communicate(timeout=20)[0]
    reader.loop_stop()
    reader.disconnect()

    assert queued >= 5, f"packets should have accumulated on disk during the outage, found {queued}\n{out}"
    seqs = sorted({m["seq"] for m in got})
    assert seqs and seqs == list(range(len(seqs))), f"gap in the delivered seqs: missing {sorted(set(range(seqs[-1] + 1)) - set(seqs))}\n{out}"
    assert len(seqs) >= 20
    replayed = [m for m in got if m.get("replayed")]
    assert len(replayed) >= 5 and all(m["t_mqtt_pub"] > m["t_gw_rx"] for m in replayed), "replays carry a publish time later than their receive time"
    assert max(m["t_mqtt_pub"] - m["t_gw_rx"] for m in replayed) >= 4, "the packets that waited out the 5 s outage show it in their publish time"
    assert all("replayed" not in m for m in got if m["seq"] < 5), "packets sent while the link was up are not marked"
    assert "link down" in out and "[outbox] connected" in out, out
