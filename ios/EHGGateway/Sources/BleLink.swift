// PURPOSE: The Bluetooth side of the gateway: finds boards named EHG-*, connects, subscribes to the data characteristic, starts sampling, passes every notification to the node's session, and writes RESEND commands.
//   The CoreBluetooth counterpart of run_ble_node() in gateway/gateway.py.
// INPUT: a factory for NodeSessions (so the app owns the outbox). OUTPUT: callbacks for the status screen. State restoration is on (restore identifier), so iOS can relaunch the app and hand back the connection.
// HOW: scan for the EHG service UUID, connect, discover, setNotifyValue, write OP_START (0x01). RESEND = 0x03 + seq (UInt32 LE) + count (UInt16 LE), written without response. On disconnect it reconnects after 2 s; the NodeSession is kept, so
//   its gap tracker still knows the last seq and the board's history fills the outage. A secure board (pairing) shows the system passkey dialog on the first protected access; there is no pairing code here on purpose (CoreBluetooth cannot pair itself).
// STATUS: COMPILES for the simulator. NEVER RUN: the simulator has no Bluetooth and no phone was used, so scanning, notification rate, background behaviour and state restoration are all UNVERIFIED (the 15a feasibility spike).
// ROLE IN THESIS: Hop 3 of the topology (board -> phone).
// BEFORE -> AFTER: Before: only the Mac could be the gateway. After: a phone can, once the spike has been run.
import CoreBluetooth
import EHGGatewayCore
import Foundation

final class BleLink: NSObject, ObservableObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    static let service = CBUUID(string: "4e480001-a0e1-4c3b-8747-9a1b2c3d4e5f")
    static let data = CBUUID(string: "4e480002-a0e1-4c3b-8747-9a1b2c3d4e5f")
    static let control = CBUUID(string: "4e480003-a0e1-4c3b-8747-9a1b2c3d4e5f")

    @Published var state = "idle"
    var makeSession: (String) -> NodeSession = { _ in fatalError("set by GatewayModel") }
    var onLog: (String) -> Void = { _ in }

    private var central: CBCentralManager!
    private var sessions: [UUID: NodeSession] = [:]
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var control: [UUID: CBCharacteristic] = [:]
    private var running = false
    private var admission = NodeAdmission(allowed: [])
    private var refused = Set<String>()   // log each refusal once

    /// Node ids this gateway may serve (set before start). Empty = nothing is accepted.
    func allow(_ nodes: Set<String>) { admission = NodeAdmission(allowed: nodes); refused.removeAll() }

    func start() {
        running = true
        if central == nil { central = CBCentralManager(delegate: self, queue: .main, options: [CBCentralManagerOptionRestoreIdentifierKey: "net.thesisb.ehggateway.central"]) }
        else { scanIfReady() }
    }

    func stop() {
        running = false
        admission = NodeAdmission(allowed: admission.allowed)   // forget the claims: a re-flashed board may come back with a new identifier
        central?.stopScan()
        peripherals.values.forEach { central.cancelPeripheralConnection($0) }
    }

    private func scanIfReady() {
        guard running, central.state == .poweredOn else { state = central.state == .poweredOn ? "idle" : "Bluetooth is off or not allowed"; return }
        central.scanForPeripherals(withServices: [Self.service])
        state = "scanning for EHG-* nodes"
    }

    // MARK: CBCentralManagerDelegate

    func centralManagerDidUpdateState(_ c: CBCentralManager) { scanIfReady() }

    func centralManager(_ c: CBCentralManager, willRestoreState dict: [String: Any]) {
        for p in (dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral]) ?? [] where admit(p) { adopt(p); onLog("restored \(p.name ?? "?")") }
    }

    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral, advertisementData: [String: Any], rssi: NSNumber) {
        guard peripherals[p.identifier] == nil, admit(p) else { return }
        adopt(p); c.connect(p)
    }

    /// Name check, allowlist and one-claimant rule (NodeAdmission); every refusal is logged once.
    private func admit(_ p: CBPeripheral) -> Bool {
        switch admission.admit(name: p.name, peripheral: p.identifier) {
        case .accept: return true
        case .reject(let why):
            if refused.insert("\(p.identifier)\(why)").inserted { onLog("ignored \(p.name ?? "unnamed device"): \(why)") }
            return false
        }
    }

    private func adopt(_ p: CBPeripheral) {
        peripherals[p.identifier] = p; p.delegate = self
        if sessions[p.identifier] == nil, let name = p.name { sessions[p.identifier] = makeSession(name) }
    }

    func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) { state = "connected to \(p.name ?? "?")"; p.discoverServices([Self.service]) }

    func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
        onLog("\(p.name ?? "?") disconnected: \(error?.localizedDescription ?? "closed")")
        control[p.identifier] = nil
        guard running else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { if self.running { c.connect(p) } }   // same NodeSession: the gap tracker asks for what the outage cost
    }

    func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        onLog("connect failed: \(error?.localizedDescription ?? "?")")
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { if self.running { c.connect(p) } }
    }

    // MARK: CBPeripheralDelegate

    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        p.services?.forEach { p.discoverCharacteristics([Self.data, Self.control], for: $0) }
    }

    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        for ch in s.characteristics ?? [] {
            if ch.uuid == Self.data { p.setNotifyValue(true, for: ch) }
            if ch.uuid == Self.control { control[p.identifier] = ch }
        }
    }

    func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor ch: CBCharacteristic, error: Error?) {
        if let error { onLog("subscribe failed (a secure board needs pairing first): \(error.localizedDescription)"); return }
        if ch.uuid == Self.data, let ctl = control[p.identifier] { p.writeValue(Data([0x01]), for: ctl, type: .withResponse) }   // START; a no-op if already running
    }

    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        guard ch.uuid == Self.data, let v = ch.value, let session = sessions[p.identifier] else { return }
        session.requestResend = { [weak self] start, count in self?.writeResend(p, start, count) }
        session.onBytes([UInt8](v), tRx: Date().timeIntervalSince1970)
    }

    private func writeResend(_ p: CBPeripheral, _ start: Int, _ count: Int) {
        guard let ctl = control[p.identifier] else { return }
        var b: [UInt8] = [0x03]
        for i in 0..<4 { b.append(UInt8((start >> (8 * i)) & 0xFF)) }
        for i in 0..<2 { b.append(UInt8((count >> (8 * i)) & 0xFF)) }
        p.writeValue(Data(b), for: ctl, type: .withoutResponse)
    }

    /// Called once a second by the model so a retry falls due even when no packet arrives.
    func poll() { let now = Date().timeIntervalSince1970; sessions.values.forEach { $0.poll(at: now) } }

    var summaries: [(node: String, received: Int, missing: Int, duplicates: Int, bad: Int)] {
        sessions.values.map { ($0.nodeId, $0.gaps.received, $0.gaps.missing.count, $0.gaps.duplicates, $0.bad) }.sorted { $0.node < $1.node }
    }
}
