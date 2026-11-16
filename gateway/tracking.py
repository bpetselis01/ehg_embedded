"""Pure gateway logic: packet-loss tracking + board-to-laptop clock mapping. No I/O."""

from gateway.packet import TICK_HZ

WRAP = 2**32
NEW_STREAM_BACKSTEP = 1000  # seq this far backwards = node rebooted / restarted stream
RETRY_S, MAX_TRIES = 1.0, 5  # re-ask for a still-missing packet after 1 s, at most 5 times in all


def ranges(seqs: list[int]) -> list[tuple[int, int]]:
    """Group missing seqs into (start, count) runs, one RESEND each: [2,3,4,9] -> [(2,3),(9,1)]. A run is at most 0xFFFF long (the command's count is 16 bits)."""
    out: list[tuple[int, int]] = []
    for s in seqs:
        if out and out[-1][0] + out[-1][1] == s and out[-1][1] < 0xFFFF:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((s, 1))
    return out


class ResendScheduler:
    """Decides WHEN to re-ask the board for missing packets: new gaps at once, still-missing ones again every RETRY_S, at most MAX_TRIES requests in all.

    The re-sent copy can be lost on the air too, so one request is not enough. Pure (the caller passes the clock); the iPhone port in
    ios/EHGWire/Sources/EHGWire/Tracking.swift must behave identically (golden cases in tests/fixtures/packet_vectors.json, "resend")."""

    def __init__(self):
        self._asked: dict[int, tuple[float, int]] = {}  # missing seq -> (time of the last request, requests so far)
        self._stream = 0

    def due(self, gaps: "GapTracker", new_gaps: list[int], t: float) -> list[tuple[int, int]]:
        """(start, count) runs to request now. Call after gaps.observe(), with its return value as new_gaps."""
        if gaps.streams != self._stream:  # board restarted numbering: old requests mean nothing
            self._stream, self._asked = gaps.streams, {}
        due = list(new_gaps)
        for s in list(self._asked):
            if s not in gaps.missing:
                del self._asked[s]  # arrived
            elif t - self._asked[s][0] >= RETRY_S and self._asked[s][1] < MAX_TRIES:
                due.append(s)
        for s in due:
            self._asked[s] = (t, self._asked.get(s, (0, 0))[1] + 1)
        return ranges(sorted(due))


class GapTracker:
    """Tracks which seq numbers arrived. Reports new gaps so the caller can request resends."""

    def __init__(self):
        self.last = None
        self.missing: set[int] = set()
        self.received = 0
        self.duplicates = 0
        self.streams = 0
        self._seen_recent: set[int] = set()

    def observe(self, seq: int) -> list[int]:
        """Record one arrival; return the seq numbers newly found missing (empty list if none)."""
        if self.last is None or seq < self.last - NEW_STREAM_BACKSTEP:
            self.last, self.missing, self._seen_recent = seq, set(), {seq}
            self.streams += 1
            self.received += 1
            return []
        if seq in self._seen_recent:
            self.duplicates += 1
            return []
        self._seen_recent.add(seq)
        self.received += 1
        if seq <= self.last:  # late arrival (e.g. resend) fills a hole
            self.missing.discard(seq)
            return []
        gap = list(range(self.last + 1, seq))
        self.missing.update(gap)
        self.last = seq
        # shortcut: dedup window is unbounded per stream (~4 bytes*seq); prune if soaks exceed days
        return gap


class ClockMap:
    """Maps device ticks to host epoch seconds.

    offset = min over packets of (host_rx - device_time): the packet with the least
    queueing delay bounds the true offset from above. Latencies computed with this
    offset are therefore lower bounds shifted by the (unknown, constant) minimum link
    delay - report that caveat with H1.
    # shortcut: no drift correction; DK crystal is +-20 ppm (~0.4 s over 6 h).
    # Add a linear fit of the min-offset envelope if soak results show drift.
    """

    def __init__(self, tick_hz: int = TICK_HZ):
        self.tick_hz = tick_hz
        self.offset = None
        self._last = None  # last unwrapped tick

    def _unwrap(self, ticks: int) -> int:
        """The board's 32-bit tick counter rolls over to 0 every ~36 h; undo that roll-over."""
        if self._last is None:
            return ticks
        t = (self._last - self._last % WRAP) + ticks
        if t - self._last > WRAP // 2:
            t -= WRAP
        elif self._last - t > WRAP // 2:
            t += WRAP
        return t

    def update(self, ticks: int, t_host: float) -> None:
        """Learn from one (board time, laptop arrival time) pair; keep the smallest offset."""
        t = self._unwrap(ticks)
        self._last = t if self._last is None else max(self._last, t)
        off = t_host - t / self.tick_hz
        self.offset = off if self.offset is None else min(self.offset, off)

    def to_host(self, ticks: int) -> float:
        """Board ticks -> laptop time (Unix seconds)."""
        return self._unwrap(ticks) / self.tick_hz + self.offset
