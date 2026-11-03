// swift-tools-version: 5.9
// PURPOSE: The iPhone gateway's wire-format library as a plain Swift package, so it can be tested on the command line (`swift test`) without Xcode, a simulator, a signing identity or an iPhone.
// INPUT: none. OUTPUT: library EHGWire (decoder and encoder for wire v2) and its tests, which decode the golden vectors in ../../tests/fixtures/packet_vectors.json.
// ROLE IN THESIS: Third copy of the wire format (decision 0015); the app target `ios/EHGGateway` (not written: it needs Byron, an Apple ID and the phone) will import this package.
import PackageDescription

let package = Package(
    name: "EHGWire",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "EHGWire", targets: ["EHGWire"])],
    targets: [
        .target(name: "EHGWire"),
        .testTarget(name: "EHGWireTests", dependencies: ["EHGWire"]),
    ]
)
