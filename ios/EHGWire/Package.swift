// swift-tools-version: 5.9
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
