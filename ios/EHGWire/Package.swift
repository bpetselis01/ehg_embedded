// swift-tools-version: 5.9
// PURPOSE: The iPhone gateway's wire-format library as a plain Swift package, so it can be tested on the command line (`swift test`) without Xcode, a simulator, a signing identity or an iPhone.
// INPUT: none. OUTPUT: library EHGWire (decoder and encoder for wire v2) and its tests, which decode the golden vectors in ../../tests/fixtures/packet_vectors.json.
// ROLE IN THESIS: Third copy of the wire format (decision 0015); the app target `ios/EHGGateway` imports it. EHGGatewayCore holds the gateway logic without Bluetooth or UI (message builder, outbox, per-node session, MQTT client).
import PackageDescription

let package = Package(
    name: "EHGWire",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "EHGWire", targets: ["EHGWire"]), .library(name: "EHGGatewayCore", targets: ["EHGGatewayCore"])],
    targets: [
        .target(name: "EHGWire"),
        .target(name: "EHGGatewayCore", dependencies: ["EHGWire"], linkerSettings: [.linkedLibrary("sqlite3")]),
        .testTarget(name: "EHGWireTests", dependencies: ["EHGWire"]),
        .testTarget(name: "EHGGatewayCoreTests", dependencies: ["EHGGatewayCore", "EHGWire"]),
    ]
)
