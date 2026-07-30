import Foundation
import Security

protocol CredentialStoring: Sendable {
    func apiToken() throws -> String?
    func setAPIToken(_ token: String) throws
    func deviceID() throws -> UUID
}

final class KeychainCredentialStore: CredentialStoring, @unchecked Sendable {
    private let service: String
    private let lock = NSLock()

    init(service: String = "com.timoknapp.apple-health-exporter") {
        self.service = service
    }

    func apiToken() throws -> String? {
        try read(account: "api-token").flatMap { String(data: $0, encoding: .utf8) }
    }

    func setAPIToken(_ token: String) throws {
        let normalized = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if normalized.isEmpty {
            try delete(account: "api-token")
        } else {
            try write(Data(normalized.utf8), account: "api-token")
        }
    }

    func deviceID() throws -> UUID {
        lock.lock()
        defer { lock.unlock() }
        if let data = try read(account: "device-id"),
           let value = String(data: data, encoding: .utf8),
           let id = UUID(uuidString: value) {
            return id
        }
        let id = UUID()
        try write(Data(id.uuidString.utf8), account: "device-id")
        return id
    }

    private func read(account: String) throws -> Data? {
        var query = baseQuery(account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess, let data = result as? Data else {
            throw ExporterError.persistence("Keychain read error \(status)")
        }
        return data
    }

    private func write(_ data: Data, account: String) throws {
        let query = baseQuery(account: account)
        let attributes = [kSecValueData as String: data]
        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess {
            return
        }
        guard updateStatus == errSecItemNotFound else {
            throw ExporterError.persistence("Keychain update error \(updateStatus)")
        }
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let addStatus = SecItemAdd(item as CFDictionary, nil)
        guard addStatus == errSecSuccess else {
            throw ExporterError.persistence("Keychain write error \(addStatus)")
        }
    }

    private func delete(account: String) throws {
        let status = SecItemDelete(baseQuery(account: account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw ExporterError.persistence("Keychain delete error \(status)")
        }
    }

    private func baseQuery(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
    }
}

protocol SettingsStoring: Sendable {
    func loadSettings() async throws -> AppSettings
    func saveSettings(_ settings: AppSettings) async throws
    func loadConsent() async throws -> ConsentReceipt?
    func saveConsent(_ receipt: ConsentReceipt?) async throws
}

actor UserDefaultsSettingsStore: SettingsStoring {
    private let defaults: UserDefaults
    private let settingsKey = "exporter-settings-v1"
    private let consentKey = "exporter-consent-v1"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func loadSettings() throws -> AppSettings {
        guard let data = defaults.data(forKey: settingsKey) else {
            return AppSettings()
        }
        return try JSONDecoder.exporter.decode(AppSettings.self, from: data)
    }

    func saveSettings(_ settings: AppSettings) throws {
        defaults.set(try JSONEncoder.exporter.encode(settings), forKey: settingsKey)
    }

    func loadConsent() throws -> ConsentReceipt? {
        guard let data = defaults.data(forKey: consentKey) else {
            return nil
        }
        return try JSONDecoder.exporter.decode(ConsentReceipt.self, from: data)
    }

    func saveConsent(_ receipt: ConsentReceipt?) throws {
        if let receipt {
            defaults.set(try JSONEncoder.exporter.encode(receipt), forKey: consentKey)
        } else {
            defaults.removeObject(forKey: consentKey)
        }
    }
}

enum StoragePaths {
    static func productionRoot(fileManager: FileManager = .default) throws -> URL {
        guard let applicationSupport = fileManager.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            throw ExporterError.persistence("Application Support is unavailable.")
        }
        return applicationSupport.appendingPathComponent("HealthKitExporter", isDirectory: true)
    }

    static func prepareDirectory(_ url: URL, fileManager: FileManager = .default) throws {
        try fileManager.createDirectory(at: url, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var mutableURL = url
        try mutableURL.setResourceValues(values)
        try fileManager.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
    }
}

enum AtomicFile {
    static func write(_ data: Data, to url: URL, fileManager: FileManager = .default) throws {
        try StoragePaths.prepareDirectory(url.deletingLastPathComponent(), fileManager: fileManager)
        try data.write(to: url, options: [.atomic])
        try fileManager.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
    }
}

protocol AnchorStoring: Sendable {
    func state(for metricKey: String) async throws -> QueryAnchorState?
    func save(_ state: QueryAnchorState, for metricKey: String) async throws
    func resetAll() async throws
    func storedMetricKeys() async throws -> [String]
}

actor FileAnchorStore: AnchorStoring {
    private let fileURL: URL
    private let legacyFileURLs: [URL]
    private let fileManager: FileManager

    init(root: URL, fileManager: FileManager = .default) {
        self.fileURL = root.appendingPathComponent("anchors-v4.json")
        self.legacyFileURLs = [
            root.appendingPathComponent("anchors-v1.json"),
            root.appendingPathComponent("anchors-v2.json"),
            root.appendingPathComponent("anchors-v3.json")
        ]
        self.fileManager = fileManager
    }

    func state(for metricKey: String) throws -> QueryAnchorState? {
        try load()[metricKey]
    }

    func save(_ state: QueryAnchorState, for metricKey: String) throws {
        var anchors = try load()
        anchors[metricKey] = state
        try AtomicFile.write(
            JSONEncoder.exporter.encode(anchors),
            to: fileURL,
            fileManager: fileManager
        )
    }

    func resetAll() throws {
        for url in [fileURL] + legacyFileURLs {
            if fileManager.fileExists(atPath: url.path) {
                try fileManager.removeItem(at: url)
            }
        }
    }

    func storedMetricKeys() throws -> [String] {
        try load().keys.sorted()
    }

    private func load() throws -> [String: QueryAnchorState] {
        guard fileManager.fileExists(atPath: fileURL.path) else { return [:] }
        return try JSONDecoder.exporter.decode(
            [String: QueryAnchorState].self,
            from: Data(contentsOf: fileURL)
        )
    }
}

protocol OutboxStoring: Sendable {
    @discardableResult
    func enqueue(
        batchId: UUID,
        payload: Data,
        requiredMetricKeys: [String],
        createdAt: Date
    ) async throws -> Bool
    func pending(
        now: Date,
        includePermanent: Bool,
        limit: Int
    ) async throws -> [OutboxItem]
    func remove(batchId: UUID) async throws
    func markRetry(batchId: UUID, nextAttemptAt: Date, error: String) async throws
    func markPermanent(batchId: UUID, error: String) async throws
    func clear() async throws
    func count() async throws -> Int
}

actor FileOutboxStore: OutboxStoring {
    private struct Metadata: Decodable {
        let batchId: UUID
        let createdAt: Date
        let nextAttemptAt: Date
        let permanentFailure: Bool

        init(item: OutboxItem) {
            batchId = item.batchId
            createdAt = item.createdAt
            nextAttemptAt = item.nextAttemptAt
            permanentFailure = item.permanentFailure
        }
    }

    private let directory: URL
    private let fileManager: FileManager
    private var metadataCache: [UUID: Metadata]?

    init(root: URL, fileManager: FileManager = .default) {
        self.directory = root.appendingPathComponent("Outbox", isDirectory: true)
        self.fileManager = fileManager
    }

    @discardableResult
    func enqueue(
        batchId: UUID,
        payload: Data,
        requiredMetricKeys: [String],
        createdAt: Date
    ) throws -> Bool {
        let url = itemURL(batchId)
        if fileManager.fileExists(atPath: url.path) {
            let existing = try read(url)
            guard existing.batchId == batchId else {
                throw ExporterError.persistence("A deterministic batch ID collision was detected.")
            }
            return false
        }
        let item = OutboxItem(
            batchId: batchId,
            payload: payload,
            requiredMetricKeys: requiredMetricKeys.sorted(),
            createdAt: createdAt,
            attemptCount: 0,
            nextAttemptAt: .distantPast,
            permanentFailure: false,
            lastError: nil
        )
        try write(item)
        return true
    }

    func pending(
        now: Date,
        includePermanent: Bool,
        limit: Int
    ) throws -> [OutboxItem] {
        guard limit > 0 else { return [] }
        let selected = try loadMetadata().values
            .filter {
                includePermanent
                    ? true
                    : !$0.permanentFailure && $0.nextAttemptAt <= now
            }
            .sorted {
                if $0.createdAt == $1.createdAt {
                    return $0.batchId.uuidString < $1.batchId.uuidString
                }
                return $0.createdAt < $1.createdAt
            }
            .prefix(limit)
        return try selected.map { try read(itemURL($0.batchId)) }
    }

    func remove(batchId: UUID) throws {
        let url = itemURL(batchId)
        if fileManager.fileExists(atPath: url.path) {
            try fileManager.removeItem(at: url)
        }
        metadataCache?.removeValue(forKey: batchId)
    }

    func markRetry(batchId: UUID, nextAttemptAt: Date, error: String) throws {
        var item = try read(itemURL(batchId))
        item.attemptCount += 1
        item.nextAttemptAt = nextAttemptAt
        item.permanentFailure = false
        item.lastError = error
        try write(item)
    }

    func markPermanent(batchId: UUID, error: String) throws {
        var item = try read(itemURL(batchId))
        item.attemptCount += 1
        item.nextAttemptAt = .distantFuture
        item.permanentFailure = true
        item.lastError = error
        try write(item)
    }

    func clear() throws {
        if fileManager.fileExists(atPath: directory.path) {
            try fileManager.removeItem(at: directory)
        }
        metadataCache = [:]
    }

    func count() throws -> Int {
        if let metadataCache {
            return metadataCache.count
        }
        return try itemURLs().count
    }

    private func loadMetadata() throws -> [UUID: Metadata] {
        if let metadataCache {
            return metadataCache
        }
        var loaded: [UUID: Metadata] = [:]
        for url in try itemURLs() {
            let metadata = try autoreleasepool {
                try JSONDecoder.exporter.decode(
                    Metadata.self,
                    from: Data(contentsOf: url, options: .mappedIfSafe)
                )
            }
            loaded[metadata.batchId] = metadata
        }
        metadataCache = loaded
        return loaded
    }

    private func itemURLs() throws -> [URL] {
        guard fileManager.fileExists(atPath: directory.path) else { return [] }
        return try fileManager.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil
        ).filter { $0.pathExtension == "outbox" }
    }

    private func itemURL(_ batchId: UUID) -> URL {
        directory
            .appendingPathComponent(batchId.uuidString.lowercased())
            .appendingPathExtension("outbox")
    }

    private func read(_ url: URL) throws -> OutboxItem {
        try JSONDecoder.exporter.decode(OutboxItem.self, from: Data(contentsOf: url))
    }

    private func write(_ item: OutboxItem) throws {
        try AtomicFile.write(
            JSONEncoder.exporter.encode(item),
            to: itemURL(item.batchId),
            fileManager: fileManager
        )
        metadataCache?.updateValue(Metadata(item: item), forKey: item.batchId)
    }
}

protocol BackfillStateStoring: Sendable {
    func load() async throws -> BackfillCheckpoint?
    func save(_ checkpoint: BackfillCheckpoint) async throws
    func clear() async throws
}

actor FileBackfillStateStore: BackfillStateStoring {
    private let fileURL: URL
    private let fileManager: FileManager

    init(root: URL, fileManager: FileManager = .default) {
        self.fileURL = root.appendingPathComponent("backfill-v1.json")
        self.fileManager = fileManager
    }

    func load() throws -> BackfillCheckpoint? {
        guard fileManager.fileExists(atPath: fileURL.path) else { return nil }
        return try JSONDecoder.exporter.decode(
            BackfillCheckpoint.self,
            from: Data(contentsOf: fileURL)
        )
    }

    func save(_ checkpoint: BackfillCheckpoint) throws {
        try AtomicFile.write(
            JSONEncoder.exporter.encode(checkpoint),
            to: fileURL,
            fileManager: fileManager
        )
    }

    func clear() throws {
        guard fileManager.fileExists(atPath: fileURL.path) else { return }
        try fileManager.removeItem(at: fileURL)
    }
}

protocol StatusStoring: Sendable {
    func load() async throws -> OperationalStatus
    func save(_ status: OperationalStatus) async throws
}

actor FileStatusStore: StatusStoring {
    private let fileURL: URL
    private let fileManager: FileManager

    init(root: URL, fileManager: FileManager = .default) {
        self.fileURL = root.appendingPathComponent("status-v1.json")
        self.fileManager = fileManager
    }

    func load() throws -> OperationalStatus {
        guard fileManager.fileExists(atPath: fileURL.path) else {
            return OperationalStatus()
        }
        return try JSONDecoder.exporter.decode(
            OperationalStatus.self,
            from: Data(contentsOf: fileURL)
        )
    }

    func save(_ status: OperationalStatus) throws {
        try AtomicFile.write(
            JSONEncoder.exporter.encode(status),
            to: fileURL,
            fileManager: fileManager
        )
    }
}

protocol BackendDataStatusStoring: Sendable {
    func load() async throws -> BackendDataStatusCache?
    func save(_ cache: BackendDataStatusCache) async throws
}

actor FileBackendDataStatusStore: BackendDataStatusStoring {
    private let fileURL: URL
    private let fileManager: FileManager

    init(root: URL, fileManager: FileManager = .default) {
        self.fileURL = root.appendingPathComponent("backend-data-status-v1.json")
        self.fileManager = fileManager
    }

    func load() throws -> BackendDataStatusCache? {
        guard fileManager.fileExists(atPath: fileURL.path) else {
            return nil
        }
        return try JSONDecoder.exporter.decode(
            BackendDataStatusCache.self,
            from: Data(contentsOf: fileURL)
        )
    }

    func save(_ cache: BackendDataStatusCache) throws {
        try AtomicFile.write(
            JSONEncoder.exporter.encode(cache),
            to: fileURL,
            fileManager: fileManager
        )
    }
}
