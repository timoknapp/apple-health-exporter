import Foundation
import HealthKit

enum HealthMetric: String, Codable, CaseIterable, Sendable {
    case heartRateVariabilitySDNN
    case restingHeartRate
    case sleepAnalysis
    case respiratoryRate
    case appleSleepingWristTemperature
    case heartRateRecoveryOneMinute
    case vo2Max
    case oxygenSaturation
    case bodyMass
    case bodyFatPercentage
    case leanBodyMass
    case activeEnergyBurned
    case appleExerciseTime
    case appleStandTime
    case stepCount
    case physicalEffort
    case workout
    case heartRate
    case cyclingPower
    case cyclingCadence
    case cyclingSpeed

    var displayName: String {
        switch self {
        case .heartRateVariabilitySDNN: return "Heart rate variability (SDNN)"
        case .restingHeartRate: return "Resting heart rate"
        case .sleepAnalysis: return "Sleep analysis"
        case .respiratoryRate: return "Respiratory rate"
        case .appleSleepingWristTemperature: return "Sleeping wrist temperature"
        case .heartRateRecoveryOneMinute: return "One-minute heart rate recovery"
        case .vo2Max: return "VO2 max"
        case .oxygenSaturation: return "Blood oxygen saturation"
        case .bodyMass: return "Body mass"
        case .bodyFatPercentage: return "Body fat percentage"
        case .leanBodyMass: return "Lean body mass"
        case .activeEnergyBurned: return "Active energy"
        case .appleExerciseTime: return "Exercise time"
        case .appleStandTime: return "Stand time"
        case .stepCount: return "Steps"
        case .physicalEffort: return "Physical effort"
        case .workout: return "Workouts"
        case .heartRate: return "Workout heart rate"
        case .cyclingPower: return "Workout cycling power"
        case .cyclingCadence: return "Workout cycling cadence"
        case .cyclingSpeed: return "Workout cycling speed"
        }
    }

    var purpose: String {
        switch self {
        case .workout, .heartRate, .cyclingPower, .cyclingCadence, .cyclingSpeed:
            return "Training load and workout context"
        case .activeEnergyBurned, .appleExerciseTime, .appleStandTime, .stepCount, .physicalEffort:
            return "Daily activity and movement"
        default:
            return "Recovery and fitness coaching"
        }
    }

    var isOptional: Bool {
        switch self {
        case .oxygenSaturation, .bodyMass, .bodyFatPercentage, .leanBodyMass:
            return true
        default:
            return false
        }
    }

    var workoutOnly: Bool {
        switch self {
        case .heartRate, .cyclingPower, .cyclingCadence, .cyclingSpeed:
            return true
        default:
            return false
        }
    }

    var sampleType: HKSampleType? {
        switch self {
        case .heartRateVariabilitySDNN:
            return HKObjectType.quantityType(forIdentifier: .heartRateVariabilitySDNN)
        case .restingHeartRate:
            return HKObjectType.quantityType(forIdentifier: .restingHeartRate)
        case .sleepAnalysis:
            return HKObjectType.categoryType(forIdentifier: .sleepAnalysis)
        case .respiratoryRate:
            return HKObjectType.quantityType(forIdentifier: .respiratoryRate)
        case .appleSleepingWristTemperature:
            if #available(iOS 16.0, *) {
                return HKObjectType.quantityType(forIdentifier: .appleSleepingWristTemperature)
            }
            return nil
        case .heartRateRecoveryOneMinute:
            if #available(iOS 16.0, *) {
                return HKObjectType.quantityType(forIdentifier: .heartRateRecoveryOneMinute)
            }
            return nil
        case .vo2Max:
            return HKObjectType.quantityType(forIdentifier: .vo2Max)
        case .oxygenSaturation:
            return HKObjectType.quantityType(forIdentifier: .oxygenSaturation)
        case .bodyMass:
            return HKObjectType.quantityType(forIdentifier: .bodyMass)
        case .bodyFatPercentage:
            return HKObjectType.quantityType(forIdentifier: .bodyFatPercentage)
        case .leanBodyMass:
            return HKObjectType.quantityType(forIdentifier: .leanBodyMass)
        case .activeEnergyBurned:
            return HKObjectType.quantityType(forIdentifier: .activeEnergyBurned)
        case .appleExerciseTime:
            return HKObjectType.quantityType(forIdentifier: .appleExerciseTime)
        case .appleStandTime:
            return HKObjectType.quantityType(forIdentifier: .appleStandTime)
        case .stepCount:
            return HKObjectType.quantityType(forIdentifier: .stepCount)
        case .physicalEffort:
            if #available(iOS 17.0, *) {
                return HKObjectType.quantityType(forIdentifier: .physicalEffort)
            }
            return nil
        case .workout:
            return HKObjectType.workoutType()
        case .heartRate:
            return HKObjectType.quantityType(forIdentifier: .heartRate)
        case .cyclingPower:
            if #available(iOS 17.0, *) {
                return HKObjectType.quantityType(forIdentifier: .cyclingPower)
            }
            return nil
        case .cyclingCadence:
            if #available(iOS 17.0, *) {
                return HKObjectType.quantityType(forIdentifier: .cyclingCadence)
            }
            return nil
        case .cyclingSpeed:
            if #available(iOS 17.0, *) {
                return HKObjectType.quantityType(forIdentifier: .cyclingSpeed)
            }
            return nil
        }
    }

    var canonicalUnit: CanonicalUnit {
        switch self {
        case .heartRateVariabilitySDNN:
            return .init(symbol: "ms", healthKitUnit: .secondUnit(with: .milli))
        case .restingHeartRate, .heartRateRecoveryOneMinute, .heartRate, .cyclingCadence:
            return .init(symbol: "count/min", healthKitUnit: .count().unitDivided(by: .minute()))
        case .sleepAnalysis:
            return .category
        case .respiratoryRate:
            return .init(symbol: "count/min", healthKitUnit: .count().unitDivided(by: .minute()))
        case .appleSleepingWristTemperature:
            return .init(symbol: "degC", healthKitUnit: .degreeCelsius())
        case .vo2Max:
            let unit = HKUnit.literUnit(with: .milli)
                .unitDivided(by: .gramUnit(with: .kilo))
                .unitDivided(by: .minute())
            return .init(symbol: "mL/(kg*min)", healthKitUnit: unit)
        case .oxygenSaturation, .bodyFatPercentage:
            return .init(symbol: "%", healthKitUnit: .percent(), scale: 100)
        case .bodyMass, .leanBodyMass:
            return .init(symbol: "kg", healthKitUnit: .gramUnit(with: .kilo))
        case .activeEnergyBurned:
            return .init(symbol: "kcal", healthKitUnit: .kilocalorie())
        case .appleExerciseTime, .appleStandTime:
            return .init(symbol: "min", healthKitUnit: .minute())
        case .stepCount:
            return .init(symbol: "count", healthKitUnit: .count())
        case .physicalEffort:
            let unit = HKUnit.kilocalorie()
                .unitDivided(by: .gramUnit(with: .kilo))
                .unitDivided(by: .hour())
            return .init(symbol: "MET", healthKitUnit: unit)
        case .workout:
            return .workout
        case .cyclingPower:
            return .init(symbol: "W", healthKitUnit: .watt())
        case .cyclingSpeed:
            return .init(symbol: "m/s", healthKitUnit: .meter().unitDivided(by: .second()))
        }
    }

    func isEnabled(by selection: MetricSelection) -> Bool {
        switch self {
        case .oxygenSaturation:
            return selection.oxygenSaturation
        case .bodyMass:
            return selection.bodyMass
        case .bodyFatPercentage:
            return selection.bodyFatPercentage
        case .leanBodyMass:
            return selection.leanBodyMass
        default:
            return true
        }
    }
}

struct CanonicalUnit: @unchecked Sendable {
    let symbol: String
    let healthKitUnit: HKUnit?
    let scale: Double

    init(symbol: String, healthKitUnit: HKUnit?, scale: Double = 1) {
        self.symbol = symbol
        self.healthKitUnit = healthKitUnit
        self.scale = scale
    }

    static let category = CanonicalUnit(symbol: "category-code", healthKitUnit: nil)
    static let workout = CanonicalUnit(symbol: "workout", healthKitUnit: nil)

    func value(from quantity: HKQuantity) throws -> Double {
        guard let healthKitUnit else {
            throw ExporterError.unsupportedType(symbol)
        }
        return quantity.doubleValue(for: healthKitUnit) * scale
    }
}

enum HealthTypeRegistry {
    struct Availability: Equatable, Sendable {
        let available: [HealthMetric]
        let unavailable: [HealthMetric]
    }

    static func selectedMetrics(_ selection: MetricSelection) -> [HealthMetric] {
        HealthMetric.allCases.filter { $0.isEnabled(by: selection) }
    }

    static func availableMetrics(_ selection: MetricSelection) -> [HealthMetric] {
        availability(for: selectedMetrics(selection)).available
    }

    static func unavailableMetrics(_ selection: MetricSelection) -> [HealthMetric] {
        availability(for: selectedMetrics(selection)).unavailable
    }

    static func availability(
        for metrics: [HealthMetric],
        isAvailable: (HealthMetric) -> Bool = { $0.sampleType != nil }
    ) -> Availability {
        Availability(
            available: metrics.filter(isAvailable),
            unavailable: metrics.filter { !isAvailable($0) }
        )
    }

    static func metric(forKey key: String) -> HealthMetric? {
        HealthMetric(rawValue: key)
    }
}

enum ReadAuthorizationInterpretation: String, Equatable, Sendable {
    case notRequested
    case requestCompletedReadAvailabilityUnknown
}

struct AuthorizationOutcome: Equatable, Sendable {
    let requestedMetricKeys: [String]
    let unavailableMetricKeys: [String]
    let interpretation: ReadAuthorizationInterpretation
}

protocol HealthAuthorizing: Sendable {
    var isHealthDataAvailable: Bool { get }
    func requestReadAuthorization(for metrics: [HealthMetric]) async throws -> AuthorizationOutcome
}

final class HealthAuthorizationService: HealthAuthorizing, @unchecked Sendable {
    private let healthStore: HKHealthStore

    init(healthStore: HKHealthStore) {
        self.healthStore = healthStore
    }

    var isHealthDataAvailable: Bool {
        HKHealthStore.isHealthDataAvailable()
    }

    func requestReadAuthorization(for metrics: [HealthMetric]) async throws -> AuthorizationOutcome {
        guard isHealthDataAvailable else {
            throw ExporterError.healthDataUnavailable
        }

        let available = metrics.compactMap { metric -> (HealthMetric, HKSampleType)? in
            guard let type = metric.sampleType else { return nil }
            return (metric, type)
        }
        let readTypes = Set<HKObjectType>(available.map(\.1))

        try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<Void, Error>) in
            healthStore.requestAuthorization(toShare: Set<HKSampleType>(), read: readTypes) { success, error in
                if let error {
                    continuation.resume(throwing: error)
                } else if success {
                    continuation.resume()
                } else {
                    continuation.resume(throwing: ExporterError.healthDataUnavailable)
                }
            }
        }

        return AuthorizationOutcome(
            requestedMetricKeys: available.map(\.0.rawValue),
            unavailableMetricKeys: metrics.filter { $0.sampleType == nil }.map(\.rawValue),
            interpretation: .requestCompletedReadAvailabilityUnknown
        )
    }
}
