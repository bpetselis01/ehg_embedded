// Everything the gateway does for ONE board, in the order gateway/gateway.py:Node.on_bytes does it: decode, count a bad packet, teach the clock, track gaps, ask the board for missing packets again at the right time, build the JSON and hand it to the outbox.
import EHGWire
import Foundation

public final class NodeSession {
    public let nodeId: String
    public private(set) var gaps = GapTracker()
    public private(set) var clock = ClockMap()
    public private(set) var bad = 0
    public var requestResend: (Int, Int) -> Void = { _, _ in }
    private var scheduler = ResendScheduler()
    private let outbox: Outbox

    public init(nodeId: String, outbox: Outbox) { self.nodeId = nodeId; self.outbox = outbox }

    public func onBytes(_ bytes: [UInt8], tRx: Double) {
        let p: Packet
        do { p = try Packet.parse(bytes) } catch { bad += 1; return }
        updateClock(&clock, p, tRx: tRx)
        let fresh = gaps.observe(Int(p.seq))
        for r in scheduler.due(gaps: gaps, newGaps: fresh, at: tRx) { requestResend(r.start, r.count) }
        outbox.add(nodeId: nodeId, seq: Int(p.seq), tInject: Int(p.tInject), topic: "ehg/\(nodeId)/pkt", payload: packetMessageJSON(nodeId: nodeId, p, clock: clock, tRx: tRx))
    }

    /// Ask again for packets that are still missing and whose retry time has come.
    public func poll(at t: Double) {
        for r in scheduler.due(gaps: gaps, newGaps: [], at: t) { requestResend(r.start, r.count) }
    }
}
