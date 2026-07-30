import SwiftUI
import UIKit

@main
struct HealthKitExporterApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .onReceive(
                    NotificationCenter.default.publisher(
                        for: UIApplication.protectedDataDidBecomeAvailableNotification
                    )
                ) { _ in
                    model.protectedDataDidBecomeAvailable()
                }
        }
        .onChange(of: scenePhase) { _, newPhase in
            if newPhase == .active {
                model.appDidBecomeActive()
            }
        }
    }
}
