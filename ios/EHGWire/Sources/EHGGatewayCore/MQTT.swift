// PURPOSE: A small MQTT 3.1.1 client for the phone: TLS 1.3 with a client certificate (mTLS), persistent session, QoS 1 publishes, last will, keep-alive and reconnect with backoff. Written on Network.framework
//   instead of a third-party library because the plan's own concern was that popular clients cannot present an EC client identity; this takes a SecIdentity directly.
// INPUT: host, port, client id (= the certificate's CN: the broker uses it as the user name), the client SecIdentity, the CA certificates to trust, an optional last will. start() / stop(); send(topic, payload) -> token.
// OUTPUT: callbacks onConnected(), onDisconnected(), onPuback(token). `send` returns the MQTT packet id as the token the outbox waits for, or nil when not connected. publishRetained() is for the online/offline status topic.
// HOW: only what the gateway uses is implemented: CONNECT (with will), CONNACK, PUBLISH QoS 1 (out), PUBACK (in), PINGREQ/PINGRESP, DISCONNECT. The server certificate is validated against the given anchors ONLY (not the system store) and
//   the host name; TLS below 1.3 is refused. Reconnect waits 1 s doubling to 60 s with jitter. Everything runs on one serial queue.
// STATUS: tested against a real Mosquitto 2.1.2 with the repository's TLS config and ACL (tests/test_ios_mqtt.py). Never run on a phone, so cellular behaviour, background suspension and Keychain handling are NOT verified.
// ROLE IN THESIS: Hop 4 of the topology (phone -> cloud) for the iOS gateway.
// BEFORE -> AFTER: Before: the outbox had no transport. After: it has a TLS client.
import Foundation
import Network
import Security

public struct MQTTWill { public var topic: String, payload: String
    public init(topic: String, payload: String) { self.topic = topic; self.payload = payload } }

public final class MQTTClient: OutboxTransport {
    public var onConnected: () -> Void = {}
    public var onDisconnected: () -> Void = {}
    public var onPuback: (Int) -> Void = { _ in }
    public private(set) var isConnected = false

    private let host: String, port: UInt16, clientId: String, keepAlive: UInt16, will: MQTTWill?
    private let identity: SecIdentity, anchors: [SecCertificate]
    private let queue = DispatchQueue(label: "ehg.mqtt")
    private let queueKey = DispatchSpecificKey<Bool>()
    private var conn: NWConnection?
    private var buffer = [UInt8]()
    private var packetId: UInt16 = 0
    private var backoff = 1.0
    private var wantRunning = false
    private var ping: DispatchSourceTimer?
    private var lastRx = Date()

    public init(host: String, port: UInt16 = 8883, clientId: String, identity: SecIdentity, anchors: [SecCertificate], keepAlive: UInt16 = 30, will: MQTTWill? = nil) {
        self.host = host; self.port = port; self.clientId = clientId; self.identity = identity; self.anchors = anchors; self.keepAlive = keepAlive; self.will = will
        queue.setSpecific(key: queueKey, value: true)
    }

    /// Run on the client's queue, or directly when already on it: the callbacks (onConnected -> outbox -> send) call back in from that queue, and a nested sync would trap.
    private func onQueue<T>(_ work: () -> T) -> T { DispatchQueue.getSpecific(key: queueKey) == true ? work() : queue.sync(execute: work) }

    public func start() { queue.async { self.wantRunning = true; self.connect() } }

    public func stop() {
        onQueue {
            wantRunning = false
            if isConnected { conn?.send(content: Data([0xE0, 0x00]), completion: .idempotent) }   // DISCONNECT: a clean exit does not trigger the will
            teardown(notify: true)
        }
    }

    /// QoS 1 publish; returns the packet id (the outbox's token) or nil if the link is down.
    public func send(topic: String, payload: String) -> Int? { onQueue { publish(topic, payload, retain: false) } }

    /// Retained QoS 1 publish for the status topic (online/offline).
    @discardableResult public func publishRetained(topic: String, payload: String) -> Int? { onQueue { publish(topic, payload, retain: true) } }

    /// Test hook: drop the connection WITHOUT a DISCONNECT, as a crash or lost signal would, so the broker publishes the will.
    func abortForTest() { onQueue { wantRunning = false; teardown(notify: true) } }

    // MARK: connection

    private func connect() {
        guard wantRunning, conn == nil else { return }
        let tls = NWProtocolTLS.Options()
        let sec = tls.securityProtocolOptions
        sec_protocol_options_set_min_tls_protocol_version(sec, .TLSv13)
        sec_protocol_options_set_tls_server_name(sec, host)
        sec_protocol_options_set_local_identity(sec, sec_identity_create(identity)!)
        let roots = anchors
        sec_protocol_options_set_verify_block(sec, { _, trust, complete in
            let t = sec_trust_copy_ref(trust).takeRetainedValue()
            SecTrustSetAnchorCertificates(t, roots as CFArray)
            SecTrustSetAnchorCertificatesOnly(t, true)   // our CA only, never the system store
            complete(SecTrustEvaluateWithError(t, nil))
        }, queue)
        let c = NWConnection(host: NWEndpoint.Host(host), port: NWEndpoint.Port(rawValue: port)!, using: NWParameters(tls: tls, tcp: NWProtocolTCP.Options()))
        conn = c; buffer.removeAll(); lastRx = Date()
        c.stateUpdateHandler = { [weak self, weak c] state in
            guard let self, let c, self.conn === c else { return }
            switch state {
            case .ready: self.sendConnect(); self.receive()
            case .failed, .cancelled: self.lost()
            case .waiting: self.lost()
            default: break
            }
        }
        c.start(queue: queue)
    }

    private func lost() {
        let was = isConnected
        teardown(notify: was)
        guard wantRunning else { return }
        let wait = backoff * Double.random(in: 0.8...1.2)
        backoff = min(backoff * 2, 60)
        queue.asyncAfter(deadline: .now() + wait) { self.connect() }
    }

    private func teardown(notify: Bool) {
        ping?.cancel(); ping = nil
        let c = conn; conn = nil
        c?.cancel()
        let was = isConnected; isConnected = false
        if was && notify { onDisconnected() }
    }

    private func receive() {
        guard let c = conn else { return }
        c.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self, weak c] data, _, isComplete, error in
            guard let self, let c, self.conn === c else { return }
            if let data, !data.isEmpty { self.buffer += [UInt8](data); self.lastRx = Date(); self.parse() }
            if error != nil || isComplete { self.lost() } else { self.receive() }
        }
    }

    private func parse() {
        while buffer.count >= 2 {
            var mult = 1, len = 0, i = 1
            while true {
                guard i < buffer.count else { return }
                let b = Int(buffer[i]); len += (b & 0x7F) * mult; i += 1
                if b & 0x80 == 0 { break }
                mult *= 128
                if mult > 128 * 128 * 128 { lost(); return }
            }
            guard buffer.count >= i + len else { return }
            let type = buffer[0] >> 4, body = Array(buffer[i..<(i + len)])
            buffer.removeFirst(i + len)
            switch type {
            case 2:   // CONNACK: session-present, return code
                if body.count == 2, body[1] == 0 { isConnected = true; backoff = 1; startPing(); onConnected() } else { teardown(notify: false); wantRunning = false; return }
            case 4 where body.count == 2: onPuback(Int(UInt16(body[0]) << 8 | UInt16(body[1])))
            default: break   // PINGRESP and anything else: lastRx already refreshed
            }
        }
    }

    private func startPing() {
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now() + .seconds(Int(keepAlive) / 2), repeating: .seconds(max(1, Int(keepAlive) / 2)))
        t.setEventHandler { [weak self] in
            guard let self, self.isConnected else { return }
            if Date().timeIntervalSince(self.lastRx) > Double(self.keepAlive) * 1.5 { self.lost(); return }
            self.conn?.send(content: Data([0xC0, 0x00]), completion: .idempotent)
        }
        t.resume(); ping = t
    }

    // MARK: encoding

    private func utf8(_ s: String) -> [UInt8] { let b = Array(s.utf8); return [UInt8(b.count >> 8), UInt8(b.count & 0xFF)] + b }
    private func remaining(_ n: Int) -> [UInt8] { var out = [UInt8](), x = n; repeat { var d = UInt8(x % 128); x /= 128; if x > 0 { d |= 0x80 }; out.append(d) } while x > 0; return out }

    private func sendConnect() {
        var flags: UInt8 = 0                                   // clean session = 0: the broker keeps our subscriptions and queued QoS 1 messages across reconnects
        var payload = utf8(clientId)
        if let w = will { flags |= 0x04 | 0x08 | 0x20; payload += utf8(w.topic) + utf8(w.payload) }   // will flag, QoS 1, retained
        let variable: [UInt8] = utf8("MQTT") + [4, flags, UInt8(keepAlive >> 8), UInt8(keepAlive & 0xFF)]
        let body = variable + payload
        conn?.send(content: Data([0x10] + remaining(body.count) + body), completion: .idempotent)
    }

    private func publish(_ topic: String, _ payload: String, retain: Bool) -> Int? {
        guard isConnected, let c = conn else { return nil }
        packetId = packetId &+ 1; if packetId == 0 { packetId = 1 }
        let body = utf8(topic) + [UInt8(packetId >> 8), UInt8(packetId & 0xFF)] + Array(payload.utf8)
        c.send(content: Data([0x32 | (retain ? 1 : 0)] + remaining(body.count) + body), completion: .idempotent)
        return Int(packetId)
    }
}
