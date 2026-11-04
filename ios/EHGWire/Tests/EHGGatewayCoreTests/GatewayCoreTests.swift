import EHGWire
import Foundation
import XCTest
@testable import EHGGatewayCore

private struct Vectors: Decodable {
    struct Valid: Decodable { let name: String; let hex: String }
    struct Message: Decodable { let name: String; let node_id: String; let t_rx: Double }
    let valid: [Valid]
}

private func vectorsURL() -> URL {
    URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("tests/fixtures/packet_vectors.json")
}

private func hexBytes(_ s: String) -> [UInt8] {
    var out = [UInt8](), i = s.startIndex
    while i < s.endIndex { let j = s.index(i, offsetBy: 2); out.append(UInt8(s[i..<j], radix: 16)!); i = j }
    return out
}

/// Records what the outbox hands over; tokens are consecutive integers.
final class RecordingTransport: OutboxTransport {
    var sent: [(topic: String, payload: String, token: Int)] = []
    var accept = true
    private var next = 1
    func send(topic: String, payload: String) -> Int? {
        guard accept else { return nil }
        defer { next += 1 }
        sent.append((topic, payload, next))
        return next
    }
}

private func tempPath() -> String { NSTemporaryDirectory() + "outbox-\(UUID().uuidString).db" }
private func json(_ s: String) -> [String: Any] { try! JSONSerialization.jsonObject(with: Data(s.utf8)) as! [String: Any] }

final class MessageTests: XCTestCase {
    func testMessagesMatchTheJSONThePythonGatewayPublishes() throws {
        let raw = try JSONSerialization.jsonObject(with: Data(contentsOf: vectorsURL())) as! [String: Any]
        let tracking = (raw["messages"] as! [[String: Any]])
        XCTAssertGreaterThanOrEqual(tracking.count, 5)
        for m in tracking {
            let name = m["name"] as! String, tRx = m["t_rx"] as! Double
            let hex = ((raw["valid"] as! [[String: Any]]).first { ($0["name"] as! String) == name }!["hex"] as! String)
            let p = try Packet.parse(hexBytes(hex))
            var clock = ClockMap()
            updateClock(&clock, p, tRx: tRx)
            let got = json(packetMessageJSON(nodeId: "EHG-TEST", p, clock: clock, tRx: tRx))
            let want = m["message"] as! [String: Any]
            XCTAssertEqual(Set(got.keys), Set(want.keys), name)
            for (k, v) in want {
                if let d = v as? Double, let g = got[k] as? Double { XCTAssertEqual(g, d, accuracy: 1e-6, "\(name).\(k)") }
                else { XCTAssertEqual("\(got[k]!)", "\(v)", "\(name).\(k)") }
            }
        }
    }

    func testAResendThatIsTheFirstPacketStillSeedsTheClock() throws {
        let p = try Packet.parse(hexBytes(try JSONDecoder().decode(Vectors.self, from: Data(contentsOf: vectorsURL())).valid[0].hex))
        var q = p; q.flags |= WireFormat.flagRetransmission
        var clock = ClockMap()
        updateClock(&clock, q, tRx: 1000)
        XCTAssertNotNil(clock.offset, "otherwise t_inject_host would be null for the first packet")
        let before = clock.offset!
        updateClock(&clock, q, tRx: 5000)     // a later resend must NOT move the offset
        XCTAssertEqual(clock.offset, before)
    }
}

final class OutboxTests: XCTestCase {
    private func make(_ t: RecordingTransport, path: String? = nil, maxBytes: Int = 512 << 20, maxInflight: Int = 100, now: @escaping () -> Double = { 1000 }) throws -> (Outbox, String) {
        let p = path ?? tempPath()
        return (try Outbox(path: p, transport: t, maxBytes: maxBytes, maxInflight: maxInflight, clock: now, log: { _ in }), p)
    }

    func testNothingIsSentWhileDisconnectedAndEverythingGoesOutOldestFirstMarkedReplayed() throws {
        let t = RecordingTransport(); let (box, _) = try make(t)
        for s in 0..<3 { box.add(nodeId: "N", seq: s, tInject: s, topic: "ehg/N/pkt", payload: "{\"seq\": \(s)}") }
        XCTAssertTrue(t.sent.isEmpty); XCTAssertEqual(box.pending(), 3)
        box.setConnected(true)
        XCTAssertEqual(t.sent.map { json($0.payload)["seq"] as! Int }, [0, 1, 2])
        XCTAssertTrue(t.sent.allSatisfy { json($0.payload)["replayed"] as? Bool == true && json($0.payload)["t_mqtt_pub"] != nil })
    }

    func testAMessageAddedWhileConnectedIsLiveNotReplayedAndIsDeletedOnlyWhenAcknowledged() throws {
        let t = RecordingTransport(); let (box, _) = try make(t)
        box.setConnected(true)
        box.add(nodeId: "N", seq: 7, tInject: 70, topic: "ehg/N/pkt", payload: "{\"seq\": 7}")
        XCTAssertNil(json(t.sent[0].payload)["replayed"]); XCTAssertEqual(box.pending(), 1, "still on disk until the PUBACK")
        box.acked(token: t.sent[0].token)
        XCTAssertEqual(box.pending(), 0)
        box.acked(token: 999); XCTAssertEqual(box.staleAcks, 1)
    }

    func testTheInflightLimitHoldsAndEachAckReleasesTheNextRow() throws {
        let t = RecordingTransport(); let (box, _) = try make(t, maxInflight: 2)
        box.setConnected(true)
        for s in 0..<5 { box.add(nodeId: "N", seq: s, tInject: s, topic: "x", payload: "{\"seq\": \(s)}") }
        XCTAssertEqual(t.sent.count, 2)
        box.acked(token: t.sent[0].token); XCTAssertEqual(t.sent.count, 3)
        XCTAssertEqual(t.sent.map { json($0.payload)["seq"] as! Int }, [0, 1, 2])
    }

    func testADisconnectKeepsEveryRowAndTheyAreSentAgainAfterTheNextConnect() throws {
        let t = RecordingTransport(); let (box, _) = try make(t)
        box.setConnected(true)
        box.add(nodeId: "N", seq: 1, tInject: 1, topic: "x", payload: "{\"seq\": 1}")
        box.setConnected(false); box.setConnected(true)
        XCTAssertEqual(t.sent.count, 2, "a duplicate after a lost PUBACK is harmless; the cloud keys on node, seq, t_inject")
        XCTAssertEqual(json(t.sent[1].payload)["replayed"] as? Bool, true)
    }

    func testRowsSurviveARestart() throws {
        let t1 = RecordingTransport(); let (a, path) = try make(t1)
        for s in 0..<3 { a.add(nodeId: "N", seq: s, tInject: s, topic: "x", payload: "{\"seq\": \(s)}") }
        let t2 = RecordingTransport(); let (b, _) = try make(t2, path: path)
        XCTAssertEqual(b.pending(), 3)
        b.setConnected(true)
        XCTAssertEqual(t2.sent.count, 3)
    }

    func testTheSizeCapDropsTheOldestRowsAndSaysSo() throws {
        let t = RecordingTransport(); var logged: [String] = []
        let box = try Outbox(path: tempPath(), transport: t, maxBytes: 64 << 10, clock: { 1 }, log: { logged.append($0) })
        let big = "{\"x\": \"" + String(repeating: "a", count: 2000) + "\"}"
        for s in 0..<200 { box.add(nodeId: "N", seq: s, tInject: s, topic: "x", payload: big) }
        XCTAssertGreaterThan(box.dropped, 0)
        XCTAssertTrue(logged.contains { $0.contains("FULL") && $0.contains("LOST") })
        box.setConnected(true)
        XCTAssertEqual(json(t.sent[0].payload)["x"] as? String, String(repeating: "a", count: 2000))
        XCTAssertLessThan(box.pending(), 200)
    }

    func testATransportThatRefusesLeavesTheRowForTheNextConnect() throws {
        let t = RecordingTransport(); t.accept = false; let (box, _) = try make(t)
        box.setConnected(true)
        box.add(nodeId: "N", seq: 1, tInject: 1, topic: "x", payload: "{\"seq\": 1}")
        XCTAssertEqual(box.pending(), 1); XCTAssertTrue(t.sent.isEmpty)
        t.accept = true; box.setConnected(false); box.setConnected(true)
        XCTAssertEqual(t.sent.count, 1)
    }
}

final class NodeSessionTests: XCTestCase {
    private func packet(_ seq: UInt32, flags: UInt8 = 0) -> [UInt8] {
        Packet(nCh: 1, fsHz: 20, seq: seq, frameIdx: seq * 2, tInject: seq * 32768, nFrames: 2, flags: flags, recordId: 0, samples: [[1], [2]]).pack()
    }

    func testAGapIsRequestedAtOnceAndAgainAfterOneSecondUntilItArrives() throws {
        let t = RecordingTransport()
        let box = try Outbox(path: tempPath(), transport: t, clock: { 1 }, log: { _ in })
        let s = NodeSession(nodeId: "N", outbox: box); var asked: [[Int]] = []
        s.requestResend = { asked.append([$0, $1]) }
        s.onBytes(packet(0), tRx: 10.0); s.onBytes(packet(3), tRx: 10.1)
        XCTAssertEqual(asked, [[1, 2]])
        s.poll(at: 10.5); XCTAssertEqual(asked.count, 1, "not before one second")
        s.poll(at: 11.2); XCTAssertEqual(asked, [[1, 2], [1, 2]])
        s.onBytes(packet(1, flags: 1), tRx: 11.3); s.onBytes(packet(2, flags: 1), tRx: 11.3)
        s.poll(at: 20); XCTAssertEqual(asked.count, 2, "nothing is missing any more")
        XCTAssertEqual(box.pending(), 4)
    }

    func testABadPacketIsCountedAndNeverStored() throws {
        let t = RecordingTransport()
        let box = try Outbox(path: tempPath(), transport: t, clock: { 1 }, log: { _ in })
        let s = NodeSession(nodeId: "N", outbox: box)
        var raw = packet(0); raw[25] ^= 0x01
        s.onBytes(raw, tRx: 1); s.onBytes([1, 2, 3], tRx: 1)
        XCTAssertEqual(s.bad, 2); XCTAssertEqual(box.pending(), 0)
    }

    func testThePublishedTopicAndFieldsAreThePythonGatewaysAndTheJSONIsSpliceable() throws {
        let t = RecordingTransport()
        let box = try Outbox(path: tempPath(), transport: t, clock: { 5 }, log: { _ in })
        let s = NodeSession(nodeId: "EHG-93C0", outbox: box)
        box.setConnected(true)
        s.onBytes(packet(4), tRx: 100)
        XCTAssertEqual(t.sent[0].topic, "ehg/EHG-93C0/pkt")
        let m = json(t.sent[0].payload)
        XCTAssertEqual(m["node_id"] as? String, "EHG-93C0"); XCTAssertEqual(m["seq"] as? Int, 4); XCTAssertEqual(m["t_mqtt_pub"] as? Double, 5)
        XCTAssertTrue(t.sent[0].payload.hasSuffix("}") && t.sent[0].payload.contains("\"t_gw_rx\":100"))
    }
}

final class CertificateTests: XCTestCase {
    // `openssl req -x509 ... -not_after 20271231235959Z` (generated 2026-11-04, throwaway key)
    private let pem = """
-----BEGIN CERTIFICATE-----
MIIBezCCASGgAwIBAgIUDbBDu/1x9f4iYaMGX1b7FzIbsVAwCgYIKoZIzj0EAwIw
EzERMA8GA1UEAwwIZWhnLXRlc3QwHhcNMjYwMTAxMDAwMDAwWhcNMjcxMjMxMjM1
OTU5WjATMREwDwYDVQQDDAhlaGctdGVzdDBZMBMGByqGSM49AgEGCCqGSM49AwEH
A0IABAb9oj8n+1tOp8tGYgG8dIssRqfUccgnscz4N5WAxcu5+/iM6XyKR9BSuMR+
Uq+KmBKYlVwslKJnMEbhfrHsmh6jUzBRMB0GA1UdDgQWBBTrWn7r9LIMM29WlHgL
+sXWsAywNTAfBgNVHSMEGDAWgBTrWn7r9LIMM29WlHgL+sXWsAywNTAPBgNVHRMB
Af8EBTADAQH/MAoGCCqGSM49BAMCA0gAMEUCIQDBzUQ8gu8agV3yF30LQMSipC8J
mrWULvbSGeD8dhcx5QIgF3SnINPETiSNmfbjJb5nBYTkDUA5zE/kcqhoMJX6ZZ0=
-----END CERTIFICATE-----
"""

    func testReadsTheExpiryOfARealCertificate() throws {
        let b64 = pem.components(separatedBy: "\n").filter { !$0.hasPrefix("-----") }.joined()
        let date = try XCTUnwrap(certificateNotAfter(der: try XCTUnwrap(Data(base64Encoded: b64))))
        XCTAssertEqual(date, ISO8601DateFormatter().date(from: "2027-12-31T23:59:59Z"))
    }

    func testGarbageIsNilNotAGuess() {
        XCTAssertNil(certificateNotAfter(der: Data([0x30, 0x03, 0x01, 0x02, 0x03])))
        XCTAssertNil(certificateNotAfter(der: Data()))
        XCTAssertNil(certificateNotAfter(der: Data(repeating: 0xFF, count: 40)))
    }
}
