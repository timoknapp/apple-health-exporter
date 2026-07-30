from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from health_coach.app import create_app
from health_coach.config import Settings

INGEST_TOKEN = "ingest-" + ("a" * 40)
MCP_TOKEN = "mcp-" + ("b" * 40)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.from_mapping(
        {
            "HEALTH_COACH_DB_PATH": str(tmp_path / "coach.sqlite3"),
            "HEALTH_COACH_TIMEZONE": "Europe/Berlin",
            "HEALTH_COACH_INGEST_TOKEN": INGEST_TOKEN,
            "HEALTH_COACH_MCP_TOKEN": MCP_TOKEN,
            "HEALTH_COACH_ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
        }
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def make_record(
    *,
    sample_type: str = "HKQuantityTypeIdentifierRestingHeartRate",
    sample_id: str | None = None,
    start: str = "2026-07-16T04:30:00.000Z",
    end: str | None = None,
    value: dict[str, Any] | None = None,
    unit: str = "count/min",
    source_name: str = "Synthetic Watch",
    source_bundle: str = "com.example.synthetic-watch",
) -> dict[str, Any]:
    return {
        "operation": "upsert",
        "sampleType": sample_type,
        "sampleId": sample_id or str(uuid4()),
        "start": start,
        "end": end or start,
        "value": value or {"kind": "numeric", "numericValue": 49.0},
        "unit": unit,
        "source": {
            "name": source_name,
            "bundleIdentifier": source_bundle,
            "version": "1.0",
            "productType": "Synthetic",
        },
        "device": {
            "name": "Synthetic Device",
            "manufacturer": "Example",
            "model": "Test",
        },
        "metadata": {},
    }


def make_batch(
    *,
    batch_id: str | None = None,
    device_id: str = "22222222-2222-2222-2222-222222222222",
    records: list[dict[str, Any]] | None = None,
    deleted_records: list[dict[str, Any]] | None = None,
    schema_version: int = 1,
) -> dict[str, Any]:
    return {
        "schemaVersion": schema_version,
        "batchId": batch_id or str(uuid4()),
        "deviceId": device_id,
        "createdAt": "2026-07-16T14:45:00.000Z",
        "timezone": "Europe/Berlin",
        "mode": "incremental",
        "records": records or [],
        "deletedRecords": deleted_records or [],
    }


def ingest_headers(batch_id: str, token: str = INGEST_TOKEN) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Idempotency-Key": batch_id,
    }
