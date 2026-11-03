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
    let valid: [Valid], invalid: [Invalid]
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
}
