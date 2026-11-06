import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var model: DashboardModel
    @AppStorage("memoryOSAPIBaseURL") private var apiBaseURL = "http://127.0.0.1:8766"
    @State private var token = ""
    @State private var message = ""

    var body: some View {
        Form {
            Section("Local backend") {
                TextField("API address", text: $apiBaseURL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("Agent access token", text: $token)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                HStack {
                    Button("Save to Keychain") { saveToken() }
                    Button("Connect") { Task { await model.refresh() } }
                }
                Text(message.isEmpty ? "The token stays in the macOS Keychain." : message)
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Text("Use a project-scoped agent token. The app connects to localhost and does not send memory to a hosted service.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(20)
        .onAppear { token = KeychainTokenStore.read() ?? "" }
    }

    private func saveToken() {
        let status = KeychainTokenStore.save(token)
        message = status == errSecSuccess ? "Saved in Keychain." : "Keychain could not save this token (status \(status))."
        if status == errSecSuccess {
            Task { await model.refresh() }
        }
    }
}
