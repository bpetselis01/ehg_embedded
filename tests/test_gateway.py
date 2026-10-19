"""Checks gateway/gateway.py:Node across a reconnect: the board keeps sampling while the link is down,
so the first packet after reconnecting shows a seq jump, and the same Node must ask for exactly the
missing range through the new connection's resend function (one stream, nothing restarted)."""

import pytest

from gateway.packet import FLAG_RETRANS, VERSION, Packet, pack


def pkt(seq, flags=0):
    return pack(Packet(VERSION, 3, 20, seq, seq * 10, seq * 16384, 10, flags, 0, [[seq, 0, 0]] * 10))


@pytest.fixture
def node(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # Node writes gateway/logs/<node>.csv relative to the working dir
    from gateway.gateway import Node
    out = []
    n = Node("EHG-TEST", lambda node_id, msg: out.append(msg), lambda s, c: None)
    n.published = out
    yield n
    n.close()


def test_gap_after_reconnect_requested_through_new_connection(node):
    old, new = [], []
    node.request_resend = lambda s, c: old.append((s, c))
    for s in range(10):
        node.on_bytes(pkt(s), 1000.0 + s)
    node.request_resend = lambda s, c: new.append((s, c))  # what run_ble_node does on reconnect
    node.on_bytes(pkt(40), 1100.0)
    assert old == [] and new == [(10, 30)]
    for s in range(10, 40):  # the board answers from its history
        node.on_bytes(pkt(s, FLAG_RETRANS), 1101.0)
    assert node.gaps.missing == set() and node.gaps.streams == 1
    assert len(node.published) == 41


def test_bad_crc_is_dropped_and_recovered_as_a_gap(node):
    asked = []
    node.request_resend = lambda s, c: asked.append((s, c))
    node.on_bytes(pkt(0), 1.0)
    bad = bytearray(pkt(1))
    bad[25] ^= 0x01
    node.on_bytes(bytes(bad), 2.0)  # rejected: never published
    node.on_bytes(pkt(2), 3.0)
    assert asked == [(1, 1)]
    assert [m["seq"] for m in node.published] == [0, 2]


def test_lost_resend_is_requested_again_after_a_second(node):
    asked = []
    node.request_resend = lambda s, c: asked.append((s, c))
    node.on_bytes(pkt(0), 0.0)
    node.on_bytes(pkt(2), 0.5)      # 1 missing: asked at once
    node.on_bytes(pkt(3), 1.0)      # resend of 1 lost on the air too; < 1 s since asking: wait
    node.on_bytes(pkt(4), 1.6)      # > 1 s: ask again
    assert asked == [(1, 1), (1, 1)]
    node.on_bytes(pkt(1, FLAG_RETRANS), 1.7)
    node.on_bytes(pkt(5), 3.0)      # arrived: no more requests
    assert asked == [(1, 1), (1, 1)]


def test_gives_up_after_max_tries(node):
    from gateway.gateway import MAX_TRIES
    asked = []
    node.request_resend = lambda s, c: asked.append(s)
    node.on_bytes(pkt(0), 0.0)
    for k in range(2, 20):
        node.on_bytes(pkt(k), float(k))  # packet 1 never comes back
    assert asked.count(1) == MAX_TRIES
