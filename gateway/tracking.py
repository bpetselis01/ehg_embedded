"""Pure gateway logic: packet-loss tracking + board-to-laptop clock mapping. No I/O.

PURPOSE: 2 helpers per board: spot missing packets; turn the board's clock into real time.
INPUT: GapTracker.observe(seq): packet number of each arrival.
  ClockMap.update(ticks, t_host): board clock ticks (32768/s, 32-bit, wraps) + laptop time at arrival.
  e.g. observe(0), observe(1), observe(2), observe(5), observe(3)
  e.g. update(113049, 1791243068.468658)  (tick of the packet's last frame, laptop time it arrived)
OUTPUT: observe() -> list of newly missing seq numbers (e.g. 5 arrives after 2 -> [3, 4]).
  e.g. the 5 calls above return [], [], [], [3, 4], []   (3 arriving late fills its gap)
  ClockMap.to_host(ticks) -> laptop epoch seconds.
  e.g. after that update, to_host(98304) -> 1791243068.0187 (frame 0, 0.45 s before arrival)
HOW: GapTracker watches seq. ClockMap compares "board time at sampling" with "laptop time at
  receipt" and keeps the smallest difference = best estimate of how the clocks line up.
ROLE IN THESIS: GapTracker makes every loss identifiable, triggers re-requests (H2).
  ClockMap gives every sample a real timestamp for latency (H1).
BEFORE -> AFTER: Fed by gateway/gateway.py:Node.on_bytes per packet. Missing seqs -> RESEND commands
  to the firmware; mapped times -> MQTT field t_inject_host -> InfluxDB. No network/file access, so
  tests/test_tracking.py tests it directly.
"""

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
