import SwiftUI

@main
struct MemoryOSApp: App {
    @StateObject private var model = DashboardModel()

    var body: some Scene {
        MenuBarExtra {
            MenuBarDashboard()
                .environmentObject(model)
                .frame(width: 390, height: 540)
        } label: {
            Label("Memory OS", systemImage: model.isOnline ? "brain.head.profile" : "brain")
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
                .environmentObject(model)
                .frame(width: 440, height: 280)
        }
    }
}
