from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

BoundedString = Annotated[StrictStr, Field(min_length=1, max_length=512)]
FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class SourceInfo(ContractModel):
    name: BoundedString
    bundle_identifier: BoundedString = Field(alias="bundleIdentifier")
    version: Annotated[StrictStr, Field(max_length=128)] | None = None
    product_type: Annotated[StrictStr, Field(max_length=128)] | None = Field(
        default=None, alias="productType"
    )


class DeviceInfo(ContractModel):
    name: Annotated[StrictStr, Field(max_length=256)] | None = None
    manufacturer: Annotated[StrictStr, Field(max_length=256)] | None = None
    model: Annotated[StrictStr, Field(max_length=256)] | None = None
    hardware_version: Annotated[StrictStr, Field(max_length=128)] | None = Field(
        default=None, alias="hardwareVersion"
    )
    firmware_version: Annotated[StrictStr, Field(max_length=128)] | None = Field(
        default=None, alias="firmwareVersion"
    )
    software_version: Annotated[StrictStr, Field(max_length=128)] | None = Field(
        default=None, alias="softwareVersion"
    )
    local_identifier: Annotated[StrictStr, Field(max_length=256)] | None = Field(
        default=None, alias="localIdentifier"
    )
    udi_device_identifier: Annotated[StrictStr, Field(max_length=256)] | None = Field(
        default=None, alias="udiDeviceIdentifier"
    )


class WorkoutValue(ContractModel):
    activity_type: Annotated[StrictInt, Field(ge=0, le=4_294_967_295)] = Field(alias="activityType")
    duration_seconds: Annotated[FiniteFloat, Field(ge=0)] = Field(alias="durationSeconds")
    total_energy_kilocalories: Annotated[FiniteFloat, Field(ge=0)] | None = Field(
        default=None, alias="totalEnergyKilocalories"
    )
    total_distance_meters: Annotated[FiniteFloat, Field(ge=0)] | None = Field(
        default=None, alias="totalDistanceMeters"
    )


class NumericRecordValue(ContractModel):
    kind: Literal["numeric"]
    numeric_value: FiniteFloat = Field(alias="numericValue")


class CategoryRecordValue(ContractModel):
    kind: Literal["category"]
    category_value: StrictInt = Field(alias="categoryValue")


class WorkoutRecordValue(ContractModel):
    kind: Literal["workout"]
    workout: WorkoutValue


RecordValue = Annotated[
    NumericRecordValue | CategoryRecordValue | WorkoutRecordValue,
    Field(discriminator="kind"),
]


class HealthRecord(ContractModel):
    operation: Literal["upsert"]
    sample_type: Annotated[StrictStr, Field(min_length=1, max_length=256)] = Field(
        alias="sampleType"
    )
    sample_id: UUID = Field(alias="sampleId")
    start: AwareDatetime
    end: AwareDatetime
    value: RecordValue
    unit: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    source: SourceInfo
    device: DeviceInfo | None = None
    metadata: dict[str, StrictStr]

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64:
            raise ValueError("metadata contains too many entries")
        if any(not key or len(key) > 256 or len(item) > 512 for key, item in value.items()):
            raise ValueError("metadata keys or values exceed the contract limit")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> HealthRecord:
        if self.end < self.start:
            raise ValueError("record end must not be before start")
        return self


class DeletedRecord(ContractModel):
    sample_type: Annotated[StrictStr, Field(min_length=1, max_length=256)] = Field(
        alias="sampleType"
    )
    sample_id: UUID = Field(alias="sampleId")


class IngestBatch(ContractModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    batch_id: UUID = Field(alias="batchId")
    device_id: UUID = Field(alias="deviceId")
    created_at: AwareDatetime = Field(alias="createdAt")
    timezone: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    mode: Literal["incremental", "backfill"]
    records: list[HealthRecord]
    deleted_records: list[DeletedRecord] = Field(alias="deletedRecords")

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be an IANA timezone") from error
        return value


def normalized_payload_json(batch: IngestBatch) -> str:
    normalized = _normalize_value(batch.model_dump(mode="python", by_alias=True))
    return json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def payload_digest(batch: IngestBatch) -> str:
    return hashlib.sha256(normalized_payload_json(batch).encode("utf-8")).hexdigest()


def canonical_raw_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return utc_text(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON numbers are not accepted")
    return value
