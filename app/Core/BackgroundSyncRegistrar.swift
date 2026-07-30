import BackgroundTasks
import Foundation
import HealthKit
import OSLog

protocol BackgroundSyncRegistering: Sendable {
    func registerAppRefresh()
    func registerObservers(for metrics: [HealthMetric]) async
    func scheduleAppRefresh()
}

final class BackgroundSyncRegistrar: BackgroundSyncRegistering, @unchecked Sendable {
    static let refreshIdentifier = "com.timoknapp.apple-health-exporter.refresh"

    private let healthStore: HKHealthStore
    private let coordinator: SyncCoordinator
    private let logger = Logger(
        subsystem: "com.timoknapp.apple-health-exporter",
        category: "background"
    )
    private let lock = NSLock()
    private var observerQueries: [HKObserverQuery] = []

    init(healthStore: HKHealthStore, coordinator: SyncCoordinator) {
        self.healthStore = healthStore
        self.coordinator = coordinator
    }

    func registerAppRefresh() {
        let registered = BGTaskScheduler.shared.register(
            forTaskWithIdentifier: Self.refreshIdentifier,
            using: nil
        ) { [weak self] task in
            guard let self, let refreshTask = task as? BGAppRefreshTask else {
                task.setTaskCompleted(success: false)
                return
            }
            self.handle(refreshTask)
        }
        Task {
            do {
                try await coordinator.setBackgroundRegistration(
                    registered ? "App refresh registered" : "App refresh registration failed"
                )
            } catch {
                logger.error("Could not persist background registration status.")
            }
        }
    }

    func registerObservers(for metrics: [HealthMetric]) async {
        stopObservers()
        do {
            try await disableAllBackgroundDelivery()
        } catch {
            do {
                try await coordinator.noteOperationalError(error)
            } catch {
                logger.error("Could not persist background reset error.")
            }
        }
        let available = metrics.compactMap { metric -> (HealthMetric, HKSampleType)? in
            guard let type = metric.sampleType else { return nil }
            return (metric, type)
        }
        var enabledCount = 0
        var failures: [String] = []

        for (metric, sampleType) in available {
            let observer = HKObserverQuery(
                sampleType: sampleType,
                predicate: nil
            ) { [weak self] _, completion, error in
                guard let self else {
                    completion()
                    return
                }
                self.handleObserverEvent(
                    metric: metric,
                    initialError: error,
                    completion: completion
                )
            }
            healthStore.execute(observer)
            appendObserver(observer)

            do {
                try await enableBackgroundDelivery(for: sampleType)
                enabledCount += 1
            } catch {
                failures.append(metric.rawValue)
                do {
                    try await coordinator.noteOperationalError(error)
                } catch {
                    logger.error("Could not persist observer error status.")
                }
            }
        }

        let state: String
        if available.isEmpty {
            state = "No supported HealthKit types"
        } else if failures.isEmpty {
            state = "Enabled hourly for \(enabledCount) types"
        } else {
            state = "Enabled \(enabledCount)/\(available.count); failed: \(failures.joined(separator: ", "))"
        }
        do {
            try await coordinator.setBackgroundRegistration(state)
        } catch {
            logger.error("Could not persist observer registration status.")
        }
    }

    func scheduleAppRefresh() {
        BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: Self.refreshIdentifier)
        let request = BGAppRefreshTaskRequest(identifier: Self.refreshIdentifier)
        request.earliestBeginDate = Date().addingTimeInterval(60 * 60)
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            Task {
                do {
                    try await coordinator.noteOperationalError(error)
                } catch {
                    logger.error("Could not persist app refresh scheduling error.")
                }
            }
        }
    }

    private func handleObserverEvent(
        metric: HealthMetric,
        initialError: Error?,
        completion: @escaping () -> Void
    ) {
        Task {
            do {
                if let initialError {
                    throw initialError
                }
                try await coordinator.recordObserverEvent()
                _ = try await coordinator.queueIncremental(metric: metric)
            } catch {
                completion()
                do {
                    try await coordinator.noteOperationalError(error)
                } catch {
                    logger.error("Could not persist observer processing error.")
                }
                return
            }

            completion()
            do {
                try await coordinator.uploadPending(includePermanent: false)
            } catch {
                do {
                    try await coordinator.noteOperationalError(error)
                } catch {
                    logger.error("Could not persist observer upload error.")
                }
            }
        }
    }

    private func handle(_ task: BGAppRefreshTask) {
        scheduleAppRefresh()
        let completionGate = BackgroundTaskCompletionGate(task: task)
        let operation = Task {
            do {
                let result = try await coordinator.syncNow()
                completionGate.complete(success: result.failures.isEmpty)
            } catch {
                do {
                    try await coordinator.noteOperationalError(error)
                } catch {
                    logger.error("Could not persist app refresh error.")
                }
                completionGate.complete(success: false)
            }
        }
        task.expirationHandler = {
            operation.cancel()
            completionGate.complete(success: false)
        }
    }

    private func enableBackgroundDelivery(for type: HKObjectType) async throws {
        try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<Void, Error>) in
            healthStore.enableBackgroundDelivery(for: type, frequency: .hourly) {
                success,
                error in
                if let error {
                    continuation.resume(throwing: error)
                } else if success {
                    continuation.resume()
                } else {
                    continuation.resume(
                        throwing: MessageError(
                            message: "HealthKit did not enable background delivery."
                        )
                    )
                }
            }

        }
    }

    private func disableAllBackgroundDelivery() async throws {
        try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<Void, Error>) in
            healthStore.disableAllBackgroundDelivery { success, error in
                if let error {
                    continuation.resume(throwing: error)
                } else if success {
                    continuation.resume()
                } else {
                    continuation.resume(
                        throwing: MessageError(
                            message: "HealthKit did not reset background delivery."
                        )
                    )
                }
            }

        }
    }

    private func appendObserver(_ observer: HKObserverQuery) {
        lock.lock()
        observerQueries.append(observer)
        lock.unlock()
    }

    private func stopObservers() {
        lock.lock()
        let existing = observerQueries
        observerQueries.removeAll()
        lock.unlock()
        for query in existing {
            healthStore.stop(query)
        }
    }
}

private final class BackgroundTaskCompletionGate: @unchecked Sendable {
    private let task: BGAppRefreshTask
    private let lock = NSLock()
    private var completed = false

    init(task: BGAppRefreshTask) {
        self.task = task
    }

    func complete(success: Bool) {
        lock.lock()
        guard !completed else {
            lock.unlock()
            return
        }
        completed = true
        lock.unlock()
        task.setTaskCompleted(success: success)
    }
}
