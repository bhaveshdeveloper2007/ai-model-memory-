import Combine
import Foundation
import MemoryOSCore

struct BackendHealth: Decodable {
    let status: String
    let database: String
    let vector_extension: Bool
}

struct ProjectSummary: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let root_path: String
}

struct TaskSummary: Decodable, Identifiable {
    let id: String
    let title: String
    let status: String
    let details: String?
}

struct EventSummary: Decodable, Identifiable {
    let id: String
    let event_type: String
    let occurred_at: String
    let source: String
}

@MainActor
final class DashboardModel: ObservableObject {
    @Published private(set) var isOnline = false
    @Published private(set) var statusText = "Not connected"
    @Published private(set) var projects: [ProjectSummary] = []
    @Published private(set) var tasks: [TaskSummary] = []
    @Published private(set) var events: [EventSummary] = []
    @Published var selectedProjectID: String?
    @Published private(set) var lastError: String?
    @Published private(set) var vectorEnabled = false

    private var apiBaseURL: String {
        UserDefaults.standard.string(forKey: "memoryOSAPIBaseURL") ?? "http://127.0.0.1:8766"
    }

    func refresh() async {
        guard let token = KeychainTokenStore.read(), !token.isEmpty else {
            isOnline = false
            statusText = "Add an agent token in Settings"
            return
        }
        do {
            let health: BackendHealth = try await get("/health", token: token)
            let projects: [ProjectSummary] = try await get("/v1/projects", token: token)
            self.projects = projects
            vectorEnabled = health.vector_extension
            if selectedProjectID == nil || !projects.contains(where: { $0.id == selectedProjectID }) {
                selectedProjectID = projects.first?.id
            }
            isOnline = health.status == "ok"
            statusText = "Backend and database connected"
            lastError = nil
            await refreshProjectData(token: token)
        } catch {
            isOnline = false
            statusText = "Backend unavailable"
            lastError = error.localizedDescription
            tasks = []
            events = []
        }
    }

    func refreshProjectData(token: String? = nil) async {
        guard let projectID = selectedProjectID,
              let token = token ?? KeychainTokenStore.read() else {
            tasks = []
            events = []
            return
        }
        do {
            let project = projectID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? projectID
            async let loadedTasks: [TaskSummary] = get("/v1/projects/\(project)/tasks", token: token)
            async let loadedEvents: [EventSummary] = get("/v1/projects/\(project)/events?limit=20", token: token)
            tasks = try await loadedTasks
            events = try await loadedEvents
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func get<T: Decodable>(_ path: String, token: String) async throws -> T {
        guard let url = LocalAPIConfiguration.endpoint(baseURL: apiBaseURL, path: path) else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}
