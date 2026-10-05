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
