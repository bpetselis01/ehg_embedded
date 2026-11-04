// PURPOSE: Packet-loss tracking and board-to-phone clock mapping for the iPhone gateway. A line-for-line port of gateway/tracking.py (GapTracker, ClockMap, ResendScheduler); change them together.
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

/// How long to wait before asking again for a packet that is still missing, and how many requests in all (gateway/tracking.py: RETRY_S, MAX_TRIES).
public enum ResendPolicy {
    public static let retrySeconds = 1.0
    public static let maxTries = 5
}

/// Groups missing sequence numbers into (start, count) runs, one RESEND command each: [2,3,4,9] becomes [(2,3),(9,1)]. A run is at most 0xFFFF long (the command's count is 16 bits).
public func resendRanges(_ seqs: [Int]) -> [(start: Int, count: Int)] {
    var out: [(start: Int, count: Int)] = []
    for s in seqs {
        if let last = out.last, last.start + last.count == s, last.count < 0xFFFF {
            out[out.count - 1].count += 1
        } else {
            out.append((s, 1))
        }
    }
    return out
}

/// Decides WHEN to re-ask the board for missing packets: new gaps at once, still-missing ones again every `retrySeconds`, at most `maxTries` requests in all.
/// The phone app owns the timer and the Bluetooth write; this only says what to ask for at a given time. Same behaviour as Python's ResendScheduler.
public struct ResendScheduler {
    private var asked: [Int: (time: Double, tries: Int)] = [:]
    private var stream = 0

    public init() {}

    /// The (start, count) runs to request at time `t`. Call after `gaps.observe(...)` with its return value as `newGaps`.
    public mutating func due(gaps: GapTracker, newGaps: [Int], at t: Double) -> [(start: Int, count: Int)] {
        if gaps.streams != stream {  // the board restarted its numbering: old requests mean nothing
            stream = gaps.streams
            asked = [:]
        }
        var due = newGaps
        for (s, a) in asked {
            if !gaps.missing.contains(s) {
                asked[s] = nil  // arrived
            } else if t - a.time >= ResendPolicy.retrySeconds && a.tries < ResendPolicy.maxTries {
                due.append(s)
            }
        }
        for s in due { asked[s] = (t, (asked[s]?.tries ?? 0) + 1) }
        return resendRanges(due.sorted())
    }
}
