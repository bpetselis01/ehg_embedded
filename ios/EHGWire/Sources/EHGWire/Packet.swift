// PURPOSE: EHG wire format v2 (v1 + CRC-16 trailer) for the iPhone gateway. Mirror of gateway/packet.py and firmware/ehg_node/src/ehg_packet.h. Change all three together.
// INPUT: Packet.parse(bytes): one BLE notification, little-endian. 20-byte header, then nFrames x nCh samples (Int16, or 3-byte signed when flags has flag24Bit), then a 2-byte CRC-16/CCITT-FALSE of everything before it.
//   e.g. 3 channels x 10 frames = 20 + 60 + 2 = 82 bytes (24-bit: 20 + 90 + 2 = 112).
// OUTPUT: a Packet value (version, nCh, fsHz, seq, frameIdx, tInject, nFrames, flags, recordId, samples[frame][channel] as Int32), or a PacketError. pack() is the exact inverse (used by the tests and a future simulator).
//   The checks run in the same order as gateway/packet.py: too short, unknown version, wrong length, bad CRC. A damaged packet is refused, never stored (H2/H4); samples stay raw ADC counts, unscaled.
// HOW: Fixed byte offsets (0 version, 1 nCh, 2-3 fsHz, 4-7 seq, 8-11 frameIdx, 12-15 tInject, 16 nFrames, 17 flags, 18-19 recordId), no padding. Verified against tests/fixtures/packet_vectors.json, generated from gateway/packet.py.
// STATUS: compiled and tested with `swift test` (command line only). NEVER run on a phone, with CoreBluetooth, or in Xcode.
// ROLE IN THESIS: Keeps the phone's decode bit-exact with the laptop's (decision 0015, hypothesis H4).
// BEFORE -> AFTER: Before: the wire format existed in C and Python. After: also Swift, guarded by shared golden vectors.

public enum WireFormat {
    public static let version: UInt8 = 2
    public static let headerSize = 20
    public static let crcSize = 2
    public static let flagRetransmission: UInt8 = 0x01
    public static let flag24Bit: UInt8 = 0x02
    public static let tickHz = 32768
    public static let crcInitial: UInt16 = 0xFFFF
    public static let crcPolynomial: UInt16 = 0x1021
}

public enum PacketError: Error, Equatable {
    case tooShort(Int)
    case unknownVersion(Int)
    case wrongLength(expected: Int, got: Int)
    case crcMismatch

    /// The reason as a short name, shared with tests/fixtures/packet_vectors.json.
    public var kind: String {
        switch self {
        case .tooShort: return "too_short"
        case .unknownVersion: return "unknown_version"
        case .wrongLength: return "wrong_length"
        case .crcMismatch: return "crc_mismatch"
        }
    }
}

/// CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection): the same as Zephyr's crc16_itu_t(0xFFFF, ...) and Python's binascii.crc_hqx(b, 0xFFFF).
public func crc16(_ bytes: [UInt8]) -> UInt16 {
    var crc = WireFormat.crcInitial
    for byte in bytes {
        crc ^= UInt16(byte) << 8
        for _ in 0..<8 {
            crc = (crc & 0x8000) != 0 ? (crc << 1) ^ WireFormat.crcPolynomial : crc << 1
        }
    }
    return crc
}

public struct Packet: Equatable {
    public var version: UInt8
    public var nCh: UInt8
    public var fsHz: UInt16
    public var seq: UInt32
    public var frameIdx: UInt32
    public var tInject: UInt32
    public var nFrames: UInt8
    public var flags: UInt8
    public var recordId: UInt16
    public var samples: [[Int32]]

    public init(version: UInt8 = WireFormat.version, nCh: UInt8, fsHz: UInt16, seq: UInt32, frameIdx: UInt32, tInject: UInt32, nFrames: UInt8, flags: UInt8, recordId: UInt16, samples: [[Int32]]) {
        self.version = version; self.nCh = nCh; self.fsHz = fsHz; self.seq = seq; self.frameIdx = frameIdx
        self.tInject = tInject; self.nFrames = nFrames; self.flags = flags; self.recordId = recordId; self.samples = samples
    }

    /// 2 bytes per sample, or 3 when flag24Bit is set.
    public static func sampleBytes(flags: UInt8) -> Int { flags & WireFormat.flag24Bit != 0 ? 3 : 2 }

    private static func le(_ b: [UInt8], _ at: Int, _ n: Int) -> UInt64 {
        var v: UInt64 = 0
        for i in 0..<n { v |= UInt64(b[at + i]) << (8 * UInt64(i)) }
        return v
    }

    /// Bytes to Packet. Throws PacketError for anything wrong; nothing is partially returned.
    public static func parse(_ b: [UInt8]) throws -> Packet {
        guard b.count >= WireFormat.headerSize else { throw PacketError.tooShort(b.count) }
        guard b[0] == WireFormat.version else { throw PacketError.unknownVersion(Int(b[0])) }
        let nCh = Int(b[1]), nFrames = Int(b[16]), flags = b[17], w = sampleBytes(flags: flags), n = nCh * nFrames
        let expected = WireFormat.headerSize + w * n + WireFormat.crcSize
        guard b.count == expected else { throw PacketError.wrongLength(expected: expected, got: b.count) }
        guard UInt16(le(b, b.count - 2, 2)) == crc16(Array(b[0..<(b.count - 2)])) else { throw PacketError.crcMismatch }
        var flat = [Int32]()
        flat.reserveCapacity(n)
        for i in 0..<n {
            let at = WireFormat.headerSize + i * w
            let raw = le(b, at, w)
            let bits = UInt64(8 * w)
            let signed = (raw & (1 << (bits - 1))) != 0 ? Int64(raw) - Int64(1 << bits) : Int64(raw)
            flat.append(Int32(signed))
        }
        let frames = (0..<nFrames).map { Array(flat[($0 * nCh)..<(($0 + 1) * nCh)]) }
        return Packet(version: b[0], nCh: b[1], fsHz: UInt16(le(b, 2, 2)), seq: UInt32(le(b, 4, 4)), frameIdx: UInt32(le(b, 8, 4)), tInject: UInt32(le(b, 12, 4)),
                      nFrames: b[16], flags: b[17], recordId: UInt16(le(b, 18, 2)), samples: frames)
    }

    /// Packet to bytes: the exact inverse of parse().
    public func pack() -> [UInt8] {
        var out = [UInt8]()
        func put(_ v: UInt64, _ n: Int) { for i in 0..<n { out.append(UInt8((v >> (8 * UInt64(i))) & 0xFF)) } }
        put(UInt64(version), 1); put(UInt64(nCh), 1); put(UInt64(fsHz), 2); put(UInt64(seq), 4); put(UInt64(frameIdx), 4); put(UInt64(tInject), 4)
        put(UInt64(nFrames), 1); put(UInt64(flags), 1); put(UInt64(recordId), 2)
        let w = Packet.sampleBytes(flags: flags)
        for frame in samples { for v in frame { put(UInt64(bitPattern: Int64(v)) & ((1 << UInt64(8 * w)) - 1), w) } }
        put(UInt64(crc16(out)), 2)
        return out
    }
}
