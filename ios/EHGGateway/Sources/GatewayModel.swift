// PURPOSE: Ties the Bluetooth link, the per-node sessions, the outbox and the MQTT client together and publishes the numbers the screen shows. Also holds the settings and the client identity.
// INPUT: settings (broker host, gateway id = the certificate's CN) and a client certificate in PKCS#12 form (made by tools/pki.sh and `openssl pkcs12 -export`, then imported through the Files picker; kept in the Keychain).
//   There is no in-app enrolment yet: the Keycloak device-grant flow of docs/decisions/0013 is not implemented, and neither is the certificate renewal.
// OUTPUT: @Published status for ContentView; a log (exportable) with every line that matters. The online/offline status topic ehg/_gw/<id>/status is retained "online" on connect, "offline" as the last will.
// HOW: the CA that signed the broker certificate is bundled as ca-chain.crt (copy backend/pki/ca-chain.crt into Sources before building: it is public, the keys are not). The outbox lives in Application Support with file protection
//   "complete until first user authentication". A 1 s timer polls the sessions for due retries and refreshes the status.
// STATUS: COMPILES for the simulator; never run on a phone (no Bluetooth in the simulator, no Apple ID here).
// ROLE IN THESIS: The iOS gateway of the topology (Unit 15).
// BEFORE -> AFTER: Before: package and tests only. After: an app that wires them.
import CoreBluetooth
import EHGGatewayCore
import Foundation
import Security
import SwiftUI

@MainActor
final class GatewayModel: ObservableObject {
    @Published var host = UserDefaults.standard.string(forKey: "host") ?? ""
    @Published var gatewayId = UserDefaults.standard.string(forKey: "gatewayId") ?? ""
    @Published var allowedNodes = UserDefaults.standard.string(forKey: "allowedNodes") ?? ""   // comma-separated node ids, e.g. EHG-93C0
    @Published var running = false
    @Published var mqtt = "not connected"
    @Published var lastPuback: Date?
    @Published var identityExpiry: Date?
    @Published var enrolStatus = ""
    @Published var userCode: String?
    @Published var verificationURL: URL?
    @Published var logLines: [String] = []
    @Published var rows: [(node: String, received: Int, missing: Int, duplicates: Int, bad: Int)] = []
    @Published var pending = 0
    @Published var dropped = 0

    let ble = BleLink()
    private var outbox: Outbox?
    private var client: MQTTClient?
    private var timer: Timer?

    var signatureDaysLeft: Int? {   // a free Apple account's signature lasts 7 days from the install; the date is stored on first run of each install
        let key = "installedAt"
        let d = UserDefaults.standard.object(forKey: key) as? Date ?? { let n = Date(); UserDefaults.standard.set(n, forKey: key); return n }()
        return max(0, 7 - Int(Date().timeIntervalSince(d) / 86400))
    }

    func log(_ s: String) {
        let line = "\(ISO8601DateFormatter().string(from: Date())) \(s)"
        logLines.append(line); if logLines.count > 500 { logLines.removeFirst(100) }
    }

    func exportLog() -> String { logLines.joined(separator: "\n") }

    // MARK: identity

    func importIdentity(from url: URL, password: String) {
        guard url.startAccessingSecurityScopedResource() else { log("cannot read the file"); return }
        defer { url.stopAccessingSecurityScopedResource() }
        var items: CFArray?
        guard let data = try? Data(contentsOf: url), SecPKCS12Import(data as CFData, [kSecImportExportPassphrase as String: password] as CFDictionary, &items) == errSecSuccess,
              let first = (items as? [[String: Any]])?.first, let identity = first[kSecImportItemIdentity as String] as! SecIdentity? else { log("the certificate file or its password was refused"); return }
        SecItemDelete([kSecClass as String: kSecClassIdentity, kSecAttrLabel as String: "ehg-gateway"] as CFDictionary)
        let add = SecItemAdd([kSecValueRef as String: identity, kSecAttrLabel as String: "ehg-gateway", kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly] as CFDictionary, nil)
        log(add == errSecSuccess ? "certificate stored in the Keychain" : "Keychain refused the certificate (\(add))")
        refreshIdentity()
    }

    /// Keycloak device grant -> own P-256 key (kept in the Keychain) -> certificate request -> enrolment API -> certificate in the Keychain. Run it again before the certificate expires to renew.
    /// The gateway id becomes the Keycloak user name (the server uses it as the certificate's CN). NEVER run on a phone; the logic is tested in EHGGatewayCore, the Keychain calls are not.
    func enrol() {
        guard !host.isEmpty else { log("set the broker host first"); return }
        enrolStatus = "asking Keycloak for a code ..."
        Task {
            do {
                let realm = "https://\(host)/auth/realms/ehg", client = "ios-gateway", e = Enrolment()
                let dc = try await e.begin(realm: realm, clientId: client)
                userCode = dc.userCode; verificationURL = URL(string: dc.verificationURL); enrolStatus = "open the link, sign in, approve code \(dc.userCode)"
                let token = try await e.token(realm: realm, clientId: client, code: dc)
                guard let user = Enrolment.userName(inToken: token) else { throw EnrolmentError.malformed }
                for cls in [kSecClassIdentity, kSecClassCertificate, kSecClassKey] { SecItemDelete([kSecClass as String: cls, kSecAttrLabel as String: "ehg-gateway"] as CFDictionary) }
                let key = try CSR.newKey(permanentLabel: "ehg-gateway")
                let pem = try await e.enrol(url: "https://\(host)/enroll-api/enroll", token: token, csrPEM: CSR.pem(try CSR.build(commonName: user, key: key)))
                let b64 = pem.components(separatedBy: "\n").filter { !$0.hasPrefix("-----") }.joined()
                guard let der = Data(base64Encoded: b64), let cert = SecCertificateCreateWithData(nil, der as CFData) else { throw EnrolmentError.malformed }
                let add = SecItemAdd([kSecValueRef as String: cert, kSecAttrLabel as String: "ehg-gateway"] as CFDictionary, nil)
                guard add == errSecSuccess else { throw EnrolmentError.signing("Keychain refused the certificate (\(add))") }
                gatewayId = user; userCode = nil; verificationURL = nil; enrolStatus = "enrolled as \(user)"; refreshIdentity(); log("enrolled as \(user)")
            } catch { enrolStatus = "enrolment failed: \(error)"; userCode = nil; log("enrolment failed: \(error)") }
        }
    }

    func loadIdentity() -> SecIdentity? {
        var out: CFTypeRef?
        let q: [String: Any] = [kSecClass as String: kSecClassIdentity, kSecAttrLabel as String: "ehg-gateway", kSecReturnRef as String: true]
        return SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess ? (out as! SecIdentity?) : nil
    }

    func refreshIdentity() {
        var cert: SecCertificate?
        if let id = loadIdentity(), SecIdentityCopyCertificate(id, &cert) == errSecSuccess, let cert { identityExpiry = certificateNotAfter(der: SecCertificateCopyData(cert) as Data) } else { identityExpiry = nil }
    }

    // MARK: run

    func start() {
        guard !host.isEmpty, !gatewayId.isEmpty else { log("set the broker host and the gateway id first"); return }
        let nodes = Set(allowedNodes.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { NodeAdmission.isValidNodeId($0) })
        guard !nodes.isEmpty else { log("list the node ids this gateway may serve (EHG-XXXX): any other Bluetooth device is ignored"); return }
        guard let identity = loadIdentity() else { log("import the client certificate first"); return }
        guard let caURL = Bundle.main.url(forResource: "ca-chain", withExtension: "crt"), let pem = try? String(contentsOf: caURL, encoding: .utf8) else { log("ca-chain.crt is not bundled (copy backend/pki/ca-chain.crt into Sources and rebuild)"); return }
        UserDefaults.standard.set(host, forKey: "host"); UserDefaults.standard.set(gatewayId, forKey: "gatewayId"); UserDefaults.standard.set(allowedNodes, forKey: "allowedNodes")
        ble.allow(nodes)
        let anchors = pem.components(separatedBy: "-----END CERTIFICATE-----").compactMap { chunk -> SecCertificate? in
            guard let r = chunk.range(of: "-----BEGIN CERTIFICATE-----") else { return nil }
            return Data(base64Encoded: chunk[r.upperBound...].replacingOccurrences(of: "\n", with: "")).flatMap { SecCertificateCreateWithData(nil, $0 as CFData) }
        }
        let dir = try! FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        let statusTopic = "ehg/_gw/\(gatewayId)/status"
        let c = MQTTClient(host: host, clientId: gatewayId, identity: identity, anchors: anchors, will: MQTTWill(topic: statusTopic, payload: "offline"))
        guard let box = try? Outbox(path: dir.appendingPathComponent("outbox.db").path, transport: c, log: { [weak self] s in Task { @MainActor in self?.log(s) } }) else { log("cannot open the outbox file"); return }
        c.onConnected = { [weak self] in box.setConnected(true); c.publishRetained(topic: statusTopic, payload: "online"); Task { @MainActor in self?.mqtt = "connected"; self?.log("MQTT connected") } }
        c.onDisconnected = { [weak self] in box.setConnected(false); Task { @MainActor in self?.mqtt = "reconnecting"; self?.log("MQTT disconnected") } }
        c.onPuback = { [weak self] token in box.acked(token: token); Task { @MainActor in self?.lastPuback = Date() } }
        outbox = box; client = c
        ble.makeSession = { NodeSession(nodeId: $0, outbox: box) }
        ble.onLog = { [weak self] s in self?.log(s) }
        c.start(); ble.start(); running = true
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in Task { @MainActor in self?.tick() } }
        log("started as \(gatewayId) -> \(host)")
    }

    func stop() {
        timer?.invalidate(); ble.stop(); client?.stop(); running = false; mqtt = "not connected"; log("stopped")
    }

    private func tick() {
        ble.poll(); rows = ble.summaries
        pending = outbox?.pending() ?? 0; dropped = outbox?.dropped ?? 0
    }
}
