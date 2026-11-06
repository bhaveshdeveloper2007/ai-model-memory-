// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MemoryOSMenuBar",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "MemoryOSMenuBar", targets: ["MemoryOSMenuBar"]),
        .library(name: "MemoryOSCore", targets: ["MemoryOSCore"])
    ],
    targets: [
        .target(name: "MemoryOSCore", path: "Sources/MemoryOSCore"),
        .executableTarget(name: "MemoryOSMenuBar", dependencies: ["MemoryOSCore"], path: "Sources/MemoryOSMenuBar"),
        .testTarget(name: "MemoryOSCoreTests", dependencies: ["MemoryOSCore"], path: "Tests/MemoryOSCoreTests")
    ]
)
