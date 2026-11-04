import EHGWire
import Foundation
import Security
import XCTest
@testable import EHGGatewayCore

/// Runs only when tests/test_ios_mqtt.py has started a real Mosquitto and exported its settings; otherwise skipped.
final class MQTTIntegrationTests: XCTestCase {
    private func settings() throws -> (port: UInt16, id: String, identity: SecIdentity, anchors: [SecCertificate], mode: String) {
        let env = ProcessInfo.processInfo.environment
        guard let port = env["EHG_MQTT_PORT"], let ca = env["EHG_MQTT_CA"], let p12 = env["EHG_MQTT_P12"], let id = env["EHG_MQTT_ID"] else { throw XCTSkip("no broker (set by tests/test_ios_mqtt.py)") }
        var items: CFArray?
        let status = SecPKCS12Import(try Data(contentsOf: URL(fileURLWithPath: p12)) as CFData, [kSecImportExportPassphrase as String: "test"] as CFDictionary, &items)
        guard status == errSecSuccess, let first = (items as? [[String: Any]])?.first, let identity = first[kSecImportItemIdentity as String] as! SecIdentity? else {
            XCTFail("the PKCS#12 did not import (OSStatus \(status)); macOS wants SHA-1/3DES-style PKCS#12, not OpenSSL 3's default AES"); throw XCTSkip("no identity")
        }
        let pem = try String(contentsOfFile: ca, encoding: .utf8)
        let anchors = pem.components(separatedBy: "-----END CERTIFICATE-----").compactMap { chunk -> SecCertificate? in
            guard let r = chunk.range(of: "-----BEGIN CERTIFICATE-----") else { return nil }
            let b64 = chunk[r.upperBound...].replacingOccurrences(of: "\n", with: "")
            return Data(base64Encoded: b64).flatMap { SecCertificateCreateWithData(nil, $0 as CFData) }
        }
        return (UInt16(port)!, id, identity, anchors, env["EHG_MQTT_MODE"] ?? "publish")
    }

    private func wait(_ seconds: Double, _ cond: () -> Bool) -> Bool {
        let end = Date().addingTimeInterval(seconds)
        while Date() < end { if cond() { return true }; RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
        return cond()
    }

    func testOutboxDeliversThroughTLS13WithAClientCertificateAndTheWillFiresOnAnUncleanDrop() throws {
        let s = try settings(); fputs("[IT] settings ok, anchors=\(s.anchors.count)\n", stderr)
        let will = MQTTWill(topic: "ehg/_gw/\(s.id)/status", payload: "offline")
        let client = MQTTClient(host: "localhost", port: s.port, clientId: s.id, identity: s.identity, anchors: s.anchors, keepAlive: 10, will: will)
        let box = try Outbox(path: NSTemporaryDirectory() + "it-\(UUID().uuidString).db", transport: client, clock: { Date().timeIntervalSince1970 }, log: { print($0) })
        client.onConnected = { box.setConnected(true); client.publishRetained(topic: will.topic, payload: "online") }
        client.onDisconnected = { box.setConnected(false) }
        client.onPuback = { box.acked(token: $0) }
        // two messages BEFORE the link exists: they must wait on disk and be replayed
        for seq in 0..<2 { box.add(nodeId: "EHG-IT", seq: seq, tInject: seq, topic: "ehg/EHG-IT/pkt", payload: "{\"node_id\": \"EHG-IT\", \"seq\": \(seq)}") }
        fputs("[IT] starting\n", stderr); client.start()
        XCTAssertTrue(wait(10) { client.isConnected }, "TLS 1.3 + client certificate + CONNACK"); fputs("[IT] connected=\(client.isConnected)\n", stderr)
        for seq in 2..<5 { box.add(nodeId: "EHG-IT", seq: seq, tInject: seq, topic: "ehg/EHG-IT/pkt", payload: "{\"node_id\": \"EHG-IT\", \"seq\": \(seq)}") }
        XCTAssertTrue(wait(10) { box.pending() == 0 }, "every message acknowledged by the broker and deleted from disk")
        // a topic the ACL does not allow this identity: the broker drops it silently and still acknowledges QoS 1 at the MQTT level; the python side asserts it never arrives
        _ = client.send(topic: "ehg/_gw/someone-else/status", payload: "spoof")
        RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        client.abortForTest()   // no DISCONNECT: the broker must publish the will
        RunLoop.current.run(until: Date().addingTimeInterval(1.0))
    }
}
