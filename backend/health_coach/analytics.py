from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .catalog import (
    METRICS,
    SLEEP_SAMPLE_TYPE,
    SPORT_ACTIVITY_TYPES,
    WORKOUT_SAMPLE_TYPE,
    Aggregation,
    MetricDefinition,
    MetricName,
    Sport,
    source_preference,
    sport_for_activity_type,
)
from .config import Settings
from .db import CURRENT_SCHEMA_VERSION, read_connection


class QueryValidationError(ValueError):
    """Raised when a bounded analytics request violates a server limit."""


class HealthAnalytics:
    def __init__(self, settings: Settings) -> None:
        self._database_path = settings.db_path
        self._default_timezone = settings.timezone
        self._busy_timeout_ms = settings.sqlite_busy_timeout_ms

    def data_freshness(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        domains: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        domain_types = {
            "recovery": [
                definition.sample_type
                for definition in METRICS.values()
                if definition.domain == "recovery"
            ],
            "sleep": [SLEEP_SAMPLE_TYPE],
            "activity": [
                definition.sample_type
                for definition in METRICS.values()
                if definition.domain == "activity"
            ],
            "workouts": [WORKOUT_SAMPLE_TYPE],
        }
        projections = [
            "COUNT(*) AS active_record_count",
            "MAX(end_at) AS latest_record_at",
        ]
        parameters: list[str] = []
        for domain, sample_types in domain_types.items():
            placeholders = ",".join("?" for _ in sample_types)
            projections.extend(
                [
                    (
                        f"SUM(CASE WHEN sample_type IN ({placeholders}) "
                        f"THEN 1 ELSE 0 END) AS {domain}_count"
                    ),
                    (
                        f"MAX(CASE WHEN sample_type IN ({placeholders}) "
                        f"THEN end_at END) AS {domain}_latest"
                    ),
                ]
            )
            parameters.extend(sample_types)
            parameters.extend(sample_types)

        with read_connection(
            self._database_path, busy_timeout_ms=self._busy_timeout_ms
        ) as connection:
            batch_row = connection.execute(
                "SELECT MAX(received_at) AS latest FROM batches"
            ).fetchone()
            status_row = connection.execute(
                f"""
                SELECT {", ".join(projections)}
                FROM records
                WHERE deleted_at IS NULL
                """,
                parameters,
            ).fetchone()
            for domain in domain_types:
                latest = status_row[f"{domain}_latest"]
                state = _freshness_state(latest, now)
                domains[domain] = {
                    "state": state,
                    "latestRecordAt": latest,
                    "recordCount": int(status_row[f"{domain}_count"] or 0),
                }
                if state == "empty":
                    warnings.append(f"{domain} data is absent")
                elif state == "stale":
                    warnings.append(f"{domain} data is stale")

        latest_record = status_row["latest_record_at"]
        return {
            "schemaVersion": 1,
            "generatedAt": _utc_text(now),
            "state": _freshness_state(latest_record, now),
            "latestAcceptedBatchAt": batch_row["latest"],
            "latestRecordAt": latest_record,
            "latestNonDeletedRecordAt": latest_record,
            "activeRecordCount": status_row["active_record_count"],
            "domains": domains,
            "databaseSchemaVersion": CURRENT_SCHEMA_VERSION,
            "warnings": warnings,
        }

    def daily_summary(
        self,
        day: date | None = None,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        zone = self._zone(timezone)
        selected_day = day or datetime.now(zone).date()
        recovery_metrics = {
            "hrvSdnn": MetricName.HRV_SDNN,
            "restingHeartRate": MetricName.RESTING_HEART_RATE,
            "respiratoryRate": MetricName.RESPIRATORY_RATE,
            "sleepingWristTemperature": MetricName.SLEEPING_WRIST_TEMPERATURE,
            "heartRateRecoveryOneMinute": MetricName.HEART_RATE_RECOVERY_ONE_MINUTE,
            "vo2Max": MetricName.VO2_MAX,
            "oxygenSaturation": MetricName.OXYGEN_SATURATION,
            "bodyMass": MetricName.BODY_MASS,
            "bodyFatPercentage": MetricName.BODY_FAT_PERCENTAGE,
            "leanBodyMass": MetricName.LEAN_BODY_MASS,
        }
        activity_metrics = {
            "activeEnergy": MetricName.ACTIVE_ENERGY,
            "exerciseTime": MetricName.EXERCISE_TIME,
            "standTime": MetricName.STAND_TIME,
            "stepCount": MetricName.STEP_COUNT,
            "physicalEffort": MetricName.PHYSICAL_EFFORT,
        }
        missing: list[str] = []
        warnings: list[str] = []

        def metric_value(name: str, metric: MetricName) -> dict[str, Any] | None:
            definition = METRICS[metric]
            aggregation = Aggregation.DAILY_SUM if definition.additive else Aggregation.DAILY_MEDIAN
            result = self.metric_series(
                metric,
                selected_day,
                selected_day,
                aggregation,
                timezone=zone.key,
            )
            warnings.extend(result["warnings"])
            if not result["points"]:
                missing.append(name)
                return None
            return {"value": result["points"][0]["value"], "unit": definition.unit}

        recovery = {name: metric_value(name, metric) for name, metric in recovery_metrics.items()}
        activity = {name: metric_value(name, metric) for name, metric in activity_metrics.items()}
        sleep = self.sleep_summary(
            selected_day, selected_day, include_nights=True, timezone=zone.key
        )
        workout_result = self.workouts(selected_day, selected_day, limit=200, timezone=zone.key)
        if sleep["aggregate"]["nightsWithData"] == 0:
            missing.append("sleep")
        if not workout_result["workouts"]:
            missing.append("workouts")
        warnings.extend(sleep["warnings"])
        warnings.extend(workout_result["warnings"])

        return {
            "schemaVersion": 1,
            "generatedAt": _utc_text(datetime.now(UTC)),
            "timezone": zone.key,
            "date": selected_day.isoformat(),
            "recovery": recovery,
            "sleep": sleep["aggregate"],
            "activity": activity,
            "workouts": {
                "count": len(workout_result["workouts"]),
                "durationMinutes": _clean_number(
                    sum(item["durationMinutes"] for item in workout_result["workouts"])
                ),
                "items": workout_result["workouts"],
            },
            "missingFields": sorted(missing),
            "dataFreshness": self._latest_batch_receipt(),
            "sourcePolicyNotes": [
                "One deterministic preferred source is selected per local day.",
                "Deleted records are excluded; missing values are not converted to zero.",
            ],
            "warnings": sorted(set(warnings)),
        }

    def metric_series(
        self,
        metric: MetricName | str,
        start_date: date,
        end_date: date | None,
        aggregation: Aggregation | str,
        *,
        limit: int | None = None,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        try:
            metric_name = MetricName(metric)
            aggregation_name = Aggregation(aggregation)
        except ValueError as error:
            raise QueryValidationError("metric and aggregation must be allowlisted") from error
        definition = METRICS[metric_name]
        if aggregation_name not in definition.aggregations:
            raise QueryValidationError(
                f"{aggregation_name.value} is not valid for {metric_name.value}"
            )
        zone = self._zone(timezone)
        selected_end = end_date or datetime.now(zone).date()
        days = _validate_range(start_date, selected_end, maximum_days=366)
        if aggregation_name is Aggregation.RAW and days > 31:
            raise QueryValidationError("raw metric ranges cannot exceed 31 days")
        if limit is not None and not 1 <= limit <= 1_000:
            raise QueryValidationError("limit must be between 1 and 1000")

        start, end = local_date_bounds(start_date, selected_end, zone)
        with read_connection(
            self._database_path, busy_timeout_ms=self._busy_timeout_ms
        ) as connection:
            rows = connection.execute(
                """
                SELECT start_at, start_ts, numeric_value, source_name, source_bundle_id
                FROM records
                WHERE sample_type = ?
                  AND record_kind = 'numeric'
                  AND deleted_at IS NULL
                  AND start_ts >= ?
                  AND start_ts < ?
                ORDER BY start_ts
                """,
                (definition.sample_type, start.timestamp(), end.timestamp()),
            ).fetchall()

        selected, overlap_days = _select_preferred_sources(rows, zone)
        warnings: list[str] = []
        if overlap_days:
            warnings.append(
                "Potentially overlapping sources were reduced to one preferred source "
                f"on {overlap_days} local day(s)."
            )
        explicit_limit = limit is not None
        maximum_points = limit or 1_000
        points: list[dict[str, Any]]
        if aggregation_name is Aggregation.RAW:
            raw_points = [
                {
                    "timestamp": datetime.fromtimestamp(row["start_ts"], UTC)
                    .astimezone(zone)
                    .isoformat(),
                    "value": _clean_number(row["numeric_value"]),
                }
                for row in selected
            ]
            if len(raw_points) > maximum_points and not explicit_limit:
                raise QueryValidationError(
                    "raw result exceeds 1000 points; provide an explicit smaller limit"
                )
            points = raw_points[:maximum_points]
            if len(raw_points) > len(points):
                warnings.append(f"Result was explicitly limited to {maximum_points} points.")
        else:
            grouped: dict[date, list[Any]] = defaultdict(list)
            for row in selected:
                local_day = datetime.fromtimestamp(row["start_ts"], UTC).astimezone(zone).date()
                grouped[local_day].append(row)
            points = []
            for local_day in sorted(grouped):
                values = [float(row["numeric_value"]) for row in grouped[local_day]]
                if aggregation_name is Aggregation.DAILY_MEDIAN:
                    value = statistics.median(values)
                elif aggregation_name is Aggregation.DAILY_MEAN:
                    value = statistics.fmean(values)
                elif aggregation_name is Aggregation.DAILY_SUM:
                    value = math.fsum(values)
                elif aggregation_name is Aggregation.LATEST:
                    value = float(grouped[local_day][-1]["numeric_value"])
                else:
                    raise AssertionError("unsupported validated aggregation")
                points.append({"date": local_day.isoformat(), "value": _clean_number(value)})
            if explicit_limit and len(points) > maximum_points:
                points = points[:maximum_points]
                warnings.append(f"Result was explicitly limited to {maximum_points} points.")

        days_with_data = len(
            {
                datetime.fromtimestamp(row["start_ts"], UTC).astimezone(zone).date()
                for row in selected
            }
        )
        return {
            "schemaVersion": 1,
            "generatedAt": _utc_text(datetime.now(UTC)),
            "metric": metric_name.value,
            "unit": definition.unit,
            "aggregation": aggregation_name.value,
            "aggregationSemantics": _aggregation_semantics(aggregation_name),
            "timezone": zone.key,
            "range": {
                "startDate": start_date.isoformat(),
                "endDate": selected_end.isoformat(),
            },
            "points": points,
            "sourcePolicy": "preferred-source-per-local-day",
            "dataCoverage": {
                "records": len(selected),
                "daysWithData": days_with_data,
                "rangeDays": days,
                "missingDays": days - days_with_data,
            },
            "warnings": warnings,
        }

    def sleep_summary(
        self,
        start_date: date,
        end_date: date | None,
        *,
        include_nights: bool = True,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        zone = self._zone(timezone)
        selected_end = end_date or datetime.now(zone).date()
        days = _validate_range(start_date, selected_end, maximum_days=180)
        start, end = local_date_bounds(start_date, selected_end, zone)
        with read_connection(
            self._database_path, busy_timeout_ms=self._busy_timeout_ms
        ) as connection:
            rows = connection.execute(
                """
                SELECT start_ts, end_ts, category_value, source_name, source_bundle_id
                FROM records
                WHERE sample_type = ?
                  AND record_kind = 'category'
                  AND deleted_at IS NULL
                  AND end_ts > ?
                  AND start_ts < ?
                ORDER BY start_ts
                """,
                (
                    SLEEP_SAMPLE_TYPE,
                    start.timestamp() - (18 * 60 * 60),
                    end.timestamp() + (12 * 60 * 60),
                ),
            ).fetchall()

        by_night: dict[date, list[Any]] = defaultdict(list)
        warnings: list[str] = []
        for row in rows:
            local_start = datetime.fromtimestamp(row["start_ts"], UTC).astimezone(zone)
            night = (
                local_start.date() + timedelta(days=1)
                if local_start.hour >= 12
                else local_start.date()
            )
            if start_date <= night <= selected_end:
                by_night[night].append(row)

        nights: list[dict[str, Any]] = []
        duplicates_collapsed = 0
        for night in sorted(by_night):
            source_groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
            for row in by_night[night]:
                source_groups[(row["source_bundle_id"], row["source_name"])].append(row)
            ranked_sources = sorted(
                source_groups.items(),
                key=lambda item: (
                    -_union_duration(item[1]),
                    source_preference(item[0][0], item[0][1]),
                ),
            )
            selected_rows = ranked_sources[0][1]
            duplicates_collapsed += max(0, len(ranked_sources) - 1)
            totals, unknown_values = _merged_sleep_totals(selected_rows)
            if unknown_values:
                warnings.append(
                    f"Night {night.isoformat()} contained unsupported sleep stage values."
                )
            sleep_minutes = math.fsum(
                totals[stage] for stage in ("core", "deep", "rem", "unspecified")
            )
            awake_minutes = totals["awake"]
            denominator = sleep_minutes + awake_minutes
            nights.append(
                {
                    "date": night.isoformat(),
                    "sleepMinutes": _clean_number(sleep_minutes),
                    "awakeMinutes": _clean_number(awake_minutes),
                    "coreMinutes": _clean_number(totals["core"]),
                    "deepMinutes": _clean_number(totals["deep"]),
                    "remMinutes": _clean_number(totals["rem"]),
                    "unspecifiedMinutes": _clean_number(totals["unspecified"]),
                    "inBedMinutes": _clean_number(totals["in_bed"]),
                    "efficiencyPercent": (
                        _clean_number((sleep_minutes / denominator) * 100) if denominator else None
                    ),
                }
            )

        aggregate = _sleep_aggregate(nights, expected_nights=days)
        if not nights:
            warnings.append("No sleep data is available in the requested range.")
        return {
            "schemaVersion": 1,
            "generatedAt": _utc_text(datetime.now(UTC)),
            "timezone": zone.key,
            "range": {
                "startDate": start_date.isoformat(),
                "endDate": selected_end.isoformat(),
            },
            "nights": nights if include_nights else [],
            "aggregate": aggregate,
            "duplicatesCollapsed": duplicates_collapsed,
            "sourcePolicy": "longest-coverage-then-preferred-source-per-night",
            "dataCoverage": {
                "nightsWithData": len(nights),
                "expectedNights": days,
                "missingNights": days - len(nights),
            },
            "warnings": warnings,
        }

    def workouts(
        self,
        start_date: date,
        end_date: date | None,
        *,
        sport: Sport | str | None = None,
        limit: int = 50,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        zone = self._zone(timezone)
        selected_end = end_date or datetime.now(zone).date()
        _validate_range(start_date, selected_end, maximum_days=366)
        if not 1 <= limit <= 200:
            raise QueryValidationError("workout limit must be between 1 and 200")
        sport_filter: Sport | None
        try:
            sport_filter = Sport(sport) if sport is not None else None
        except ValueError as error:
            raise QueryValidationError("sport must be allowlisted") from error
        rows = self._workout_rows(start_date, selected_end, zone, sport_filter)
        deduplicated, duplicates_collapsed = _deduplicate_workouts(rows)
        limited = deduplicated[:limit]
        warnings: list[str] = []
        if len(deduplicated) > limit:
            warnings.append(f"Result was explicitly limited to {limit} workouts.")

        results = [self._workout_response(row, zone) for row in limited]
        return {
            "schemaVersion": 1,
            "generatedAt": _utc_text(datetime.now(UTC)),
            "timezone": zone.key,
            "range": {
                "startDate": start_date.isoformat(),
                "endDate": selected_end.isoformat(),
            },
            "sport": sport_filter.value if sport_filter else None,
            "workouts": results,
            "duplicatesCollapsed": duplicates_collapsed,
            "dataCoverage": {
                "workoutsReturned": len(results),
                "workoutsAfterDeduplication": len(deduplicated),
            },
            "warnings": warnings,
        }

    def training_load(
        self,
        end_date: date | None = None,
        *,
        days: int = 28,
        sport: Sport | str | None = None,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        if not 7 <= days <= 365:
            raise QueryValidationError("days must be between 7 and 365")
        zone = self._zone(timezone)
        selected_end = end_date or datetime.now(zone).date()
        selected_start = selected_end - timedelta(days=days - 1)
        try:
            sport_filter = Sport(sport) if sport is not None else None
        except ValueError as error:
            raise QueryValidationError("sport must be allowlisted") from error
        rows, duplicates_collapsed = _deduplicate_workouts(
            self._workout_rows(selected_start, selected_end, zone, sport_filter)
        )
        daily: dict[date, list[Any]] = defaultdict(list)
        weekly: dict[date, list[Any]] = defaultdict(list)
        sports: dict[Sport, list[Any]] = defaultdict(list)
        for row in rows:
            local_day = datetime.fromtimestamp(row["start_ts"], UTC).astimezone(zone).date()
            daily[local_day].append(row)
            week_start = local_day - timedelta(days=local_day.weekday())
            weekly[week_start].append(row)
            sports[sport_for_activity_type(row["activity_type"])].append(row)

        duration_seconds = math.fsum(float(row["duration_seconds"] or 0) for row in rows)
        energy_values = [
            float(row["total_energy_kcal"]) for row in rows if row["total_energy_kcal"] is not None
        ]
        recent_days = days // 2
        recent_start = selected_end - timedelta(days=recent_days - 1)
        recent_rows = [
            row
            for row in rows
            if datetime.fromtimestamp(row["start_ts"], UTC).astimezone(zone).date() >= recent_start
        ]
        prior_rows = [row for row in rows if row not in recent_rows]
        recent_duration = math.fsum(float(row["duration_seconds"] or 0) for row in recent_rows)
        prior_duration = math.fsum(float(row["duration_seconds"] or 0) for row in prior_rows)
        warnings: list[str] = []
        if len(energy_values) != len(rows):
            warnings.append("Energy is missing for one or more workouts.")
        if any(row["total_distance_m"] is None for row in rows):
            warnings.append("Distance is missing or not comparable for one or more workouts.")

        return {
            "schemaVersion": 1,
            "generatedAt": _utc_text(datetime.now(UTC)),
            "timezone": zone.key,
            "range": {
                "startDate": selected_start.isoformat(),
                "endDate": selected_end.isoformat(),
                "days": days,
            },
            "sport": sport_filter.value if sport_filter else None,
            "workoutCount": len(rows),
            "durationMinutes": _clean_number(duration_seconds / 60),
            "energyKilocalories": (
                _clean_number(math.fsum(energy_values)) if energy_values else None
            ),
            "dailyDistribution": [
                _distribution_entry(local_day, values)
                for local_day, values in sorted(daily.items())
            ],
            "weeklyDistribution": [
                _distribution_entry(week_start, values)
                for week_start, values in sorted(weekly.items())
            ],
            "sportBreakdown": {
                selected_sport.value: _sport_breakdown(values)
                for selected_sport, values in sorted(sports.items(), key=lambda item: item[0].value)
            },
            "recentVersusPrior": {
                "recentDays": recent_days,
                "recentWorkoutCount": len(recent_rows),
                "priorWorkoutCount": len(prior_rows),
                "recentDurationMinutes": _clean_number(recent_duration / 60),
                "priorDurationMinutes": _clean_number(prior_duration / 60),
                "durationChangePercent": (
                    _clean_number(((recent_duration - prior_duration) / prior_duration) * 100)
                    if prior_duration
                    else None
                ),
            },
            "duplicatesCollapsed": duplicates_collapsed,
            "dataCoverage": {
                "daysWithWorkouts": len(daily),
                "windowDays": days,
                "workoutsWithEnergy": len(energy_values),
            },
            "warnings": warnings,
        }

    def recovery_summary(
        self,
        end_date: date | None = None,
        *,
        recent_days: int = 7,
        baseline_days: int = 28,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= recent_days <= 14:
            raise QueryValidationError("recent_days must be between 1 and 14")
        if not 14 <= baseline_days <= 90:
            raise QueryValidationError("baseline_days must be between 14 and 90")
        if baseline_days <= recent_days:
            raise QueryValidationError("baseline_days must be longer than recent_days")
        zone = self._zone(timezone)
        selected_end = end_date or datetime.now(zone).date()
        recent_start = selected_end - timedelta(days=recent_days - 1)
        baseline_end = recent_start - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=baseline_days - 1)
        metric_names = {
            "hrvSdnn": MetricName.HRV_SDNN,
            "restingHeartRate": MetricName.RESTING_HEART_RATE,
            "respiratoryRate": MetricName.RESPIRATORY_RATE,
            "sleepingWristTemperature": MetricName.SLEEPING_WRIST_TEMPERATURE,
            "heartRateRecoveryOneMinute": MetricName.HEART_RATE_RECOVERY_ONE_MINUTE,
            "vo2Max": MetricName.VO2_MAX,
        }
        metrics: dict[str, Any] = {}
        for response_name, metric in metric_names.items():
            series = self.metric_series(
                metric,
                baseline_start,
                selected_end,
                Aggregation.DAILY_MEDIAN,
                timezone=zone.key,
            )
            baseline_values = [
                point["value"]
                for point in series["points"]
                if baseline_start <= date.fromisoformat(point["date"]) <= baseline_end
            ]
            recent_points = [
                point
                for point in series["points"]
                if recent_start <= date.fromisoformat(point["date"]) <= selected_end
            ]
            recent_values = [point["value"] for point in recent_points]
            metrics[response_name] = _recovery_metric(
                METRICS[metric],
                baseline_values,
                recent_values,
                recent_points,
                baseline_days=baseline_days,
                recent_days=recent_days,
            )

        sleep = self.sleep_summary(
            baseline_start, selected_end, include_nights=True, timezone=zone.key
        )
        baseline_sleep = [
            night["sleepMinutes"]
            for night in sleep["nights"]
            if baseline_start <= date.fromisoformat(night["date"]) <= baseline_end
        ]
        recent_sleep_points = [
            {"date": night["date"], "value": night["sleepMinutes"]}
            for night in sleep["nights"]
            if recent_start <= date.fromisoformat(night["date"]) <= selected_end
        ]
        metrics["sleepDuration"] = _recovery_metric(
            MetricDefinition(
                MetricName.HRV_SDNN,
                SLEEP_SAMPLE_TYPE,
                "min",
                "recovery",
                (Aggregation.DAILY_MEDIAN,),
            ),
            baseline_sleep,
            [point["value"] for point in recent_sleep_points],
            recent_sleep_points,
            baseline_days=baseline_days,
            recent_days=recent_days,
        )
        return {
            "schemaVersion": 1,
            "generatedAt": _utc_text(datetime.now(UTC)),
            "timezone": zone.key,
            "recentRange": {
                "startDate": recent_start.isoformat(),
                "endDate": selected_end.isoformat(),
            },
            "baselineRange": {
                "startDate": baseline_start.isoformat(),
                "endDate": baseline_end.isoformat(),
            },
            "metrics": metrics,
            "statistics": (
                "daily medians compared with baseline median and median absolute deviation"
            ),
            "dataCoverage": {
                "recentDays": recent_days,
                "baselineDays": baseline_days,
            },
            "warnings": [
                "Flags are fitness-coaching context, not a diagnosis or medical advice.",
                "Missing observations remain missing and no readiness score is calculated.",
            ],
        }

    def _zone(self, timezone: str | None) -> ZoneInfo:
        value = timezone or self._default_timezone
        try:
            return ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise QueryValidationError("timezone must be an IANA timezone") from error

    def _latest_batch_receipt(self) -> str | None:
        with read_connection(
            self._database_path, busy_timeout_ms=self._busy_timeout_ms
        ) as connection:
            row = connection.execute("SELECT MAX(received_at) AS latest FROM batches").fetchone()
        latest = row["latest"]
        return str(latest) if latest is not None else None

    def _workout_rows(
        self,
        start_date: date,
        end_date: date,
        zone: ZoneInfo,
        sport: Sport | None,
    ) -> list[Any]:
        start, end = local_date_bounds(start_date, end_date, zone)
        with read_connection(
            self._database_path, busy_timeout_ms=self._busy_timeout_ms
        ) as connection:
            rows = connection.execute(
                """
                SELECT sample_id, start_at, end_at, start_ts, end_ts, activity_type,
                       duration_seconds, total_energy_kcal, total_distance_m,
                       source_name, source_bundle_id
                FROM records
                WHERE sample_type = ?
                  AND record_kind = 'workout'
                  AND deleted_at IS NULL
                  AND start_ts >= ?
                  AND start_ts < ?
                ORDER BY start_ts, sample_id
                """,
                (WORKOUT_SAMPLE_TYPE, start.timestamp(), end.timestamp()),
            ).fetchall()
        if sport is None:
            return list(rows)
        if sport is Sport.OTHER:
            return [
                row for row in rows if sport_for_activity_type(row["activity_type"]) is Sport.OTHER
            ]
        activity_types = SPORT_ACTIVITY_TYPES[sport]
        return [row for row in rows if row["activity_type"] in activity_types]

    def _workout_response(self, row: Any, zone: ZoneInfo) -> dict[str, Any]:
        start = datetime.fromtimestamp(row["start_ts"], UTC)
        end = datetime.fromtimestamp(row["end_ts"], UTC)
        measurements = {
            "heartRate": self._measurement_summary(
                "HKQuantityTypeIdentifierHeartRate", row["start_ts"], row["end_ts"]
            ),
            "cyclingPower": self._measurement_summary(
                "HKQuantityTypeIdentifierCyclingPower", row["start_ts"], row["end_ts"]
            ),
            "cyclingCadence": self._measurement_summary(
                "HKQuantityTypeIdentifierCyclingCadence", row["start_ts"], row["end_ts"]
            ),
            "cyclingSpeed": self._measurement_summary(
                "HKQuantityTypeIdentifierCyclingSpeed", row["start_ts"], row["end_ts"]
            ),
        }
        return {
            "start": start.astimezone(zone).isoformat(),
            "end": end.astimezone(zone).isoformat(),
            "activityType": row["activity_type"],
            "sport": sport_for_activity_type(row["activity_type"]).value,
            "durationMinutes": _clean_number(float(row["duration_seconds"] or 0) / 60),
            "distanceMeters": row["total_distance_m"],
            "energyKilocalories": row["total_energy_kcal"],
            **measurements,
        }

    def _measurement_summary(
        self, sample_type: str, start_ts: float, end_ts: float
    ) -> dict[str, Any] | None:
        with read_connection(
            self._database_path, busy_timeout_ms=self._busy_timeout_ms
        ) as connection:
            rows = connection.execute(
                """
                SELECT numeric_value, source_name, source_bundle_id
                FROM records
                WHERE sample_type = ?
                  AND record_kind = 'numeric'
                  AND deleted_at IS NULL
                  AND start_ts >= ?
                  AND end_ts <= ?
                ORDER BY start_ts
                """,
                (sample_type, start_ts, end_ts),
            ).fetchall()
        if not rows:
            return None
        sources: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for row in rows:
            sources[(row["source_bundle_id"], row["source_name"])].append(row)
        selected = min(sources.items(), key=lambda item: source_preference(item[0][0], item[0][1]))[
            1
        ]
        values = [float(row["numeric_value"]) for row in selected]
        unit = next(
            definition.unit
            for definition in METRICS.values()
            if definition.sample_type == sample_type
        )
        return {
            "count": len(values),
            "minimum": _clean_number(min(values)),
            "maximum": _clean_number(max(values)),
            "mean": _clean_number(statistics.fmean(values)),
            "median": _clean_number(statistics.median(values)),
            "unit": unit,
        }


def local_date_bounds(
    start_date: date,
    end_date: date,
    timezone: ZoneInfo,
) -> tuple[datetime, datetime]:
    start = datetime.combine(start_date, time.min, tzinfo=timezone)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone)
    return start.astimezone(UTC), end.astimezone(UTC)


def _validate_range(start_date: date, end_date: date, *, maximum_days: int) -> int:
    if start_date > end_date:
        raise QueryValidationError("start_date must not be after end_date")
    days = (end_date - start_date).days + 1
    if days > maximum_days:
        raise QueryValidationError(f"date range cannot exceed {maximum_days} days")
    return days


def _select_preferred_sources(rows: Sequence[Any], timezone: ZoneInfo) -> tuple[list[Any], int]:
    by_day: dict[date, dict[tuple[str, str], list[Any]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        local_day = datetime.fromtimestamp(row["start_ts"], UTC).astimezone(timezone).date()
        by_day[local_day][(row["source_bundle_id"], row["source_name"])].append(row)
    selected: list[Any] = []
    overlap_days = 0
    for local_day in sorted(by_day):
        sources = by_day[local_day]
        if len(sources) > 1:
            overlap_days += 1
        chosen = min(
            sources.items(),
            key=lambda item: source_preference(item[0][0], item[0][1]),
        )[1]
        selected.extend(chosen)
    selected.sort(key=lambda row: row["start_ts"])
    return selected, overlap_days


def _parse_sleep_stage(value: str) -> str | None:
    numeric = {
        "0": "in_bed",
        "1": "unspecified",
        "2": "awake",
        "3": "core",
        "4": "deep",
        "5": "rem",
    }
    if value in numeric:
        return numeric[value]
    normalized = value.casefold()
    if "awake" in normalized:
        return "awake"
    if "asleepcore" in normalized:
        return "core"
    if "asleepdeep" in normalized:
        return "deep"
    if "asleeprem" in normalized:
        return "rem"
    if "asleep" in normalized:
        return "unspecified"
    if "inbed" in normalized:
        return "in_bed"
    return None


def _merged_sleep_totals(rows: Sequence[Any]) -> tuple[dict[str, float], set[str]]:
    intervals: list[tuple[float, float, str]] = []
    unknown: set[str] = set()
    for row in rows:
        stage = _parse_sleep_stage(str(row["category_value"]))
        if stage is None:
            unknown.add(str(row["category_value"]))
            continue
        if row["end_ts"] > row["start_ts"]:
            intervals.append((row["start_ts"], row["end_ts"], stage))
    totals = {stage: 0.0 for stage in ("awake", "core", "deep", "rem", "unspecified", "in_bed")}
    boundaries = sorted({point for interval in intervals for point in interval[:2]})
    priority = {
        "in_bed": 0,
        "unspecified": 1,
        "core": 2,
        "rem": 3,
        "deep": 4,
        "awake": 5,
    }
    for start, end in pairwise(boundaries):
        active = [
            stage
            for interval_start, interval_end, stage in intervals
            if interval_start < end and interval_end > start
        ]
        if active:
            selected = max(active, key=priority.__getitem__)
            totals[selected] += (end - start) / 60
    return totals, unknown


def _union_duration(rows: Sequence[Any]) -> float:
    intervals = sorted((float(row["start_ts"]), float(row["end_ts"])) for row in rows)
    if not intervals:
        return 0
    total = 0.0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    return total + current_end - current_start


def _sleep_aggregate(nights: Sequence[dict[str, Any]], *, expected_nights: int) -> dict[str, Any]:
    keys = (
        "sleepMinutes",
        "awakeMinutes",
        "coreMinutes",
        "deepMinutes",
        "remMinutes",
        "unspecifiedMinutes",
    )
    result: dict[str, Any] = {
        "nightsWithData": len(nights),
        "expectedNights": expected_nights,
        "missingNights": expected_nights - len(nights),
    }
    for key in keys:
        values = [float(night[key]) for night in nights]
        prefix = key.removesuffix("Minutes")
        result[f"total{prefix[0].upper() + prefix[1:]}Minutes"] = (
            _clean_number(math.fsum(values)) if values else None
        )
        result[f"average{prefix[0].upper() + prefix[1:]}Minutes"] = (
            _clean_number(statistics.fmean(values)) if values else None
        )
    efficiencies = [
        float(night["efficiencyPercent"])
        for night in nights
        if night["efficiencyPercent"] is not None
    ]
    result["averageEfficiencyPercent"] = (
        _clean_number(statistics.fmean(efficiencies)) if efficiencies else None
    )
    return result


def _deduplicate_workouts(rows: Sequence[Any]) -> tuple[list[Any], int]:
    clusters: list[list[Any]] = []
    for row in rows:
        row_sport = sport_for_activity_type(row["activity_type"])
        matching: list[Any] | None = None
        for cluster in clusters:
            representative = cluster[0]
            if sport_for_activity_type(representative["activity_type"]) is not row_sport:
                continue
            overlap = max(
                0.0,
                min(row["end_ts"], representative["end_ts"])
                - max(row["start_ts"], representative["start_ts"]),
            )
            shorter = min(
                row["end_ts"] - row["start_ts"],
                representative["end_ts"] - representative["start_ts"],
            )
            if shorter > 0 and overlap / shorter >= 0.8:
                matching = cluster
                break
        if matching is None:
            clusters.append([row])
        else:
            matching.append(row)
    selected: list[Any] = []
    for cluster in clusters:
        selected.append(
            min(
                cluster,
                key=lambda row: (
                    source_preference(row["source_bundle_id"], row["source_name"]),
                    -sum(
                        value is not None
                        for value in (
                            row["total_energy_kcal"],
                            row["total_distance_m"],
                            row["duration_seconds"],
                        )
                    ),
                    row["start_ts"],
                    row["sample_id"],
                ),
            )
        )
    selected.sort(key=lambda row: row["start_ts"], reverse=True)
    return selected, len(rows) - len(selected)


def _distribution_entry(day: date, rows: Sequence[Any]) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "count": len(rows),
        "durationMinutes": _clean_number(
            math.fsum(float(row["duration_seconds"] or 0) for row in rows) / 60
        ),
    }


def _sport_breakdown(rows: Sequence[Any]) -> dict[str, Any]:
    energy = [
        float(row["total_energy_kcal"]) for row in rows if row["total_energy_kcal"] is not None
    ]
    distance = [
        float(row["total_distance_m"]) for row in rows if row["total_distance_m"] is not None
    ]
    return {
        "count": len(rows),
        "durationMinutes": _clean_number(
            math.fsum(float(row["duration_seconds"] or 0) for row in rows) / 60
        ),
        "energyKilocalories": _clean_number(math.fsum(energy)) if energy else None,
        "distanceMeters": _clean_number(math.fsum(distance)) if distance else None,
    }


def _recovery_metric(
    definition: MetricDefinition,
    baseline_values: Sequence[float],
    recent_values: Sequence[float],
    recent_points: Sequence[dict[str, Any]],
    *,
    baseline_days: int,
    recent_days: int,
) -> dict[str, Any]:
    baseline_median = float(statistics.median(baseline_values)) if baseline_values else None
    recent_median = float(statistics.median(recent_values)) if recent_values else None
    mad = (
        float(statistics.median([abs(value - baseline_median) for value in baseline_values]))
        if baseline_values and baseline_median is not None
        else None
    )
    difference = (
        recent_median - baseline_median
        if recent_median is not None and baseline_median is not None
        else None
    )
    tolerance = max(mad or 0, abs(baseline_median) * 0.01) if baseline_median is not None else None
    if difference is None or tolerance is None:
        direction = "insufficient_data"
    elif abs(difference) <= tolerance:
        direction = "stable"
    elif difference > 0:
        direction = "higher"
    else:
        direction = "lower"
    return {
        "unit": definition.unit,
        "latest": recent_points[-1] if recent_points else None,
        "recent": {
            "median": _clean_number(recent_median) if recent_median is not None else None,
            "daysWithData": len(recent_values),
            "missingDays": recent_days - len(recent_values),
        },
        "baseline": {
            "median": (_clean_number(baseline_median) if baseline_median is not None else None),
            "medianAbsoluteDeviation": _clean_number(mad) if mad is not None else None,
            "daysWithData": len(baseline_values),
            "missingDays": baseline_days - len(baseline_values),
        },
        "percentChange": (
            _clean_number((difference / baseline_median) * 100)
            if difference is not None and baseline_median
            else None
        ),
        "direction": direction,
    }


def _aggregation_semantics(aggregation: Aggregation) -> str:
    return {
        Aggregation.DAILY_MEDIAN: "median of preferred-source samples per local day",
        Aggregation.DAILY_MEAN: "arithmetic mean of preferred-source samples per local day",
        Aggregation.DAILY_SUM: "sum of one preferred source per local day",
        Aggregation.LATEST: "latest preferred-source sample per local day",
        Aggregation.RAW: "bounded preferred-source samples in chronological order",
    }[aggregation]


def _clean_number(value: float | int) -> float:
    return round(float(value), 6)


def _parse_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
    if parsed.tzinfo is None:
        raise ValueError("stored timestamps must include an offset")
    return parsed.astimezone(UTC)


def _freshness_state(latest: str | None, now: datetime) -> str:
    if latest is None:
        return "empty"
    if now - _parse_utc(latest) > timedelta(days=3):
        return "stale"
    return "current"


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
