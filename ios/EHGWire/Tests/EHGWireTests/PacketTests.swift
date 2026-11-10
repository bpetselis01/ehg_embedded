// PURPOSE: Decode every golden vector from tests/fixtures/packet_vectors.json (generated from gateway/packet.py) and demand identical values, a byte-identical re-encode, and the same refusal reason for each bad packet.
// INPUT: the JSON file, found relative to this source file. OUTPUT: pass/fail. Run: cd ios/EHGWire && swift test   (command line only: no Xcode project, simulator or phone).
import XCTest
@testable import EHGWire

private struct Vectors: Decodable {
    struct Valid: Decodable {
        struct P: Decodable {
            let version: UInt8, n_ch: UInt8, fs_hz: UInt16, seq: UInt32, frame_idx: UInt32, t_inject: UInt32, n_frames: UInt8, flags: UInt8, record_id: UInt16
            let samples: [[Int32]]
        }
        let name: String, hex: String, packet: P
    }
    struct Invalid: Decodable { let name: String, hex: String, error: String }
    struct Tracking: Decodable {
        struct Gap: Decodable {
            struct Final: Decodable { let last: Int, missing: [Int], received: Int, duplicates: Int, streams: Int }
            let name: String, seqs: [Int], returns: [[Int]], final: Final
        }
        struct Clock: Decodable {
            struct Query: Decodable { let ticks: Int, host: Double }
            let name: String, updates: [[Double]], offset: Double, queries: [Query]
        }
        struct Resend: Decodable {
            let name: String, steps: [[Double?]], requests: [[[Int]]]
        }
        let gap: [Gap], clock: [Clock], resend: [Resend]
    }
    let valid: [Valid], invalid: [Invalid], tracking: Tracking
}

private func bytes(_ hex: String) -> [UInt8] {
    var out = [UInt8](), i = hex.startIndex
    while i < hex.endIndex {
        let j = hex.index(i, offsetBy: 2)
        out.append(UInt8(hex[i..<j], radix: 16)!)
        i = j
    }
    return out
}

private func loadVectors() throws -> Vectors {
    let url = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .appendingPathComponent("tests/fixtures/packet_vectors.json")
    return try JSONDecoder().decode(Vectors.self, from: Data(contentsOf: url))
}

final class PacketTests: XCTestCase {
    func testEveryValidVectorDecodesToTheSameValuesAndReEncodesToTheSameBytes() throws {
        let v = try loadVectors()
        XCTAssertGreaterThanOrEqual(v.valid.count, 8)
        for c in v.valid {
            let raw = bytes(c.hex)
            let p = try Packet.parse(raw)
            XCTAssertEqual(p.version, c.packet.version, c.name)
            XCTAssertEqual(p.nCh, c.packet.n_ch, c.name)
            XCTAssertEqual(p.fsHz, c.packet.fs_hz, c.name)
            XCTAssertEqual(p.seq, c.packet.seq, c.name)
            XCTAssertEqual(p.frameIdx, c.packet.frame_idx, c.name)
            XCTAssertEqual(p.tInject, c.packet.t_inject, c.name)
            XCTAssertEqual(p.nFrames, c.packet.n_frames, c.name)
            XCTAssertEqual(p.flags, c.packet.flags, c.name)
            XCTAssertEqual(p.recordId, c.packet.record_id, c.name)
            XCTAssertEqual(p.samples, c.packet.samples, "\(c.name): samples must be bit-exact raw counts")
            XCTAssertEqual(p.pack(), raw, "\(c.name): re-encoding must give the original bytes")
        }
    }

    func testEveryInvalidVectorIsRefusedForTheSameReason() throws {
        let v = try loadVectors()
        XCTAssertGreaterThanOrEqual(v.invalid.count, 7)
        for c in v.invalid {
            XCTAssertThrowsError(try Packet.parse(bytes(c.hex)), c.name) { error in
                XCTAssertEqual((error as? PacketError)?.kind, c.error, c.name)
            }
        }
    }

    func testExtremeSampleValuesSurviveBothWidths() throws {
        let p16 = Packet(nCh: 2, fsHz: 20, seq: 1, frameIdx: 0, tInject: 0, nFrames: 1, flags: 0, recordId: 0, samples: [[-32768, 32767]])
        XCTAssertEqual(try Packet.parse(p16.pack()).samples, [[-32768, 32767]])
        let p24 = Packet(nCh: 2, fsHz: 20, seq: 1, frameIdx: 0, tInject: 0, nFrames: 1, flags: WireFormat.flag24Bit, recordId: 0, samples: [[-8388608, 8388607]])
        XCTAssertEqual(try Packet.parse(p24.pack()).samples, [[-8388608, 8388607]])
    }

    func testCrcMatchesTheKnownCheckValueForCcittFalse() {
        XCTAssertEqual(crc16(Array("123456789".utf8)), 0x29B1, "the standard check value of CRC-16/CCITT-FALSE")
    }

    func testEveryTruncationOfAValidPacketIsRefused() throws {
        let raw = bytes(try loadVectors().valid[0].hex)
        for cut in 0..<raw.count { XCTAssertThrowsError(try Packet.parse(Array(raw[0..<cut]))) }
    }

    func testEverySingleBitFlipInAValidPacketIsRefused() throws {
        let raw = bytes(try loadVectors().valid[0].hex)
        for i in 0..<raw.count {
            for bit in 0..<8 {
                var damaged = raw
                damaged[i] ^= UInt8(1 << bit)
                XCTAssertThrowsError(try Packet.parse(damaged), "flip byte \(i) bit \(bit)")
            }
        }
    }

    func testGapTrackerReturnsExactlyWhatThePythonTrackerReturned() throws {
        let cases = try loadVectors().tracking.gap
        XCTAssertGreaterThanOrEqual(cases.count, 9)
        for c in cases {
            var t = GapTracker()
            let got = c.seqs.map { t.observe($0) }
            XCTAssertEqual(got, c.returns, c.name)
            XCTAssertEqual(t.last, c.final.last, c.name)
            XCTAssertEqual(t.missing.sorted(), c.final.missing, c.name)
            XCTAssertEqual(t.received, c.final.received, c.name)
            XCTAssertEqual(t.duplicates, c.final.duplicates, c.name)
            XCTAssertEqual(t.streams, c.final.streams, c.name)
        }
    }

    func testResendSchedulerAsksExactlyWhenPythonDid() throws {
        let cases = try loadVectors().tracking.resend
        XCTAssertGreaterThanOrEqual(cases.count, 7)
        for c in cases {
            var gaps = GapTracker(), scheduler = ResendScheduler()
            for (i, step) in c.steps.enumerated() {
                let new = step[1].map { gaps.observe(Int($0)) } ?? []
                let asked = scheduler.due(gaps: gaps, newGaps: new, at: try XCTUnwrap(step[0]))
                XCTAssertEqual(asked.map { [$0.start, $0.count] }, c.requests[i], "\(c.name) step \(i)")
            }
        }
    }

    func testClockMapGivesTheSameHostTimesAsPython() throws {
        let cases = try loadVectors().tracking.clock
        XCTAssertGreaterThanOrEqual(cases.count, 4)
        for c in cases {
            var m = ClockMap()
            XCTAssertNil(m.toHost(0), "\(c.name): no offset before the first update")
            for u in c.updates { m.update(ticks: Int(u[0]), tHost: u[1]) }
            XCTAssertEqual(try XCTUnwrap(m.offset), c.offset, accuracy: 1e-9, c.name)
            for q in c.queries { XCTAssertEqual(try XCTUnwrap(m.toHost(q.ticks)), q.host, accuracy: 1e-9, "\(c.name) ticks \(q.ticks)") }
        }
    }

    func testTheSpecExampleFromTheDocstring() {
        var t = GapTracker()
        XCTAssertEqual([0, 1, 2, 5, 3].map { t.observe($0) }, [[], [], [], [3, 4], []])
    }
}
