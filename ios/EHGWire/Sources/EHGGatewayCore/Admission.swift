// Decides which Bluetooth devices the gateway may treat as a node.
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
