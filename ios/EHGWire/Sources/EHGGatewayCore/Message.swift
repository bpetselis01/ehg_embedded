// The JSON the gateway publishes to ehg/<node>/pkt for each packet, and the clock rule that goes with it.
import EHGWire
import Foundation

public func updateClock(_ clock: inout ClockMap, _ p: Packet, tRx: Double) {
    guard p.flags & WireFormat.flagRetransmission == 0 || clock.offset == nil else { return }
    let tLast = Int(p.tInject) + floorDiv((Int(p.nFrames) - 1) * WireFormat.tickHz, Int(p.fsHz))   // Python's // rounds toward minus infinity (it differs from Swift's / only when nFrames is 0)
    clock.update(ticks: tLast & 0xFFFF_FFFF, tHost: tRx)
}

func floorDiv(_ a: Int, _ b: Int) -> Int { let q = a / b; return (a % b != 0 && (a < 0) != (b < 0)) ? q - 1 : q }

public func packetMessageObject(nodeId: String, _ p: Packet, clock: ClockMap, tRx: Double) -> [String: Any] {
    [
        "node_id": nodeId, "seq": Int(p.seq), "frame_idx": Int(p.frameIdx), "t_inject": Int(p.tInject),
        "t_inject_host": clock.toHost(Int(p.tInject)) as Any? ?? NSNull(),
        "n_ch": Int(p.nCh), "fs_hz": Int(p.fsHz), "record_id": Int(p.recordId), "flags": Int(p.flags),
        "samples": p.samples.map { $0.map { Int($0) } }, "t_gw_rx": tRx,
    ]
}

public func packetMessageJSON(nodeId: String, _ p: Packet, clock: ClockMap, tRx: Double) -> String {
    let data = try! JSONSerialization.data(withJSONObject: packetMessageObject(nodeId: nodeId, p, clock: clock, tRx: tRx), options: [.sortedKeys, .withoutEscapingSlashes])
    return String(decoding: data, as: UTF8.self)
}
