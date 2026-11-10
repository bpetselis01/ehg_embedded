// PURPOSE: Everything the gateway does for ONE board, in the order gateway/gateway.py:Node.on_bytes does it: decode, count a bad packet, teach the clock, track gaps, ask the board for missing packets again at the right time,
//   build the JSON and hand it to the outbox. A port; change them together.
// INPUT: onBytes(bytes, tRx) for each notification from the data characteristic; poll(at:) from a 1 s timer so a retry falls due even when no packet arrives; requestResend is set by the Bluetooth layer to write the RESEND command.
// OUTPUT: outbox.add(...) per good packet on topic ehg/<node>/pkt; requestResend(start, count) calls; counters for the status screen (received, missing, duplicates, streams, bad).
// HOW: no Bluetooth and no network in here, so it is tested with `swift test`. A bad packet is dropped, never stored; the next packet then shows the gap and the scheduler asks again (up to 5 times, 1 s apart).
// STATUS: tested with fakes. The Bluetooth and MQTT adapters that drive it are in the app target and have never run on a phone.
// ROLE IN THESIS: The phone's counterpart of Node (H1, H2).
// BEFORE -> AFTER: Before: pieces only. After: the whole per-node pipeline.
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
