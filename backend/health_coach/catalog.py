from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Aggregation(StrEnum):
    DAILY_MEDIAN = "daily_median"
    DAILY_MEAN = "daily_mean"
    DAILY_SUM = "daily_sum"
    LATEST = "latest"
    RAW = "raw"


class MetricName(StrEnum):
    HRV_SDNN = "hrv_sdnn"
    RESTING_HEART_RATE = "resting_heart_rate"
    RESPIRATORY_RATE = "respiratory_rate"
    SLEEPING_WRIST_TEMPERATURE = "sleeping_wrist_temperature"
    HEART_RATE_RECOVERY_ONE_MINUTE = "heart_rate_recovery_one_minute"
    VO2_MAX = "vo2_max"
    OXYGEN_SATURATION = "oxygen_saturation"
    BODY_MASS = "body_mass"
    BODY_FAT_PERCENTAGE = "body_fat_percentage"
    LEAN_BODY_MASS = "lean_body_mass"
    ACTIVE_ENERGY = "active_energy"
    EXERCISE_TIME = "exercise_time"
    STAND_TIME = "stand_time"
    STEP_COUNT = "step_count"
    PHYSICAL_EFFORT = "physical_effort"
    WORKOUT_HEART_RATE = "workout_heart_rate"
    CYCLING_POWER = "cycling_power"
    CYCLING_CADENCE = "cycling_cadence"
    CYCLING_SPEED = "cycling_speed"


class Sport(StrEnum):
    AMERICAN_FOOTBALL = "american_football"
    ARCHERY = "archery"
    AUSTRALIAN_FOOTBALL = "australian_football"
    BADMINTON = "badminton"
    BASEBALL = "baseball"
    BASKETBALL = "basketball"
    BOWLING = "bowling"
    BOXING = "boxing"
    CLIMBING = "climbing"
    CRICKET = "cricket"
    CROSS_TRAINING = "cross_training"
    CURLING = "curling"
    CYCLING = "cycling"
    DANCE = "dance"
    DANCE_INSPIRED_TRAINING = "dance_inspired_training"
    ELLIPTICAL = "elliptical"
    EQUESTRIAN_SPORTS = "equestrian_sports"
    FENCING = "fencing"
    FISHING = "fishing"
    FUNCTIONAL_STRENGTH_TRAINING = "functional_strength_training"
    GOLF = "golf"
    GYMNASTICS = "gymnastics"
    HANDBALL = "handball"
    HIKING = "hiking"
    HOCKEY = "hockey"
    HUNTING = "hunting"
    LACROSSE = "lacrosse"
    MARTIAL_ARTS = "martial_arts"
    MIND_AND_BODY = "mind_and_body"
    MIXED_METABOLIC_CARDIO_TRAINING = "mixed_metabolic_cardio_training"
    PADDLE_SPORTS = "paddle_sports"
    PLAY = "play"
    PREPARATION_AND_RECOVERY = "preparation_and_recovery"
    RACQUETBALL = "racquetball"
    ROWING = "rowing"
    RUGBY = "rugby"
    RUNNING = "running"
    SAILING = "sailing"
    SKATING_SPORTS = "skating_sports"
    SNOW_SPORTS = "snow_sports"
    SOCCER = "soccer"
    SOFTBALL = "softball"
    SQUASH = "squash"
    STAIR_CLIMBING = "stair_climbing"
    SURFING_SPORTS = "surfing_sports"
    SWIMMING = "swimming"
    TABLE_TENNIS = "table_tennis"
    TENNIS = "tennis"
    TRACK_AND_FIELD = "track_and_field"
    TRADITIONAL_STRENGTH_TRAINING = "traditional_strength_training"
    VOLLEYBALL = "volleyball"
    WALKING = "walking"
    WATER_FITNESS = "water_fitness"
    WATER_POLO = "water_polo"
    WATER_SPORTS = "water_sports"
    WRESTLING = "wrestling"
    YOGA = "yoga"
    BARRE = "barre"
    CORE_TRAINING = "core_training"
    CROSS_COUNTRY_SKIING = "cross_country_skiing"
    DOWNHILL_SKIING = "downhill_skiing"
    FLEXIBILITY = "flexibility"
    HIGH_INTENSITY_INTERVAL_TRAINING = "high_intensity_interval_training"
    JUMP_ROPE = "jump_rope"
    KICKBOXING = "kickboxing"
    PILATES = "pilates"
    SNOWBOARDING = "snowboarding"
    STAIRS = "stairs"
    STEP_TRAINING = "step_training"
    WHEELCHAIR_WALK_PACE = "wheelchair_walk_pace"
    WHEELCHAIR_RUN_PACE = "wheelchair_run_pace"
    TAI_CHI = "tai_chi"
    MIXED_CARDIO = "mixed_cardio"
    HAND_CYCLING = "hand_cycling"
    DISC_SPORTS = "disc_sports"
    FITNESS_GAMING = "fitness_gaming"
    CARDIO_DANCE = "cardio_dance"
    SOCIAL_DANCE = "social_dance"
    PICKLEBALL = "pickleball"
    COOLDOWN = "cooldown"
    SWIM_BIKE_RUN = "swim_bike_run"
    TRANSITION = "transition"
    UNDERWATER_DIVING = "underwater_diving"

    # Compatibility filters retained from the original broad sport catalog.
    STRENGTH = "strength"
    SKIING = "skiing"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: MetricName
    sample_type: str
    unit: str
    domain: str
    aggregations: tuple[Aggregation, ...]
    additive: bool = False


STANDARD_AGGREGATIONS = (
    Aggregation.DAILY_MEDIAN,
    Aggregation.DAILY_MEAN,
    Aggregation.LATEST,
    Aggregation.RAW,
)
ADDITIVE_AGGREGATIONS = (Aggregation.DAILY_SUM, Aggregation.RAW)

METRICS: dict[MetricName, MetricDefinition] = {
    MetricName.HRV_SDNN: MetricDefinition(
        MetricName.HRV_SDNN,
        "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
        "ms",
        "recovery",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.RESTING_HEART_RATE: MetricDefinition(
        MetricName.RESTING_HEART_RATE,
        "HKQuantityTypeIdentifierRestingHeartRate",
        "count/min",
        "recovery",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.RESPIRATORY_RATE: MetricDefinition(
        MetricName.RESPIRATORY_RATE,
        "HKQuantityTypeIdentifierRespiratoryRate",
        "count/min",
        "recovery",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.SLEEPING_WRIST_TEMPERATURE: MetricDefinition(
        MetricName.SLEEPING_WRIST_TEMPERATURE,
        "HKQuantityTypeIdentifierAppleSleepingWristTemperature",
        "degC",
        "recovery",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.HEART_RATE_RECOVERY_ONE_MINUTE: MetricDefinition(
        MetricName.HEART_RATE_RECOVERY_ONE_MINUTE,
        "HKQuantityTypeIdentifierHeartRateRecoveryOneMinute",
        "count/min",
        "recovery",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.VO2_MAX: MetricDefinition(
        MetricName.VO2_MAX,
        "HKQuantityTypeIdentifierVO2Max",
        "mL/(kg*min)",
        "recovery",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.OXYGEN_SATURATION: MetricDefinition(
        MetricName.OXYGEN_SATURATION,
        "HKQuantityTypeIdentifierOxygenSaturation",
        "%",
        "recovery",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.BODY_MASS: MetricDefinition(
        MetricName.BODY_MASS,
        "HKQuantityTypeIdentifierBodyMass",
        "kg",
        "recovery",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.BODY_FAT_PERCENTAGE: MetricDefinition(
        MetricName.BODY_FAT_PERCENTAGE,
        "HKQuantityTypeIdentifierBodyFatPercentage",
        "%",
        "recovery",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.LEAN_BODY_MASS: MetricDefinition(
        MetricName.LEAN_BODY_MASS,
        "HKQuantityTypeIdentifierLeanBodyMass",
        "kg",
        "recovery",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.ACTIVE_ENERGY: MetricDefinition(
        MetricName.ACTIVE_ENERGY,
        "HKQuantityTypeIdentifierActiveEnergyBurned",
        "kcal",
        "activity",
        ADDITIVE_AGGREGATIONS,
        additive=True,
    ),
    MetricName.EXERCISE_TIME: MetricDefinition(
        MetricName.EXERCISE_TIME,
        "HKQuantityTypeIdentifierAppleExerciseTime",
        "min",
        "activity",
        ADDITIVE_AGGREGATIONS,
        additive=True,
    ),
    MetricName.STAND_TIME: MetricDefinition(
        MetricName.STAND_TIME,
        "HKQuantityTypeIdentifierAppleStandTime",
        "min",
        "activity",
        ADDITIVE_AGGREGATIONS,
        additive=True,
    ),
    MetricName.STEP_COUNT: MetricDefinition(
        MetricName.STEP_COUNT,
        "HKQuantityTypeIdentifierStepCount",
        "count",
        "activity",
        ADDITIVE_AGGREGATIONS,
        additive=True,
    ),
    MetricName.PHYSICAL_EFFORT: MetricDefinition(
        MetricName.PHYSICAL_EFFORT,
        "HKQuantityTypeIdentifierPhysicalEffort",
        "MET",
        "activity",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.WORKOUT_HEART_RATE: MetricDefinition(
        MetricName.WORKOUT_HEART_RATE,
        "HKQuantityTypeIdentifierHeartRate",
        "count/min",
        "workouts",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.CYCLING_POWER: MetricDefinition(
        MetricName.CYCLING_POWER,
        "HKQuantityTypeIdentifierCyclingPower",
        "W",
        "workouts",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.CYCLING_CADENCE: MetricDefinition(
        MetricName.CYCLING_CADENCE,
        "HKQuantityTypeIdentifierCyclingCadence",
        "count/min",
        "workouts",
        STANDARD_AGGREGATIONS,
    ),
    MetricName.CYCLING_SPEED: MetricDefinition(
        MetricName.CYCLING_SPEED,
        "HKQuantityTypeIdentifierCyclingSpeed",
        "m/s",
        "workouts",
        STANDARD_AGGREGATIONS,
    ),
}

SLEEP_SAMPLE_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
WORKOUT_SAMPLE_TYPE = "HKWorkoutTypeIdentifier"

HEALTHKIT_ACTIVITY_TYPES: dict[int, Sport] = {
    1: Sport.AMERICAN_FOOTBALL,
    2: Sport.ARCHERY,
    3: Sport.AUSTRALIAN_FOOTBALL,
    4: Sport.BADMINTON,
    5: Sport.BASEBALL,
    6: Sport.BASKETBALL,
    7: Sport.BOWLING,
    8: Sport.BOXING,
    9: Sport.CLIMBING,
    10: Sport.CRICKET,
    11: Sport.CROSS_TRAINING,
    12: Sport.CURLING,
    13: Sport.CYCLING,
    14: Sport.DANCE,
    15: Sport.DANCE_INSPIRED_TRAINING,
    16: Sport.ELLIPTICAL,
    17: Sport.EQUESTRIAN_SPORTS,
    18: Sport.FENCING,
    19: Sport.FISHING,
    20: Sport.FUNCTIONAL_STRENGTH_TRAINING,
    21: Sport.GOLF,
    22: Sport.GYMNASTICS,
    23: Sport.HANDBALL,
    24: Sport.HIKING,
    25: Sport.HOCKEY,
    26: Sport.HUNTING,
    27: Sport.LACROSSE,
    28: Sport.MARTIAL_ARTS,
    29: Sport.MIND_AND_BODY,
    30: Sport.MIXED_METABOLIC_CARDIO_TRAINING,
    31: Sport.PADDLE_SPORTS,
    32: Sport.PLAY,
    33: Sport.PREPARATION_AND_RECOVERY,
    34: Sport.RACQUETBALL,
    35: Sport.ROWING,
    36: Sport.RUGBY,
    37: Sport.RUNNING,
    38: Sport.SAILING,
    39: Sport.SKATING_SPORTS,
    40: Sport.SNOW_SPORTS,
    41: Sport.SOCCER,
    42: Sport.SOFTBALL,
    43: Sport.SQUASH,
    44: Sport.STAIR_CLIMBING,
    45: Sport.SURFING_SPORTS,
    46: Sport.SWIMMING,
    47: Sport.TABLE_TENNIS,
    48: Sport.TENNIS,
    49: Sport.TRACK_AND_FIELD,
    50: Sport.TRADITIONAL_STRENGTH_TRAINING,
    51: Sport.VOLLEYBALL,
    52: Sport.WALKING,
    53: Sport.WATER_FITNESS,
    54: Sport.WATER_POLO,
    55: Sport.WATER_SPORTS,
    56: Sport.WRESTLING,
    57: Sport.YOGA,
    58: Sport.BARRE,
    59: Sport.CORE_TRAINING,
    60: Sport.CROSS_COUNTRY_SKIING,
    61: Sport.DOWNHILL_SKIING,
    62: Sport.FLEXIBILITY,
    63: Sport.HIGH_INTENSITY_INTERVAL_TRAINING,
    64: Sport.JUMP_ROPE,
    65: Sport.KICKBOXING,
    66: Sport.PILATES,
    67: Sport.SNOWBOARDING,
    68: Sport.STAIRS,
    69: Sport.STEP_TRAINING,
    70: Sport.WHEELCHAIR_WALK_PACE,
    71: Sport.WHEELCHAIR_RUN_PACE,
    72: Sport.TAI_CHI,
    73: Sport.MIXED_CARDIO,
    74: Sport.HAND_CYCLING,
    75: Sport.DISC_SPORTS,
    76: Sport.FITNESS_GAMING,
    77: Sport.CARDIO_DANCE,
    78: Sport.SOCIAL_DANCE,
    79: Sport.PICKLEBALL,
    80: Sport.COOLDOWN,
    82: Sport.SWIM_BIKE_RUN,
    83: Sport.TRANSITION,
    84: Sport.UNDERWATER_DIVING,
    3000: Sport.OTHER,
}

SPORT_ACTIVITY_TYPES: dict[Sport, frozenset[int]] = {
    sport: frozenset({activity_type}) for activity_type, sport in HEALTHKIT_ACTIVITY_TYPES.items()
}
SPORT_ACTIVITY_TYPES.update(
    {
        Sport.STRENGTH: frozenset({20, 50}),
        Sport.SKIING: frozenset({40, 60, 61, 67}),
    }
)


def sport_for_activity_type(activity_type: int | None) -> Sport:
    if activity_type is None:
        return Sport.OTHER
    return HEALTHKIT_ACTIVITY_TYPES.get(activity_type, Sport.OTHER)


def source_preference(bundle_identifier: str, source_name: str) -> tuple[int, str, str]:
    bundle = bundle_identifier.casefold()
    name = source_name.casefold()
    if bundle.startswith("com.apple.") and "watch" in name:
        rank = 0
    elif bundle.startswith("com.apple."):
        rank = 1
    else:
        rank = 2
    return rank, bundle, name


def metric_catalog_resource() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "metrics": [
            {
                "name": definition.name.value,
                "healthKitSampleType": definition.sample_type,
                "unit": definition.unit,
                "domain": definition.domain,
                "validAggregations": [aggregation.value for aggregation in definition.aggregations],
                "maximumAggregateDays": 366,
                "maximumRawDays": 31,
                "maximumRawPoints": 1_000,
            }
            for definition in METRICS.values()
        ],
    }
