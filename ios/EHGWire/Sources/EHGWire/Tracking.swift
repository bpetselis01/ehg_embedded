// PURPOSE: Packet-loss tracking and board-to-phone clock mapping for the iPhone gateway. A line-for-line port of gateway/tracking.py (GapTracker, ClockMap); change them together.
// INPUT: GapTracker.observe(seq): the sequence number of each arriving packet. ClockMap.update(ticks, tHost): the board's tick counter (32768 per second, 32 bits, wraps) and the phone's clock when the packet arrived.
// OUTPUT: observe() returns the sequence numbers newly found missing (empty for none), in ascending order; ClockMap.toHost(ticks) returns phone time in Unix seconds, or nil before the first update.
//   e.g. observe(0), observe(1), observe(2), observe(5), observe(3) return [], [], [], [3, 4], [].
// HOW: Same rules as Python: a late arrival fills a hole; a repeat is counted and ignored; a sequence number more than 1000 below the last one starts a new stream (the node rebooted) and forgets the old holes.
//   The clock offset is the smallest (phone time - board time) seen, which bounds the true offset from above. The tick counter's 32-bit roll-over is undone relative to the last tick.
//   KNOWN LIMIT, shared with Python on purpose (changing it would break "every loss is identified" after a long outage): a huge forward jump returns a huge list of missing numbers. The app must cap what it asks the board to re-send (the board keeps only 256 packets).
//   Sequence numbers are Int (64-bit) so the arithmetic matches Python's unbounded integers for every 32-bit value.
// STATUS: checked against tests/fixtures/packet_vectors.json ("tracking"), generated from the Python classes. Never run on a phone.
// ROLE IN THESIS: Makes every loss identifiable on the phone as on the Mac (H2) and gives samples real timestamps (H1).
// BEFORE -> AFTER: Before: only the packet decoder existed in Swift. After: the loss and clock logic too.

public struct GapTracker {
    public static let newStreamBackstep = 1000

    public private(set) var last: Int?
    public private(set) var missing = Set<Int>()
    public private(set) var received = 0
    public private(set) var duplicates = 0
    public private(set) var streams = 0
    private var seenRecent = Set<Int>()

    public init() {}

    /// Records one arrival and returns the sequence numbers newly found missing.
    @discardableResult
    public mutating func observe(_ seq: Int) -> [Int] {
        guard let previous = last, seq >= previous - GapTracker.newStreamBackstep else {
            last = seq; missing = []; seenRecent = [seq]
            streams += 1; received += 1
            return []
        }
        if seenRecent.contains(seq) { duplicates += 1; return [] }
        seenRecent.insert(seq)
        received += 1
        if seq <= previous {  // a late arrival (for example a re-send) fills a hole
            missing.remove(seq)
            return []
        }
        let gap = Array((previous + 1)..<seq)
        missing.formUnion(gap)
        last = seq
        return gap
    }
}

public struct ClockMap {
    private static let wrap = 1 << 32

    public let tickHz: Double
    public private(set) var offset: Double?
    private var lastTick: Int?

    public init(tickHz: Int = WireFormat.tickHz) { self.tickHz = Double(tickHz) }

    /// The board's 32-bit tick counter rolls over about every 36 hours; undo that relative to the last tick seen.
    private func unwrap(_ ticks: Int) -> Int {
        guard let last = lastTick else { return ticks }
        var t = (last - last % ClockMap.wrap) + ticks
        if t - last > ClockMap.wrap / 2 { t -= ClockMap.wrap } else if last - t > ClockMap.wrap / 2 { t += ClockMap.wrap }
        return t
    }

    /// Learns from one (board time, phone arrival time) pair and keeps the smallest offset.
    public mutating func update(ticks: Int, tHost: Double) {
        let t = unwrap(ticks)
        lastTick = lastTick.map { max($0, t) } ?? t
        let off = tHost - Double(t) / tickHz
        offset = offset.map { min($0, off) } ?? off
    }

    /// Board ticks to phone time in Unix seconds (nil until the first update).
    public func toHost(_ ticks: Int) -> Double? {
        offset.map { Double(unwrap(ticks)) / tickHz + $0 }
    }
}
