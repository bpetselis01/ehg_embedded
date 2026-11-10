// PURPOSE: Decides which Bluetooth devices the gateway may treat as a node. Without it, ANY device that advertises a name starting "EHG-" would be adopted, its bytes published as that node's data, and its name used as an MQTT topic.
// INPUT: the advertised name and the iOS peripheral identifier of each discovered device; the configured allowlist of node ids.
// OUTPUT: accept, or reject with the reason (shown in the log). A name must be a plain node id (EHG- then letters, digits, _ or -: no "/", "+", "#", spaces), must be on the allowlist, and may be claimed by ONE peripheral only: a second device
//   advertising an allowed name is refused, so an imitator cannot take over a node that is already connected.
// HOW: pure value logic, tested with `swift test`. An EMPTY allowlist accepts nothing (the app asks for the node ids before it starts). This is identification, not authentication: a device that copies a name while the real board is off can still be adopted;
//   the link-level protection is the secure board build with LESC passkey pairing (docs/security.md). Stop/Start resets the claims (a re-flashed board may get a new peripheral identifier).
// STATUS: tested with `swift test`; the Bluetooth code that calls it has never run on a phone.
// ROLE IN THESIS: Closes the "anyone can pose as a node" gap of the phone gateway (security review, 2026-11-04).
import Foundation

public struct NodeAdmission {
    public enum Decision: Equatable { case accept, reject(String) }
    public var allowed: Set<String>
    private var owner: [String: UUID] = [:]

    public init(allowed: Set<String>) { self.allowed = allowed }

    public static func isValidNodeId(_ s: String) -> Bool {
        guard s.hasPrefix("EHG-"), s.utf8.count >= 5, s.utf8.count <= 64 else { return false }
        return s.utf8.dropFirst(4).allSatisfy { ($0 >= 48 && $0 <= 57) || ($0 >= 65 && $0 <= 90) || ($0 >= 97 && $0 <= 122) || $0 == 95 || $0 == 45 }
    }

    public mutating func admit(name: String?, peripheral: UUID) -> Decision {
        guard let name, Self.isValidNodeId(name) else { return .reject("not a plain node name") }
        guard !allowed.isEmpty else { return .reject("no allowed nodes configured") }
        guard allowed.contains(name) else { return .reject("\(name) is not on the allowlist") }
        if let o = owner[name], o != peripheral { return .reject("another device already claims \(name)") }
        owner[name] = peripheral
        return .accept
    }
}
