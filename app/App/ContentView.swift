import SwiftUI
import UniformTypeIdentifiers
import UIKit

struct ContentView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        TabView {
            NavigationStack { SetupView() }
                .tabItem { Label("Setup", systemImage: "gearshape") }
            NavigationStack { AuthorizationView() }
                .tabItem { Label("Access", systemImage: "heart.text.square") }
            NavigationStack { StatusView() }
                .tabItem { Label("Status", systemImage: "arrow.triangle.2.circlepath") }
            NavigationStack { BackfillView() }
                .tabItem { Label("Backfill", systemImage: "clock.arrow.circlepath") }
            NavigationStack { DiagnosticsView() }
                .tabItem { Label("Diagnostics", systemImage: "stethoscope") }
        }
        .alert(
            "Health Exporter",
            isPresented: Binding(
                get: { model.alertMessage != nil },
                set: { if !$0 { model.alertMessage = nil } }
            )
        ) {
            Button("OK") { model.alertMessage = nil }
        } message: {
            Text(model.alertMessage ?? "")
        }
    }
}

private struct SetupView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Form {
            Section("Private backend") {
                TextField("https://coach.example", text: $model.settings.endpoint)
                    .keyboardType(.URL)
                    .textContentType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("API token", text: $model.apiToken)
                    .textContentType(.password)
                Text(endpointHelp)
                .font(.footnote)
                .foregroundStyle(.secondary)
            }

            Section {
                Toggle(
                    "Blood oxygen saturation",
                    isOn: $model.settings.metrics.oxygenSaturation
                )
                Toggle("Body mass", isOn: $model.settings.metrics.bodyMass)
                Toggle(
                    "Body fat percentage",
                    isOn: $model.settings.metrics.bodyFatPercentage
                )
                Toggle("Lean body mass", isOn: $model.settings.metrics.leanBodyMass)
            } header: {
                Text("Optional metrics")
            } footer: {
                Text(
                    "Recovery, daily activity, workouts, and workout-window measurements "
                    + "are included by default. No data is written to HealthKit."
                )
            }

            Section {
                Button("Save Configuration") {
                    Task { await model.saveConfiguration() }
                }
                Button("Test Connection") {
                    Task { await model.testConnection() }
                }
            }

            if let message = model.actionMessage {
                Section { Text(message).foregroundStyle(.secondary) }
            }
        }
        .disabled(model.isBusy)
        .navigationTitle("Setup")
    }

    private var endpointHelp: String {
#if DEBUG
        "The token is stored only in this device's Keychain. This debug build "
            + "accepts HTTP only for local/private test hosts; traffic and the "
            + "token are unencrypted. Release builds require HTTPS. Requests "
            + "are posted to /v1/healthkit/ingest."
#else
        "The token is stored only in this device's Keychain. "
            + "The app accepts HTTPS and posts to /v1/healthkit/ingest."
#endif
    }
}

private struct AuthorizationView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openURL) private var openURL

    var body: some View {
        List {
            Section("HealthKit read access") {
                Text(
                    "Access is requested only after you tap the button. HealthKit intentionally "
                    + "does not reveal whether individual read permissions were denied; denied "
                    + "access and no available data can look identical."
                )
                .font(.footnote)
                ForEach(model.selectedMetrics, id: \.rawValue) { metric in
                    VStack(alignment: .leading) {
                        Text(metric.displayName)
                        Text(metric.purpose)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Button("Request Read Authorization") {
                    Task { await model.requestAuthorization() }
                }
                Button("Open App Settings") {
                    openURL(URL(string: UIApplication.openSettingsURLString)!)
                }
                Text(model.authorizationMessage)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section("Upload consent") {
                Text("Destination: \(model.destinationOrigin)")
                    .fontWeight(.semibold)
                Text(
                    "Selected raw HealthKit samples, source/device context, allowlisted metadata, "
                    + "and deletions will be sent to this private host for fitness coaching. "
                    + "GPS routes and other excluded health categories are never exported."
                )
                .font(.footnote)
                Label(
                    model.consentValid
                        ? "Consent matches the current host and metric selection"
                        : "Consent is required before health uploads",
                    systemImage: model.consentValid ? "checkmark.shield" : "exclamationmark.shield"
                )
                Button("I Consent to Upload") {
                    Task { await model.grantConsent() }
                }
            }

            if !model.unavailableMetrics.isEmpty {
                Section("Unavailable on this device or OS") {
                    ForEach(model.unavailableMetrics, id: \.rawValue) {
                        Text($0.displayName)
                    }
                }
            }
        }
        .disabled(model.isBusy)
        .navigationTitle("Authorization")
    }
}

private struct StatusView: View {
    @EnvironmentObject private var model: AppModel
    @State private var confirmAnchorReset = false
    @State private var confirmQueueClear = false

    var body: some View {
        List {
            Section("Backend data") {
                if let cache = model.backendDataStatusCache {
                    Label(
                        cache.status.state.displayName,
                        systemImage: cache.status.state.systemImage
                    )
                    .foregroundStyle(cache.status.state.color)
                    StatusRow(
                        label: "Last checked",
                        value: dateText(cache.retrievedAt)
                    )
                    StatusRow(
                        label: "Last accepted upload",
                        value: dateText(cache.status.latestAcceptedBatchAt)
                    )
                    StatusRow(
                        label: "Latest stored record",
                        value: dateText(cache.status.latestRecordAt)
                    )
                    StatusRow(
                        label: "Active records",
                        value: cache.status.activeRecordCount.formatted()
                    )
                    StatusRow(
                        label: "Recovery",
                        value: domainText(cache.status.domains.recovery)
                    )
                    StatusRow(
                        label: "Sleep",
                        value: domainText(cache.status.domains.sleep)
                    )
                    StatusRow(
                        label: "Activity",
                        value: domainText(cache.status.domains.activity)
                    )
                    StatusRow(
                        label: "Workouts",
                        value: domainText(cache.status.domains.workouts)
                    )
                    ForEach(cache.status.warnings, id: \.self) { warning in
                        Label(warning, systemImage: "exclamationmark.triangle")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    Text("No backend data status has been retrieved yet.")
                        .foregroundStyle(.secondary)
                }
                if let message = model.backendDataStatusMessage {
                    Text(message)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Operational status") {
                StatusRow(
                    label: "Last observer event",
                    value: dateText(model.status.lastObserverEvent)
                )
                StatusRow(
                    label: "Last sync attempt",
                    value: dateText(model.status.lastSyncAttempt)
                )
                StatusRow(
                    label: "Last successful upload",
                    value: dateText(model.status.lastSuccessfulUpload)
                )
                StatusRow(
                    label: "Backend availability",
                    value: backendAvailabilityText
                )
                StatusRow(
                    label: "Pending batches",
                    value: String(model.status.pendingOutboxBatches)
                )
                StatusRow(
                    label: "Background delivery",
                    value: model.status.backgroundRegistration
                )
                StatusRow(
                    label: "Last redacted error",
                    value: model.status.lastRedactedError ?? "None"
                )
            }

            Section("Actions") {
                Button("Sync Now") { Task { await model.syncNow() } }
                Button("Retry Queue") { Task { await model.retryQueue() } }
                Button("Reset Anchors", role: .destructive) {
                    confirmAnchorReset = true
                }
                Button("Clear Local Queue", role: .destructive) {
                    confirmQueueClear = true
                }
            }

            if let message = model.actionMessage {
                Section { Text(message).foregroundStyle(.secondary) }
            }
        }
        .disabled(model.isBusy)
        .refreshable { await model.refresh() }
        .navigationTitle("Status")
        .confirmationDialog(
            "Reset every incremental HealthKit anchor?",
            isPresented: $confirmAnchorReset,
            titleVisibility: .visible
        ) {
            Button("Reset Anchors", role: .destructive) {
                Task { await model.resetAnchors() }
            }
        } message: {
            Text("The next sync can requeue historical samples. Existing outbox batches remain.")
        }
        .confirmationDialog(
            "Delete every locally queued upload?",
            isPresented: $confirmQueueClear,
            titleVisibility: .visible
        ) {
            Button("Clear Queue", role: .destructive) {
                Task { await model.clearQueue() }
            }
        } message: {
            Text("Queued health payloads not accepted by the server will be permanently removed.")
        }
    }

    private func dateText(_ date: Date?) -> String {
        date?.formatted(date: .abbreviated, time: .standard) ?? "Never"
    }

    private var backendAvailabilityText: String {
        guard let reachable = model.status.backendReachable else {
            return "Not checked"
        }
        let state = reachable ? "Reachable" : "Unavailable; uploads postponed"
        guard let checkedAt = model.status.lastBackendReachabilityCheck else {
            return state
        }
        return "\(state) (\(dateText(checkedAt)))"
    }

    private func domainText(_ domain: BackendDataDomainStatus) -> String {
        let count = "\(domain.recordCount.formatted()) records"
        guard let latest = domain.latestRecordAt else {
            return "\(domain.state.displayName) · \(count)"
        }
        return "\(domain.state.displayName) · \(count) · \(dateText(latest))"
    }
}

private extension BackendDataState {
    var displayName: String {
        switch self {
        case .current:
            "Current"
        case .stale:
            "Stale"
        case .empty:
            "Empty"
        }
    }

    var systemImage: String {
        switch self {
        case .current:
            "checkmark.circle.fill"
        case .stale:
            "clock.badge.exclamationmark"
        case .empty:
            "tray"
        }
    }

    var color: Color {
        switch self {
        case .current:
            .green
        case .stale:
            .orange
        case .empty:
            .secondary
        }
    }
}

private struct StatusRow: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).textSelection(.enabled)
        }
    }
}

private struct BackfillView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        Form {
            Section("Bounded historical export") {
                DatePicker(
                    "Start",
                    selection: $model.backfillStart,
                    in: ...model.backfillEnd,
                    displayedComponents: [.date]
                )
                DatePicker(
                    "End",
                    selection: $model.backfillEnd,
                    in: model.backfillStart...Date(),
                    displayedComponents: [.date]
                )
                Text(
                    "The exporter processes one calendar month at a time and pages each "
                    + "HealthKit type into durable, bounded batches. Incremental anchors are untouched."
                )
                .font(.footnote)
                .foregroundStyle(.secondary)
            }

            if let progress = model.backfillProgress {
                Section("Progress") {
                    ProgressView(
                        "Metric \(progress.currentMetric) of \(progress.totalMetrics)"
                    )
                    Text(
                        "Current chunk: \(progress.chunkStart.formatted(date: .abbreviated, time: .omitted))"
                    )
                    Text("Queued batches: \(progress.queuedBatches)")
                    Text("Processed records: \(progress.processedRecords)")
                }
            }

            Section {
                if model.isBackfillRunning {
                    Button("Pause Backfill", role: .cancel) {
                        model.cancelBackfill()
                    }
                } else {
                    Button("Start New Backfill") { model.startBackfill() }
                    if model.hasResumableBackfill {
                        Button("Resume Saved Backfill") { model.resumeBackfill() }
                    }
                }
            }

            if let message = model.actionMessage {
                Section { Text(message).foregroundStyle(.secondary) }
            }
        }
        .navigationTitle("Backfill")
    }
}

private struct DiagnosticsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var isExporting = false
    @State private var document = DiagnosticsDocument(text: "")

    var body: some View {
        List {
            Section {
                Text(
                    "Diagnostics contain operational timestamps, type names, queue counts, "
                    + "and the destination host. Tokens, device IDs, health values, sample IDs, "
                    + "and payload bodies are omitted or redacted."
                )
                Button("Export Redacted Diagnostics") {
                    Task {
                        if await model.prepareDiagnostics() {
                            document = DiagnosticsDocument(text: model.diagnosticsText)
                            isExporting = true
                        }
                    }
                }
            }

            Section("Platform limitations") {
                Text(
                    "HealthKit background delivery is unavailable in the simulator and is "
                    + "best effort on iPhone. It may be delayed, especially while the device "
                    + "is locked, in Low Power Mode, or after force-quit."
                )
            }
        }
        .navigationTitle("Diagnostics")
        .fileExporter(
            isPresented: $isExporting,
            document: document,
            contentType: .plainText,
            defaultFilename: "healthkit-exporter-diagnostics"
        ) { result in
            if case .failure(let error) = result {
                model.alertMessage = error.localizedDescription
            }
        }
    }
}

private struct DiagnosticsDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.plainText] }
    let text: String

    init(text: String) {
        self.text = text
    }

    init(configuration: ReadConfiguration) throws {
        text = configuration.file.regularFileContents
            .flatMap { String(data: $0, encoding: .utf8) } ?? ""
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: Data(text.utf8))
    }
}
