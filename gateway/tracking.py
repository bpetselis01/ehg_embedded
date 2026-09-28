"""Pure gateway logic: packet-loss tracking and device-to-host clock mapping. No I/O."""

from gateway.packet import TICK_HZ

WRAP = 2**32
NEW_STREAM_BACKSTEP = 1000  # seq this far backwards = node rebooted / restarted stream


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
        # ponytail: dedup window is unbounded per stream (~4 bytes*seq); prune if soaks exceed days
        return gap


class ClockMap:
    """Maps device ticks to host epoch seconds.

    offset = min over packets of (host_rx - device_time): the packet with the least
    queueing delay bounds the true offset from above. Latencies computed with this
    offset are therefore lower bounds shifted by the (unknown, constant) minimum link
    delay - report that caveat with H1.
    # ponytail: no drift correction; DK crystal is +-20 ppm (~0.4 s over 6 h).
    # Add a linear fit of the min-offset envelope if soak results show drift.
    """

    def __init__(self, tick_hz: int = TICK_HZ):
        self.tick_hz = tick_hz
        self.offset = None
        self._last = None  # last unwrapped tick

    def _unwrap(self, ticks: int) -> int:
        if self._last is None:
            return ticks
        t = (self._last - self._last % WRAP) + ticks
        if t - self._last > WRAP // 2:
            t -= WRAP
        elif self._last - t > WRAP // 2:
            t += WRAP
        return t

    def update(self, ticks: int, t_host: float) -> None:
        t = self._unwrap(ticks)
        self._last = t if self._last is None else max(self._last, t)
        off = t_host - t / self.tick_hz
        self.offset = off if self.offset is None else min(self.offset, off)

    def to_host(self, ticks: int) -> float:
        return self._unwrap(ticks) / self.tick_hz + self.offset
