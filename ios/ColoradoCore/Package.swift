// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ColoradoCore",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "ColoradoCore", targets: ["ColoradoCore"])],
    targets: [
        .target(name: "ColoradoCore"),
        .testTarget(name: "ColoradoCoreTests", dependencies: ["ColoradoCore"], resources: [.copy("desktop-fixtures.json")])
    ]
)
