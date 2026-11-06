import SwiftUI

struct MenuBarDashboard: View {
    @EnvironmentObject private var model: DashboardModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Memory OS").font(.title2.weight(.semibold))
                    Label(model.statusText, systemImage: model.isOnline ? "checkmark.circle.fill" : "circle.dashed")
                        .font(.caption).foregroundStyle(model.isOnline ? .green : .secondary)
                }
                Spacer()
                Button {
                    Task { await model.refresh() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .help("Refresh from the local backend")
            }

            if !model.projects.isEmpty {
                Picker("Project", selection: $model.selectedProjectID) {
                    ForEach(model.projects) { project in
                        Text(project.name).tag(Optional(project.id))
                    }
                }
                .onChange(of: model.selectedProjectID) { _, _ in
                    Task { await model.refreshProjectData() }
                }
            }

            GroupBox("Tasks") {
                if model.tasks.isEmpty {
                    Text("No recorded tasks for this project.").foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(model.tasks) { task in
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack(alignment: .firstTextBaseline) {
                                        Text(task.title).fontWeight(.medium)
                                        Spacer(minLength: 8)
                                        Text(task.status.replacingOccurrences(of: "_", with: " "))
                                            .font(.caption).foregroundStyle(.secondary)
                                    }
                                    if let details = task.details, !details.isEmpty {
                                        Text(details).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                                    }
                                }
                                Divider()
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(maxHeight: 180)
                }
            }

            GroupBox("Recent observed activity") {
                if model.events.isEmpty {
                    Text("No recent activity recorded.").foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(model.events) { event in
                                HStack(alignment: .top, spacing: 8) {
                                    Image(systemName: "circle.fill").font(.system(size: 6)).padding(.top, 5).foregroundStyle(.blue)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(event.event_type).fontWeight(.medium)
                                        Text("\(event.occurred_at) · \(event.source)").font(.caption2).foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(maxHeight: 160)
                }
            }

            if let error = model.lastError {
                Text(error).font(.caption).foregroundStyle(.red).lineLimit(3)
            }
            HStack {
                Text("Progress is based only on recorded task states.").font(.caption2).foregroundStyle(.secondary)
                Spacer()
                SettingsLink { Text("Settings…") }
            }
        }
        .padding(16)
        .task { await model.refresh() }
    }
}
