// PURPOSE: What the phone needs to get its own client certificate without a laptop: (1) the Keycloak DEVICE grant (the phone shows a code, you approve it in any browser), (2) a PKCS#10 certificate request built and signed in Swift, (3) the call
//   to the enrolment API (backend/enroll). The same steps renew the certificate: run them again before it expires (the 7-day lifetime is chosen by the server).
// INPUT: the Keycloak realm URL (https://<host>/auth/realms/ehg), the public client id `ios-gateway`, the enrolment URL (https://<host>/enroll-api/enroll). OUTPUT: the certificate PEM for the key the phone made.
// HOW: the device flow is plain HTTPS (RFC 8628) through an injectable `fetch`, so it is tested with a fake server: authorization_pending keeps polling, slow_down lengthens the interval, expired_token and access_denied stop. The CSR carries only the common
//   name (= the Keycloak user name, which the server takes as the gateway id) and a P-256 key; the server vets it again (backend/enroll/csr.py) and the certificate it returns is client-authentication only. The private key never leaves the phone.
// STATUS: the CSR is verified by the repository's own Python validator (tests/test_ios_csr.py); the device flow by a fake server. NOT run against a real Keycloak from Swift, and the Keychain storage in the app has never run on a phone.
// ROLE IN THESIS: Removes the manual .p12 step of the phone gateway (docs/decisions/0013).
import Foundation
import Security

public enum EnrolmentError: Error, Equatable { case badResponse(Int), denied, expired, malformed, signing(String) }

// MARK: PKCS#10

enum DER {
    static func len(_ n: Int) -> [UInt8] { n < 128 ? [UInt8(n)] : n < 256 ? [0x81, UInt8(n)] : [0x82, UInt8(n >> 8), UInt8(n & 0xFF)] }
    static func tlv(_ tag: UInt8, _ body: [UInt8]) -> [UInt8] { [tag] + len(body.count) + body }
    static let ecPublicKeyHeader: [UInt8] = [0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x02, 0x01, 0x06, 0x08, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00]   // SPKI prefix for P-256
}

public enum CSR {
    /// A DER PKCS#10 request for `commonName`, signed with `key` (a P-256 private key, ECDSA with SHA-256).
    public static func build(commonName: String, key: SecKey) throws -> Data {
        guard let pub = SecKeyCopyPublicKey(key), let point = SecKeyCopyExternalRepresentation(pub, nil) as Data?, point.count == 65, point[0] == 0x04 else { throw EnrolmentError.signing("not a P-256 key") }
        let name = DER.tlv(0x30, DER.tlv(0x31, DER.tlv(0x30, DER.tlv(0x06, [0x55, 0x04, 0x03]) + DER.tlv(0x0C, Array(commonName.utf8)))))
        let info = DER.tlv(0x30, DER.tlv(0x02, [0x00]) + name + DER.ecPublicKeyHeader + [UInt8](point) + DER.tlv(0xA0, []))
        var err: Unmanaged<CFError>?
        guard let sig = SecKeyCreateSignature(key, .ecdsaSignatureMessageX962SHA256, Data(info) as CFData, &err) as Data? else { throw EnrolmentError.signing(String(describing: err?.takeRetainedValue())) }
        let algorithm = DER.tlv(0x30, DER.tlv(0x06, [0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x04, 0x03, 0x02]))   // ecdsa-with-SHA256
        return Data(DER.tlv(0x30, info + algorithm + DER.tlv(0x03, [0x00] + [UInt8](sig))))
    }

    public static func pem(_ der: Data) -> String {
        let b64 = der.base64EncodedString(options: [.lineLength64Characters, .endLineWithLineFeed])
        return "-----BEGIN CERTIFICATE REQUEST-----\n\(b64)\n-----END CERTIFICATE REQUEST-----\n"
    }

    public static func newKey(permanentLabel: String? = nil) throws -> SecKey {
        var attrs: [String: Any] = [kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom, kSecAttrKeySizeInBits as String: 256]
        if let label = permanentLabel { attrs[kSecAttrIsPermanent as String] = true; attrs[kSecAttrLabel as String] = label }
        var err: Unmanaged<CFError>?
        guard let key = SecKeyCreateRandomKey(attrs as CFDictionary, &err) else { throw EnrolmentError.signing(String(describing: err?.takeRetainedValue())) }
        return key
    }
}

// MARK: device grant (RFC 8628) and the enrolment call

public struct DeviceCode: Equatable { public let deviceCode: String, userCode: String, verificationURL: String, interval: Double, expiresIn: Double }

public struct Enrolment {
    public typealias Fetch = (URLRequest) async throws -> (Data, Int)
    let fetch: Fetch, sleep: (Double) async -> Void
    public init(fetch: @escaping Fetch = { r in let (d, resp) = try await URLSession.shared.data(for: r); return (d, (resp as? HTTPURLResponse)?.statusCode ?? 0) },
                sleep: @escaping (Double) async -> Void = { try? await Task.sleep(nanoseconds: UInt64($0 * 1e9)) }) { self.fetch = fetch; self.sleep = sleep }

    private func form(_ url: String, _ fields: [String: String]) -> URLRequest {
        var r = URLRequest(url: URL(string: url)!); r.httpMethod = "POST"
        r.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        r.httpBody = fields.map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-._~"))) ?? "")" }.joined(separator: "&").data(using: .utf8)
        return r
    }

    public func begin(realm: String, clientId: String, scope: String = "openid gateway:enrol") async throws -> DeviceCode {
        let (d, status) = try await fetch(form(realm + "/protocol/openid-connect/auth/device", ["client_id": clientId, "scope": scope]))
        guard status == 200, let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let dc = j["device_code"] as? String, let uc = j["user_code"] as? String,
              let url = (j["verification_uri_complete"] ?? j["verification_uri"]) as? String else { throw EnrolmentError.badResponse(status) }
        return DeviceCode(deviceCode: dc, userCode: uc, verificationURL: url, interval: (j["interval"] as? Double) ?? 5, expiresIn: (j["expires_in"] as? Double) ?? 600)
    }

    /// Polls until you approve the code in a browser; returns the access token.
    public func token(realm: String, clientId: String, code: DeviceCode) async throws -> String {
        var interval = code.interval, waited = 0.0
        while waited <= code.expiresIn {
            await sleep(interval); waited += interval
            let (d, status) = try await fetch(form(realm + "/protocol/openid-connect/token", ["grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": code.deviceCode, "client_id": clientId]))
            let j = (try? JSONSerialization.jsonObject(with: d) as? [String: Any]) ?? [:]
            if status == 200, let t = j["access_token"] as? String { return t }
            switch j["error"] as? String {
            case "authorization_pending": continue
            case "slow_down": interval += 5
            case "access_denied": throw EnrolmentError.denied
            case "expired_token": throw EnrolmentError.expired
            default: throw EnrolmentError.badResponse(status)
            }
        }
        throw EnrolmentError.expired
    }

    /// The user name in the token (the server uses it as the gateway id and the certificate's CN). Read only to name the request: the server verifies the token itself.
    public static func userName(inToken jwt: String) -> String? {
        let parts = jwt.split(separator: "."); guard parts.count == 3 else { return nil }
        var b64 = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/"); while b64.count % 4 != 0 { b64 += "=" }
        guard let d = Data(base64Encoded: b64), let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return nil }
        return j["preferred_username"] as? String
    }

    public func enrol(url: String, token: String, csrPEM: String) async throws -> String {
        var r = URLRequest(url: URL(string: url)!); r.httpMethod = "POST"
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization"); r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try JSONSerialization.data(withJSONObject: ["csr": csrPEM])
        let (d, status) = try await fetch(r)
        guard status == 200 else { throw EnrolmentError.badResponse(status) }
        guard let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let cert = j["certificate"] as? String, cert.contains("BEGIN CERTIFICATE") else { throw EnrolmentError.malformed }
        return cert
    }
}
