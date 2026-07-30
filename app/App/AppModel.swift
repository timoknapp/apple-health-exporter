import Combine
import Foundation
import HealthKit

@MainActor
final class AppModel: ObservableObject {
    @Published var settings = AppSettings()
    @Published var apiToken = ""
    @Published var status = OperationalStatus()
    @Published var backendDataStatusCache: BackendDataStatusCache?
    @Published var backendDataStatusMessage: String?
    @Published var authorizationMessage =
        "Authorization has not been requested in this installation."
    @Published var consentValid = false
    @Published var isBusy = false
    @Published var alertMessage: String?
    @Published var actionMessage: String?
    @Published var backfillStart =
        Calendar.current.date(byAdding: .month, value: -12, to: Date()) ?? Date()
    @Published var backfillEnd = Date()
    @Published var backfillProgress: BackfillProgress?
    @Published var isBackfillRunning = false
    @Published var hasResumableBackfill = false
    @Published var diagnosticsText = ""

    private var coordinator: SyncCoordinator?
    private var settingsStore: SettingsStoring?
    private var credentialStore: CredentialStoring?
    private var authorizationService: HealthAuthorizing?
    private var backgroundRegistrar: BackgroundSyncRegistrar?
    private var backendDataStatusClient: BackendDataStatusFetching?
    private var backendDataStatusStore: BackendDataStatusStoring?
    private var backfillTask: Task<Void, Never>?
    private var automaticSyncInProgress = false

    init() {
        do {
            let root = try StoragePaths.productionRoot()
            try StoragePaths.prepareDirectory(root)
            let healthStore = HKHealthStore()
            let settingsStore = UserDefaultsSettingsStore()
            let credentialStore = KeychainCredentialStore()
            let coordinator = SyncCoordinator(
                queryService: HealthQueryService(healthStore: healthStore),
                anchorStore: FileAnchorStore(root: root),
                outboxStore: FileOutboxStore(root: root),
                payloadEncoder: JSONPayloadEncoder(),
                backendAvailabilityChecker: BackendAvailabilityClient(),
                uploader: UploadClient(),
                settingsStore: settingsStore,
                credentialStore: credentialStore,
                backfillStore: FileBackfillStateStore(root: root),
                statusStore: FileStatusStore(root: root)
            )
            let backgroundRegistrar = BackgroundSyncRegistrar(
                healthStore: healthStore,
                coordinator: coordinator
            )

            self.coordinator = coordinator
            self.settingsStore = settingsStore
            self.credentialStore = credentialStore
            self.authorizationService = HealthAuthorizationService(healthStore: healthStore)
            self.backgroundRegistrar = backgroundRegistrar
            self.backendDataStatusClient = BackendDataStatusClient()
            self.backendDataStatusStore = FileBackendDataStatusStore(root: root)
            backgroundRegistrar.registerAppRefresh()

            Task { await loadInitialState() }
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    var selectedMetrics: [HealthMetric] {
        HealthTypeRegistry.selectedMetrics(settings.metrics)
    }

    var unavailableMetrics: [HealthMetric] {
        HealthTypeRegistry.unavailableMetrics(settings.metrics)
    }

    var destinationOrigin: String {
        (try? EndpointResolver.destinationOrigin(from: settings.endpoint))
            ?? "a valid HTTPS host has not been configured"
    }

    func saveConfiguration() async {
        await runUserAction(success: "Configuration saved.") {
            try await persistConfiguration()
        }
    }

    func testConnection() async {
        await runUserAction(success: "Connection accepted the empty test batch.") {
            try await persistConfiguration()
            guard let coordinator else { throw initializationError() }
            try await coordinator.testConnection()
            try await refreshStatus()
        }
    }

    func grantConsent() async {
        await runUserAction(success: "Upload consent recorded for the current host and metrics.") {
            try await persistConfiguration()
            guard let settingsStore else { throw initializationError() }
            let origin = try EndpointResolver.destinationOrigin(from: settings.endpoint)
            let receipt = ConsentReceipt(
                destinationOrigin: origin,
                selectedMetricKeys: selectedMetrics.map(\.rawValue),
                consentedAt: Date()
            )
            try await settingsStore.saveConsent(receipt)
            consentValid = true
        }
    }

    func requestAuthorization() async {
        await runUserAction(
            success: "The HealthKit request completed. HealthKit does not reveal which read permissions were denied."
        ) {
            try await persistConfiguration()
            guard let authorizationService else { throw initializationError() }
            let outcome = try await authorizationService.requestReadAuthorization(
                for: selectedMetrics
            )
            settings.authorizationRequested = true
            guard let settingsStore else { throw initializationError() }
            try await settingsStore.saveSettings(settings)
            authorizationMessage =
                "Requested \(outcome.requestedMetricKeys.count) supported types. "
                + "No-data and denied-read results remain intentionally indistinguishable."
            if !outcome.unavailableMetricKeys.isEmpty {
                authorizationMessage +=
                    " Unavailable: \(outcome.unavailableMetricKeys.joined(separator: ", "))."
            }
            await backgroundRegistrar?.registerObservers(for: selectedMetrics)
        }
    }

    func syncNow() async {
        await runUserAction(success: nil) {
            try await persistConfiguration()
            try ensureAuthorizationWasRequested()
            try await ensureCurrentConsent(requiredMetricKeys: selectedMetrics.map(\.rawValue))
            guard let coordinator else { throw initializationError() }
            let result = try await coordinator.syncNow()
            try await refreshStatus()
            if result.failures.isEmpty {
                if result.metricKeysWithMoreChanges.isEmpty {
                    actionMessage =
                        "Incremental data was queued and pending uploads were attempted."
                } else {
                    actionMessage =
                        "A bounded page was synced. More HealthKit changes remain; "
                        + "run Sync Now again to continue safely."
                }
            } else {
                actionMessage =
                    "Sync completed with isolated failures for: "
                    + result.failures.keys.sorted().joined(separator: ", ")
            }
        }
    }

    func retryQueue() async {
        await runUserAction(success: "The local queue was retried.") {
            try await persistConfiguration()
            try await ensureCurrentConsent(requiredMetricKeys: selectedMetrics.map(\.rawValue))
            guard let coordinator else { throw initializationError() }
            try await coordinator.uploadPending(includePermanent: true)
            try await refreshStatus()
        }
    }

    func resetAnchors() async {
        await runUserAction(success: "Incremental anchors were reset.") {
            guard let coordinator else { throw initializationError() }
            await backgroundRegistrar?.registerObservers(for: [])
            try await coordinator.resetAnchors()
            if settings.authorizationRequested {
                await backgroundRegistrar?.registerObservers(for: selectedMetrics)
            }
        }
    }

    func clearQueue() async {
        await runUserAction(success: "The local upload queue was cleared.") {
            guard let coordinator else { throw initializationError() }
            try await coordinator.clearQueue()
            try await refreshStatus()
        }
    }

    func startBackfill() {
        runBackfill(createNew: true)
    }

    func resumeBackfill() {
        runBackfill(createNew: false)
    }

    func cancelBackfill() {
        backfillTask?.cancel()
        if let coordinator {
            Task { await coordinator.cancelBackfill() }
        }
    }

    func prepareDiagnostics() async -> Bool {
        guard let coordinator else {
            alertMessage = initializationError().localizedDescription
            return false
        }
        do {
            diagnosticsText = try await coordinator.redactedDiagnostics()
            return true
        } catch {
            alertMessage = error.localizedDescription
            return false
        }
    }

    func appDidBecomeActive() {
        backgroundRegistrar?.scheduleAppRefresh()
        Task {
            await attemptAutomaticSync()
            do {
                try await refreshStatus()
            } catch {
                alertMessage = error.localizedDescription
            }
        }
    }

    func protectedDataDidBecomeAvailable() {
        Task { await attemptAutomaticSync() }
    }

    func refresh() async {
        do {
            try await refreshStatus()
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    private func loadInitialState() async {
        do {
            guard
                let settingsStore,
                let credentialStore,
                let coordinator
            else {
                throw initializationError()
            }
            settings = try await settingsStore.loadSettings()
            apiToken = try credentialStore.apiToken() ?? ""
            do {
                if let cache = try await backendDataStatusStore?.load(),
                   let origin = try? EndpointResolver.destinationOrigin(from: settings.endpoint),
                   cache.destinationOrigin.caseInsensitiveCompare(origin) == .orderedSame {
                    backendDataStatusCache = cache
                }
            } catch {
                backendDataStatusMessage = "Saved backend data status could not be loaded."
            }
            try await refreshConsent()
            status = try await coordinator.currentStatus()
            hasResumableBackfill = try await coordinator.backfillCheckpoint() != nil
            if settings.authorizationRequested {
                await backgroundRegistrar?.registerObservers(for: selectedMetrics)
            }
            backgroundRegistrar?.scheduleAppRefresh()
            await attemptAutomaticSync()
            try await refreshStatus()
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    private func persistConfiguration() async throws {
        _ = try EndpointResolver.ingestURL(from: settings.endpoint)
        guard !apiToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw ExporterError.missingToken
        }
        guard let settingsStore, let credentialStore else {
            throw initializationError()
        }
        let previous = try await settingsStore.loadSettings()
        let origin = try EndpointResolver.destinationOrigin(from: settings.endpoint)
        if backendDataStatusCache?.destinationOrigin.caseInsensitiveCompare(origin)
            != .orderedSame {
            backendDataStatusCache = nil
            backendDataStatusMessage = nil
        }
        if previous.metrics != settings.metrics {
            settings.authorizationRequested = false
            await backgroundRegistrar?.registerObservers(for: [])
        }
        try await settingsStore.saveSettings(settings)
        try credentialStore.setAPIToken(apiToken)
        try await refreshConsent()
    }

    private func refreshConsent() async throws {
        guard let settingsStore else { throw initializationError() }
        guard
            let origin = try? EndpointResolver.destinationOrigin(from: settings.endpoint),
            let receipt = try await settingsStore.loadConsent()
        else {
            consentValid = false
            return
        }
        consentValid = receipt.matches(
            origin: origin,
            metricKeys: selectedMetrics.map(\.rawValue)
        )
    }

    private func runBackfill(createNew: Bool) {
        guard backfillTask == nil else { return }
        guard let coordinator else {
            alertMessage = initializationError().localizedDescription
            return
        }

        isBackfillRunning = true
        actionMessage = nil
        backfillTask = Task { [weak self] in
            guard let self else { return }
            do {
                try await persistConfiguration()
                try ensureAuthorizationWasRequested()
                let requiredMetricKeys: [String]
                if createNew {
                    requiredMetricKeys = selectedMetrics.map(\.rawValue)
                } else {
                    requiredMetricKeys =
                        try await coordinator.backfillCheckpoint()?.metricKeys
                        ?? selectedMetrics.map(\.rawValue)
                }
                try await ensureCurrentConsent(requiredMetricKeys: requiredMetricKeys)
                if createNew {
                    try await coordinator.startBackfill(
                        start: backfillStart,
                        end: backfillEnd,
                        metrics: selectedMetrics
                    )
                }
                try await coordinator.resumeBackfill { [weak self] progress in
                    Task { @MainActor in
                        self?.backfillProgress = progress
                    }
                }
                hasResumableBackfill = false
                backfillProgress = nil
                actionMessage = "Historical batches were queued. Pending uploads are being attempted."
                do {
                    try await coordinator.uploadPending(includePermanent: false)
                } catch {
                    try await coordinator.noteOperationalError(error)
                }
                try await refreshStatus()
            } catch ExporterError.cancelled {
                hasResumableBackfill = true
                actionMessage = "Backfill paused safely and can be resumed."
            } catch is CancellationError {
                hasResumableBackfill = true
                actionMessage = "Backfill paused safely and can be resumed."
            } catch {
                hasResumableBackfill =
                    (try? await coordinator.backfillCheckpoint()) != nil
                alertMessage = error.localizedDescription
            }
            isBackfillRunning = false
            backfillTask = nil
        }
    }

    private func attemptAutomaticSync() async {
        guard !automaticSyncInProgress else { return }
        automaticSyncInProgress = true
        defer { automaticSyncInProgress = false }
        guard let coordinator, let settingsStore, let credentialStore else { return }
        do {
            let storedSettings = try await settingsStore.loadSettings()
            let origin = try? EndpointResolver.destinationOrigin(
                from: storedSettings.endpoint
            )
            let selectedKeys = HealthTypeRegistry.selectedMetrics(
                storedSettings.metrics
            ).map(\.rawValue)
            let receipt = try await settingsStore.loadConsent()
            let hasToken = try credentialStore.apiToken()?.isEmpty == false
            if storedSettings.authorizationRequested,
               let origin,
               receipt?.matches(origin: origin, metricKeys: selectedKeys) == true,
               hasToken {
                _ = try await coordinator.syncNow()
            } else {
                try await coordinator.uploadPending(includePermanent: false)
            }
        } catch {
            do {
                try await coordinator.noteOperationalError(error)
            } catch {
                alertMessage = error.localizedDescription
            }
        }
    }

    private func refreshStatus() async throws {
        guard let coordinator else { throw initializationError() }
        status = try await coordinator.currentStatus()
        hasResumableBackfill = try await coordinator.backfillCheckpoint() != nil
        await refreshBackendDataStatus()
    }

    private func refreshBackendDataStatus() async {
        guard let backendDataStatusClient, let backendDataStatusStore else {
            backendDataStatusMessage = "Backend data status is unavailable in this installation."
            return
        }
        guard
            let endpoint = try? EndpointResolver.ingestURL(from: settings.endpoint),
            let origin = try? EndpointResolver.destinationOrigin(from: settings.endpoint),
            !apiToken.isEmpty
        else {
            backendDataStatusMessage = nil
            return
        }

        do {
            let snapshot = try await backendDataStatusClient.fetchStatus(
                at: endpoint,
                token: apiToken
            )
            let cache = BackendDataStatusCache(
                destinationOrigin: origin,
                retrievedAt: Date(),
                status: snapshot
            )
            backendDataStatusCache = cache
            backendDataStatusMessage = nil
            do {
                try await backendDataStatusStore.save(cache)
            } catch {
                backendDataStatusMessage =
                    "Status refreshed, but it could not be saved for offline display."
            }
        } catch is CancellationError {
            return
        } catch {
            backendDataStatusMessage =
                backendDataStatusCache == nil
                ? "Backend data status is currently unavailable."
                : "Showing the last known backend data status because refresh failed."
        }
    }

    private func ensureCurrentConsent(requiredMetricKeys: [String]) async throws {
        guard let settingsStore else { throw initializationError() }
        let origin = try EndpointResolver.destinationOrigin(from: settings.endpoint)
        guard let receipt = try await settingsStore.loadConsent(),
              receipt.matches(
                  origin: origin,
                  metricKeys: selectedMetrics.map(\.rawValue)
              ),
              Set(requiredMetricKeys).isSubset(of: Set(receipt.selectedMetricKeys)) else {
            consentValid = false
            throw ExporterError.consentRequired
        }
        consentValid = true
    }

    private func ensureAuthorizationWasRequested() throws {
        guard settings.authorizationRequested else {
            throw MessageError(
                message: "Request HealthKit read authorization before syncing health data."
            )
        }
    }

    private func runUserAction(
        success: String?,
        operation: () async throws -> Void
    ) async {
        guard !isBusy else { return }
        isBusy = true
        actionMessage = nil
        defer { isBusy = false }
        do {
            try await operation()
            if let success {
                actionMessage = success
            }
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    private func initializationError() -> MessageError {
        MessageError(message: "The exporter services could not be initialized.")
    }
}
