// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "HealthKitExporterHostTests",
    platforms: [
        .iOS(.v17),
        .macOS(.v14)
    ],
    products: [
        .library(name: "HealthKitExporter", targets: ["HealthKitExporter"])
    ],
    targets: [
        .target(
            name: "HealthKitExporter",
            path: "Core",
            exclude: ["BackgroundSyncRegistrar.swift"],
            linkerSettings: [
                .linkedFramework("HealthKit"),
                .linkedFramework("Security")
            ]
        ),
        .testTarget(
            name: "HealthKitExporterTests",
            dependencies: ["HealthKitExporter"],
            path: "Tests"
        )
    ],
    swiftLanguageModes: [.v5]
)
