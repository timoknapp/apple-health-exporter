from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .db import immediate_transaction, write_connection
from .models import (
    CategoryRecordValue,
    IngestBatch,
    NumericRecordValue,
    WorkoutRecordValue,
    canonical_raw_json,
    payload_digest,
    utc_text,
)


class BatchConflictError(RuntimeError):
    """Raised when a batch ID is reused for different normalized content."""


@dataclass(frozen=True, slots=True)
class IngestionResult:
    created: bool
    batch_id: str


class IngestionService:
    def __init__(
        self,
        database_path: Path,
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self._database_path = database_path
        self._busy_timeout_ms = busy_timeout_ms

    def ingest(
        self,
        batch: IngestBatch,
        raw_payload: dict[str, Any],
        *,
        received_at: datetime | None = None,
    ) -> IngestionResult:
        normalized_batch_id = str(batch.batch_id)
        digest = payload_digest(batch)
        received = received_at or datetime.now(UTC)
        received_text = utc_text(received)
        raw_records = raw_payload.get("records")
        raw_deletions = raw_payload.get("deletedRecords")
        if not isinstance(raw_records, list) or not isinstance(raw_deletions, list):
            raise ValueError("raw payload arrays do not match the validated contract")

        with (
            write_connection(
                self._database_path, busy_timeout_ms=self._busy_timeout_ms
            ) as connection,
            immediate_transaction(connection),
        ):
            existing = connection.execute(
                "SELECT payload_sha256 FROM batches WHERE batch_id = ?",
                (normalized_batch_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] != digest:
                    raise BatchConflictError(normalized_batch_id)
                return IngestionResult(created=False, batch_id=normalized_batch_id)

            connection.execute(
                """
                    INSERT INTO batches (
                        batch_id, payload_sha256, device_id, created_at, timezone, mode,
                        received_at, raw_payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    normalized_batch_id,
                    digest,
                    str(batch.device_id),
                    utc_text(batch.created_at),
                    batch.timezone,
                    batch.mode,
                    received_text,
                    canonical_raw_json(raw_payload),
                ),
            )
            for record, raw_record in zip(batch.records, raw_records, strict=True):
                self._upsert_record(
                    connection,
                    batch=batch,
                    record=record,
                    raw_record=raw_record,
                    received_at=received_text,
                )
            for deletion, raw_deletion in zip(batch.deleted_records, raw_deletions, strict=True):
                key = (
                    str(batch.device_id),
                    deletion.sample_type,
                    str(deletion.sample_id),
                )
                connection.execute(
                    """
                        INSERT INTO tombstones (
                            device_id, sample_type, sample_id, deleted_at,
                            source_batch_id, raw_deletion_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(device_id, sample_type, sample_id) DO NOTHING
                        """,
                    (
                        *key,
                        received_text,
                        normalized_batch_id,
                        canonical_raw_json(raw_deletion),
                    ),
                )
                connection.execute(
                    """
                        UPDATE records
                        SET deleted_at = COALESCE(deleted_at, ?)
                        WHERE device_id = ? AND sample_type = ? AND sample_id = ?
                        """,
                    (received_text, *key),
                )

        return IngestionResult(created=True, batch_id=normalized_batch_id)

    def _upsert_record(
        self,
        connection: sqlite3.Connection,
        *,
        batch: IngestBatch,
        record: Any,
        raw_record: Any,
        received_at: str,
    ) -> None:
        key = (str(batch.device_id), record.sample_type, str(record.sample_id))
        tombstone = connection.execute(
            """
            SELECT deleted_at FROM tombstones
            WHERE device_id = ? AND sample_type = ? AND sample_id = ?
            """,
            key,
        ).fetchone()
        deleted_at = tombstone["deleted_at"] if tombstone is not None else None

        numeric_value: float | None = None
        category_value: str | None = None
        activity_type: int | None = None
        duration_seconds: float | None = None
        total_energy_kcal: float | None = None
        total_distance_m: float | None = None
        if isinstance(record.value, NumericRecordValue):
            record_kind = "numeric"
            numeric_value = record.value.numeric_value
        elif isinstance(record.value, CategoryRecordValue):
            record_kind = "category"
            category_value = str(record.value.category_value)
        elif isinstance(record.value, WorkoutRecordValue):
            record_kind = "workout"
            workout = record.value.workout
            activity_type = workout.activity_type
            duration_seconds = workout.duration_seconds
            total_energy_kcal = workout.total_energy_kilocalories
            total_distance_m = workout.total_distance_meters
        else:
            raise TypeError("Unsupported validated record kind.")

        source = record.source
        device_json = (
            canonical_raw_json(record.device.model_dump(mode="json", by_alias=True))
            if record.device is not None
            else None
        )
        values = (
            *key,
            record_kind,
            utc_text(record.start),
            utc_text(record.end),
            record.start.timestamp(),
            record.end.timestamp(),
            numeric_value,
            category_value,
            activity_type,
            duration_seconds,
            total_energy_kcal,
            total_distance_m,
            record.unit,
            source.name,
            source.bundle_identifier,
            source.version,
            source.product_type,
            device_json,
            canonical_raw_json(record.metadata),
            canonical_raw_json(raw_record),
            str(batch.batch_id),
            received_at,
            deleted_at,
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
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(device_id, sample_type, sample_id) DO UPDATE SET
                record_kind = excluded.record_kind,
                start_at = excluded.start_at,
                end_at = excluded.end_at,
                start_ts = excluded.start_ts,
                end_ts = excluded.end_ts,
                numeric_value = excluded.numeric_value,
                category_value = excluded.category_value,
                activity_type = excluded.activity_type,
                duration_seconds = excluded.duration_seconds,
                total_energy_kcal = excluded.total_energy_kcal,
                total_distance_m = excluded.total_distance_m,
                unit = excluded.unit,
                source_name = excluded.source_name,
                source_bundle_id = excluded.source_bundle_id,
                source_version = excluded.source_version,
                source_product_type = excluded.source_product_type,
                device_json = excluded.device_json,
                metadata_json = excluded.metadata_json,
                raw_record_json = excluded.raw_record_json,
                source_batch_id = excluded.source_batch_id,
                updated_at = excluded.updated_at,
                deleted_at = COALESCE(records.deleted_at, excluded.deleted_at)
            """,
            values,
        )
