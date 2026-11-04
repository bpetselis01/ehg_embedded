"""Checks gateway/tracking.py: gap detection, resend filling a gap, duplicates,
node restart detection (H2), and the clock map's min-offset and counter roll-over (H1)."""

from gateway.tracking import ClockMap, GapTracker


def test_in_order_has_no_gaps():
    g = GapTracker()
    assert [g.observe(s) for s in range(5)] == [[]] * 5
    assert g.received == 5 and g.missing == set()


def test_gap_reported_then_filled_by_resend():
    g = GapTracker()
    g.observe(0); g.observe(1)
    assert g.observe(4) == [2, 3]
    assert g.missing == {2, 3}
    g.observe(2)
    assert g.missing == {3} and g.received == 4


def test_duplicate_counted_not_received_twice():
    g = GapTracker()
    g.observe(0); g.observe(1); g.observe(1)
    assert g.duplicates == 1 and g.received == 2


def test_seq_going_far_backwards_is_new_stream():
    g = GapTracker()
    g.observe(100000)
    assert g.observe(0) == []
    assert g.streams == 2 and g.missing == set()


def test_clockmap_uses_min_offset():
    c = ClockMap()
    for i, jitter in enumerate([0.030, 0.010, 0.050]):
        c.update(i * 32768, 1000.0 + i + jitter)
    assert abs(c.to_host(0) - 1000.010) < 1e-9


def test_clockmap_unwraps_u32_ticks():
    c = ClockMap()
    c.update(2**32 - 32768, 1000.0)
    c.update(0, 1001.0)  # wrapped: one second later
    assert abs(c.to_host(0) - 1001.0) < 1e-6


# ---- ResendScheduler (moved here from gateway.Node so the iPhone port has one reference; golden cases: tests/fixtures/packet_vectors.json)

def _run(steps):
    from gateway.tracking import GapTracker, ResendScheduler
    g, s, out = GapTracker(), ResendScheduler(), []
    for when, seq in steps:
        out.append(s.due(g, g.observe(seq) if seq is not None else [], when))
    return out


def test_new_gap_is_requested_at_once_and_runs_are_merged():
    assert _run([(0.0, 0), (0.1, 5)]) == [[], [(1, 4)]]


def test_retry_after_one_second_and_never_more_than_five_requests():
    steps = [(0.0, 0), (0.1, 2)] + [(1.1 * k, None) for k in range(1, 9)]
    asked = [r for r in _run(steps) if r]
    assert len(asked) == 5 and all(r == [(1, 1)] for r in asked)  # 1 at once + 4 retries


def test_no_retry_before_the_interval_and_none_once_filled():
    out = _run([(0.0, 0), (0.1, 2), (1.0, None), (1.2, 1), (3.0, None)])
    assert out[1] == [(1, 1)] and out[2] == [] and out[4] == []


def test_constants_are_reexported_for_the_gateway_and_triage():
    from gateway import gateway, tracking
    assert (gateway.RETRY_S, gateway.MAX_TRIES) == (tracking.RETRY_S, tracking.MAX_TRIES) == (1.0, 5)
