// PURPOSE: The status screen: is the phone connected to the board and to the cloud, how many packets arrived, what is missing, what is waiting on the phone, when the last PUBACK came, how long the certificate and the 7-day signature last,
//   and a button to export the log. Plain SwiftUI, no state of its own.
// STATUS: COMPILES for the simulator; the layout has been looked at only in the simulator.
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var model = GatewayModel()
    @State private var picking = false
    @State private var password = ""
    @State private var pickedURL: URL?

    var body: some View {
        NavigationStack {
            Form {
                Section("Cloud") {
                    TextField("Broker host (e.g. ehg.duckdns.org)", text: $model.host).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("Gateway id (the certificate's CN)", text: $model.gatewayId).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("Allowed nodes (EHG-93C0, ...)", text: $model.allowedNodes).textInputAutocapitalization(.characters).autocorrectionDisabled()
                    Button("Enrol with Keycloak (device code)") { model.enrol() }
                    if let code = model.userCode, let url = model.verificationURL { Link("Code \(code): open to approve", destination: url) }
                    if !model.enrolStatus.isEmpty { Text(model.enrolStatus).font(.footnote).foregroundStyle(.secondary) }
                    SecureField("PKCS#12 password (manual route)", text: $password)
                    Button("Import certificate (.p12) instead") { picking = true }
                    LabeledContent("MQTT", value: model.mqtt)
                    LabeledContent("Last PUBACK", value: model.lastPuback.map { $0.formatted(date: .omitted, time: .standard) } ?? "never")
                    LabeledContent("Certificate expires", value: model.identityExpiry.map { $0.formatted(date: .abbreviated, time: .omitted) } ?? "none imported")
                }
                Section("Phone") {
                    LabeledContent("Bluetooth", value: model.ble.state)
                    LabeledContent("Signature days left", value: model.signatureDaysLeft.map(String.init) ?? "?")
                    LabeledContent("Waiting on the phone", value: "\(model.pending) (dropped \(model.dropped))")
                }
                Section("Nodes") {
                    if model.rows.isEmpty { Text("none yet").foregroundStyle(.secondary) }
                    ForEach(model.rows, id: \.node) { r in
                        VStack(alignment: .leading) {
                            Text(r.node).font(.headline)
                            Text("received \(r.received)   missing \(r.missing)   duplicates \(r.duplicates)   bad \(r.bad)").font(.footnote.monospacedDigit())
                        }
                    }
                }
                Section {
                    Button(model.running ? "Stop" : "Start") { model.running ? model.stop() : model.start() }
                    ShareLink("Export log", item: model.exportLog())
                }
                Section("Log") { ForEach(Array(model.logLines.suffix(20).enumerated()), id: \.offset) { Text($1).font(.caption2.monospaced()) } }
                Text("Research prototype for measuring wireless telemetry performance. Not for clinical use.").font(.footnote).foregroundStyle(.secondary)
            }
            .navigationTitle("EHG Gateway")
            .fileImporter(isPresented: $picking, allowedContentTypes: [UTType(filenameExtension: "p12") ?? .data]) { if case .success(let url) = $0 { model.importIdentity(from: url, password: password) } }
            .onAppear { model.refreshIdentity() }
        }
    }
}
