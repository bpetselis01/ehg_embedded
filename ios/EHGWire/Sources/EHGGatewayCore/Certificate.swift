// Reads the notAfter date out of a DER X.509 certificate.
import Foundation

public func certificateNotAfter(der: Data) -> Date? {
    let b = [UInt8](der)
    func tlv(_ at: Int) -> (tag: UInt8, start: Int, end: Int)? {
        guard at + 2 <= b.count else { return nil }
        var i = at + 1, len = Int(b[i]); i += 1
        if len & 0x80 != 0 {
            let n = len & 0x7F
            guard n >= 1, n <= 4, i + n <= b.count else { return nil }
            len = 0; for _ in 0..<n { len = len << 8 | Int(b[i]); i += 1 }
        }
        guard i + len <= b.count else { return nil }
        return (b[at], i, i + len)
    }
    guard let cert = tlv(0), cert.tag == 0x30, let tbs = tlv(cert.start), tbs.tag == 0x30 else { return nil }
    var at = tbs.start
    if let first = tlv(at), first.tag == 0xA0 { at = first.end }                        // [0] version
    for _ in 0..<3 { guard let t = tlv(at) else { return nil }; at = t.end }             // skip serial, signature, issuer: next is the validity
    guard let validity = tlv(at), validity.tag == 0x30, let notBefore = tlv(validity.start), let notAfter = tlv(notBefore.end) else { return nil }
    let text = String(decoding: b[notAfter.start..<notAfter.end], as: UTF8.self)
    let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC")
    switch notAfter.tag {
    case 0x17: f.dateFormat = "yyMMddHHmmss'Z'"
    case 0x18: f.dateFormat = "yyyyMMddHHmmss'Z'"
    default: return nil
    }
    return f.date(from: text)
}
