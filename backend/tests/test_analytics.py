from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from health_coach.analytics import HealthAnalytics, QueryValidationError, local_date_bounds
from health_coach.catalog import Aggregation, MetricName, Sport
from health_coach.config import Settings
from health_coach.db import migrate_database, write_connection
from health_coach.ingestion import IngestionService
from health_coach.models import IngestBatch, canonical_raw_json

from .conftest import make_batch, make_record


@pytest.fixture
def analytics(settings: Settings) -> HealthAnalytics:
    migrate_database(settings.db_path)
    return HealthAnalytics(settings)


def _ingest(settings: Settings, records: list[dict[str, Any]]) -> None:
    raw = make_batch(records=records)
    IngestionService(settings.db_path).ingest(IngestBatch.model_validate(raw), raw)


def _insert_category(
    settings: Settings,
    *,
    value: str,
    start: str,
    end: str,
    source_name: str = "Synthetic Watch",
    source_bundle: str = "com.apple.health.synthetic",
) -> None:
    start_value = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_value = datetime.fromisoformat(end.replace("Z", "+00:00"))
    batch_id = str(uuid4())
    device_id = "22222222-2222-2222-2222-222222222222"
    with write_connection(settings.db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO batches (
                batch_id, payload_sha256, device_id, created_at, timezone, mode,
                received_at, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                uuid4().hex + uuid4().hex,
                device_id,
                start,
                "Europe/Berlin",
                "incremental",
                start,
                "{}",
            ),
        )
        connection.execute(
            """
            INSERT INTO records (
                device_id, sample_type, sample_id, record_kind, start_at, end_at,
                start_ts, end_ts, numeric_value, category_value, activity_type,
                duration_seconds, total_energy_kcal, total_distance_m, unit,
                source_name, source_bundle_id, source_version, source_product_type,
                device_json, metadata_json, raw_record_json, source_batch_id,
                updated_at, deleted_at
            ) VALUES (?, ?, ?, 'category', ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL,
                      NULL, 'category-code', ?, ?, NULL, NULL, NULL, '{}', ?, ?, ?, NULL)
            """,
            (
                device_id,
                "HKCategoryTypeIdentifierSleepAnalysis",
                str(uuid4()),
                start,
                end,
                start_value.timestamp(),
                end_value.timestamp(),
                value,
                source_name,
                source_bundle,
                canonical_raw_json({"synthetic": True}),
                batch_id,
                start,
            ),
        )
        connection.commit()


def test_local_date_bounds_follow_berlin_dst() -> None:
    berlin = ZoneInfo("Europe/Berlin")
    spring_start, spring_end = local_date_bounds(date(2026, 3, 29), date(2026, 3, 29), berlin)
    autumn_start, autumn_end = local_date_bounds(date(2026, 10, 25), date(2026, 10, 25), berlin)

    assert spring_end.timestamp() - spring_start.timestamp() == 23 * 60 * 60
    assert autumn_end.timestamp() - autumn_start.timestamp() == 25 * 60 * 60


def test_metric_series_uses_local_dates_across_dst(
    analytics: HealthAnalytics, settings: Settings
) -> None:
    _ingest(
        settings,
        [
            make_record(
                start="2026-03-28T23:30:00.000Z",
                value={"kind": "numeric", "numericValue": 40},
            ),
            make_record(
                start="2026-03-29T22:30:00.000Z",
                value={"kind": "numeric", "numericValue": 60},
            ),
        ],
    )

    result = analytics.metric_series(
        MetricName.RESTING_HEART_RATE,
        date(2026, 3, 29),
        date(2026, 3, 29),
        Aggregation.DAILY_MEDIAN,
    )

    assert result["points"] == [{"date": "2026-03-29", "value": 40.0}]


def test_deleted_samples_are_excluded(analytics: HealthAnalytics, settings: Settings) -> None:
    sample_id = "10000000-0000-0000-0000-000000000001"
    record = make_record(sample_id=sample_id)
    _ingest(settings, [record])
    deletion = make_batch(
        deleted_records=[{"sampleType": record["sampleType"], "sampleId": sample_id}]
    )
    IngestionService(settings.db_path).ingest(IngestBatch.model_validate(deletion), deletion)

    result = analytics.metric_series(
        MetricName.RESTING_HEART_RATE,
        date(2026, 7, 16),
        date(2026, 7, 16),
        Aggregation.DAILY_MEDIAN,
    )

    assert result["points"] == []
    assert result["dataCoverage"]["records"] == 0


def test_missing_daily_data_remains_missing(analytics: HealthAnalytics) -> None:
    result = analytics.daily_summary(date(2026, 7, 16))

    assert result["recovery"]["restingHeartRate"] is None
    assert result["activity"]["stepCount"] is None
    assert "restingHeartRate" in result["missingFields"]
    assert "stepCount" in result["missingFields"]


def test_daily_median_mean_and_sum_semantics(
    analytics: HealthAnalytics, settings: Settings
) -> None:
    records: list[dict[str, Any]] = []
    for index, value in enumerate((10, 20, 100)):
        records.append(
            make_record(
                sample_type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
                sample_id=f"10000000-0000-0000-0000-{index + 1:012d}",
                start=f"2026-07-16T0{index + 1}:00:00.000Z",
                value={"kind": "numeric", "numericValue": value},
                unit="ms",
                source_bundle="com.apple.health.synthetic",
            )
        )
    for index, value in enumerate((100, 150)):
        records.append(
            make_record(
                sample_type="HKQuantityTypeIdentifierActiveEnergyBurned",
                sample_id=f"20000000-0000-0000-0000-{index + 1:012d}",
                start=f"2026-07-16T1{index}:00:00.000Z",
                value={"kind": "numeric", "numericValue": value},
                unit="kcal",
                source_bundle="com.apple.health.synthetic",
            )
        )
    _ingest(settings, records)

    median_result = analytics.metric_series(
        MetricName.HRV_SDNN,
        date(2026, 7, 16),
        date(2026, 7, 16),
        Aggregation.DAILY_MEDIAN,
    )
    mean_result = analytics.metric_series(
        MetricName.HRV_SDNN,
        date(2026, 7, 16),
        date(2026, 7, 16),
        Aggregation.DAILY_MEAN,
    )
    sum_result = analytics.metric_series(
        MetricName.ACTIVE_ENERGY,
        date(2026, 7, 16),
        date(2026, 7, 16),
        Aggregation.DAILY_SUM,
    )

    assert median_result["points"][0]["value"] == 20
    assert mean_result["points"][0]["value"] == pytest.approx(43.333333, rel=1e-5)
    assert sum_result["points"][0]["value"] == 250


def test_additive_metrics_choose_one_preferred_source_per_day(
    analytics: HealthAnalytics, settings: Settings
) -> None:
    _ingest(
        settings,
        [
            make_record(
                sample_type="HKQuantityTypeIdentifierStepCount",
                start="2026-07-16T08:00:00.000Z",
                value={"kind": "numeric", "numericValue": 1000},
                unit="count",
                source_name="Synthetic Watch",
                source_bundle="com.apple.health.synthetic",
            ),
            make_record(
                sample_type="HKQuantityTypeIdentifierStepCount",
                start="2026-07-16T08:00:00.000Z",
                value={"kind": "numeric", "numericValue": 9000},
                unit="count",
                source_name="Imported App",
                source_bundle="com.example.import",
            ),
        ],
    )

    result = analytics.metric_series(
        MetricName.STEP_COUNT,
        date(2026, 7, 16),
        date(2026, 7, 16),
        Aggregation.DAILY_SUM,
    )

    assert result["points"][0]["value"] == 1000
    assert result["sourcePolicy"] == "preferred-source-per-local-day"
    assert any("overlapping sources" in warning for warning in result["warnings"])


def test_sleep_supports_numeric_and_symbolic_stages_without_double_counting(
    analytics: HealthAnalytics, settings: Settings
) -> None:
    _insert_category(
        settings,
        value="3",
        start="2026-07-15T20:00:00Z",
        end="2026-07-15T21:00:00Z",
    )
    _insert_category(
        settings,
        value="4",
        start="2026-07-15T20:30:00Z",
        end="2026-07-15T21:30:00Z",
    )
    _insert_category(
        settings,
        value="HKCategoryValueSleepAnalysisAsleepREM",
        start="2026-07-15T21:30:00Z",
        end="2026-07-15T22:30:00Z",
    )
    _insert_category(
        settings,
        value="HKCategoryValueSleepAnalysisAwake",
        start="2026-07-15T22:15:00Z",
        end="2026-07-15T22:45:00Z",
    )
    _insert_category(
        settings,
        value="HKCategoryValueSleepAnalysisAsleepUnspecified",
        start="2026-07-15T20:00:00Z",
        end="2026-07-15T22:45:00Z",
        source_name="Imported Sleep",
        source_bundle="com.example.sleep",
    )

    result = analytics.sleep_summary(date(2026, 7, 16), date(2026, 7, 16), include_nights=True)

    night = result["nights"][0]
    assert night["coreMinutes"] == 30
    assert night["deepMinutes"] == 60
    assert night["remMinutes"] == 45
    assert night["awakeMinutes"] == 30
    assert night["sleepMinutes"] == 135
    assert result["aggregate"]["nightsWithData"] == 1
    assert result["duplicatesCollapsed"] == 1


def test_duplicate_workouts_collapse_and_measurements_are_summarized(
    analytics: HealthAnalytics, settings: Settings
) -> None:
    apple_workout = make_record(
        sample_type="HKWorkoutTypeIdentifier",
        start="2026-07-16T06:00:00.000Z",
        end="2026-07-16T07:00:00.000Z",
        value={
            "kind": "workout",
            "workout": {
                "activityType": 37,
                "durationSeconds": 3600,
                "totalEnergyKilocalories": 500,
                "totalDistanceMeters": 10000,
            },
        },
        unit="workout",
        source_name="Apple Watch",
        source_bundle="com.apple.health.synthetic",
    )
    imported_workout = make_record(
        sample_type="HKWorkoutTypeIdentifier",
        start="2026-07-16T06:01:00.000Z",
        end="2026-07-16T07:01:00.000Z",
        value={
            "kind": "workout",
            "workout": {
                "activityType": 37,
                "durationSeconds": 3600,
                "totalEnergyKilocalories": 510,
                "totalDistanceMeters": 10020,
            },
        },
        unit="workout",
        source_name="Imported Run",
        source_bundle="com.example.running",
    )
    heart_rates = [
        make_record(
            sample_type="HKQuantityTypeIdentifierHeartRate",
            start=f"2026-07-16T06:{minute:02d}:00.000Z",
            value={"kind": "numeric", "numericValue": value},
            unit="count/min",
            source_name="Apple Watch",
            source_bundle="com.apple.health.synthetic",
        )
        for minute, value in ((10, 120), (20, 140), (30, 160))
    ]
    _ingest(settings, [apple_workout, imported_workout, *heart_rates])

    result = analytics.workouts(date(2026, 7, 16), date(2026, 7, 16), sport=Sport.RUNNING, limit=10)

    assert len(result["workouts"]) == 1
    assert result["duplicatesCollapsed"] == 1
    workout = result["workouts"][0]
    assert workout["sport"] == "running"
    assert workout["heartRate"]["mean"] == 140
    assert "sampleId" not in str(result)
    assert "raw" not in str(result).lower()


def test_workouts_preserve_exact_healthkit_activity_types(
    analytics: HealthAnalytics, settings: Settings
) -> None:
    records = [
        make_record(
            sample_type="HKWorkoutTypeIdentifier",
            start=f"2026-07-16T0{index}:00:00.000Z",
            end=f"2026-07-16T0{index}:30:00.000Z",
            value={
                "kind": "workout",
                "workout": {
                    "activityType": activity_type,
                    "durationSeconds": 1800,
                },
            },
            unit="workout",
        )
        for index, activity_type in enumerate((13, 48), start=6)
    ]
    _ingest(settings, records)

    result = analytics.workouts(date(2026, 7, 16), date(2026, 7, 16))
    cycling = analytics.workouts(date(2026, 7, 16), date(2026, 7, 16), sport=Sport.CYCLING)
    tennis = analytics.workouts(date(2026, 7, 16), date(2026, 7, 16), sport=Sport.TENNIS)

    assert [(workout["activityType"], workout["sport"]) for workout in result["workouts"]] == [
        (48, "tennis"),
        (13, "cycling"),
    ]
    assert [workout["sport"] for workout in cycling["workouts"]] == ["cycling"]
    assert [workout["sport"] for workout in tennis["workouts"]] == ["tennis"]


def test_training_load_has_daily_weekly_and_sport_breakdowns(
    analytics: HealthAnalytics, settings: Settings
) -> None:
    _ingest(
        settings,
        [
            make_record(
                sample_type="HKWorkoutTypeIdentifier",
                start="2026-07-14T06:00:00.000Z",
                end="2026-07-14T07:00:00.000Z",
                value={
                    "kind": "workout",
                    "workout": {
                        "activityType": 37,
                        "durationSeconds": 3600,
                        "totalEnergyKilocalories": 500,
                        "totalDistanceMeters": 10000,
                    },
                },
                unit="workout",
                source_bundle="com.apple.health.synthetic",
            )
        ],
    )

    result = analytics.training_load(date(2026, 7, 16), days=14)

    assert result["workoutCount"] == 1
    assert result["durationMinutes"] == 60
    assert result["dailyDistribution"][0]["date"] == "2026-07-14"
    assert result["weeklyDistribution"]
    assert result["sportBreakdown"]["running"]["count"] == 1
    assert "trimp" not in str(result).lower()
    assert "tss" not in str(result).lower()


def test_recovery_baseline_excludes_recent_window(
    analytics: HealthAnalytics, settings: Settings
) -> None:
    records = []
    for day in range(1, 15):
        records.append(
            make_record(
                sample_type="HKQuantityTypeIdentifierRestingHeartRate",
                start=f"2026-06-{day:02d}T06:00:00.000Z",
                value={"kind": "numeric", "numericValue": 50},
                source_bundle="com.apple.health.synthetic",
            )
        )
    for day in range(15, 18):
        records.append(
            make_record(
                sample_type="HKQuantityTypeIdentifierRestingHeartRate",
                start=f"2026-06-{day:02d}T06:00:00.000Z",
                value={"kind": "numeric", "numericValue": 100},
                source_bundle="com.apple.health.synthetic",
            )
        )
    _ingest(settings, records)

    result = analytics.recovery_summary(date(2026, 6, 17), recent_days=3, baseline_days=14)

    metric = result["metrics"]["restingHeartRate"]
    assert metric["recent"]["median"] == 100
    assert metric["baseline"]["median"] == 50
    assert result["baselineRange"] == {
        "startDate": "2026-06-01",
        "endDate": "2026-06-14",
    }
    assert "readinessScore" not in result


def test_ranges_aggregations_and_limits_are_enforced(
    analytics: HealthAnalytics,
) -> None:
    with pytest.raises(QueryValidationError):
        analytics.metric_series(
            MetricName.HRV_SDNN,
            date(2025, 1, 1),
            date(2026, 1, 2),
            Aggregation.DAILY_MEDIAN,
        )
    with pytest.raises(QueryValidationError):
        analytics.metric_series(
            MetricName.HRV_SDNN,
            date(2026, 1, 1),
            date(2026, 2, 1),
            Aggregation.RAW,
        )
    with pytest.raises(QueryValidationError):
        analytics.metric_series(
            MetricName.HRV_SDNN,
            date(2026, 1, 1),
            date(2026, 1, 1),
            Aggregation.DAILY_SUM,
        )
    with pytest.raises(QueryValidationError):
        analytics.workouts(date(2026, 1, 1), date(2026, 1, 1), limit=201)
    with pytest.raises(QueryValidationError):
        analytics.recovery_summary(date(2026, 1, 1), recent_days=14, baseline_days=14)


def test_data_freshness_exposes_domains_without_identifiers(
    analytics: HealthAnalytics, settings: Settings
) -> None:
    _ingest(settings, [make_record()])

    result = analytics.data_freshness()

    assert result["databaseSchemaVersion"] == 2
    assert set(result["domains"]) == {"recovery", "sleep", "activity", "workouts"}
    serialized = str(result)
    assert "sampleId" not in serialized
    assert str(settings.db_path) not in serialized
