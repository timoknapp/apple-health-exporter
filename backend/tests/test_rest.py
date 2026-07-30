from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

from health_coach.app import create_app
from health_coach.config import Settings
from health_coach.db import read_connection
from health_coach.ingestion import IngestionService

from .conftest import ingest_headers, make_batch, make_record


def test_health_and_readiness_are_minimal(client: TestClient) -> None:
    health = client.get("/healthz")
    ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.content == b'{"status":"ok"}'
    assert ready.status_code == 200
    assert ready.content == b'{"status":"ready"}'
    assert health.headers["cache-control"] == "no-store"
    assert ready.headers["cache-control"] == "no-store"


def test_database_status_requires_ingest_authentication(client: TestClient) -> None:
    missing = client.get("/v1/healthkit/status")
    wrong = client.get(
        "/v1/healthkit/status",
        headers={"Authorization": "Bearer " + ("x" * 40)},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json() == {"error": "unauthorized"}
    assert missing.headers["www-authenticate"] == "Bearer"


def test_empty_database_status_has_exact_privacy_safe_shape(client: TestClient) -> None:
    response = client.get(
        "/v1/healthkit/status",
        headers=ingest_headers("unused"),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schemaVersion": 1,
        "generatedAt": response.json()["generatedAt"],
        "state": "empty",
        "latestAcceptedBatchAt": None,
        "latestRecordAt": None,
        "latestNonDeletedRecordAt": None,
        "activeRecordCount": 0,
        "domains": {
            domain: {
                "state": "empty",
                "latestRecordAt": None,
                "recordCount": 0,
            }
            for domain in ("recovery", "sleep", "activity", "workouts")
        },
        "databaseSchemaVersion": 2,
        "warnings": [
            "recovery data is absent",
            "sleep data is absent",
            "activity data is absent",
            "workouts data is absent",
        ],
    }


def test_database_status_summarizes_current_active_records(client: TestClient) -> None:
    timestamp = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    records = [
        make_record(start=timestamp),
        make_record(
            sample_type="HKCategoryTypeIdentifierSleepAnalysis",
            start=timestamp,
            value={"kind": "category", "categoryValue": 3},
            unit="category-code",
        ),
        make_record(
            sample_type="HKQuantityTypeIdentifierStepCount",
            start=timestamp,
            unit="count",
        ),
        make_record(
            sample_type="HKWorkoutTypeIdentifier",
            start=timestamp,
            value={
                "kind": "workout",
                "workout": {
                    "activityType": 48,
                    "durationSeconds": 1800,
                },
            },
            unit="workout",
        ),
    ]
    batch = make_batch(records=records)
    assert (
        client.post(
            "/v1/healthkit/ingest",
            headers=ingest_headers(batch["batchId"]),
            json=batch,
        ).status_code
        == 201
    )

    response = client.get(
        "/v1/healthkit/status",
        headers=ingest_headers("unused"),
    )
    result = response.json()

    assert response.status_code == 200
    assert result["state"] == "current"
    assert result["activeRecordCount"] == 4
    assert datetime.fromisoformat(result["latestRecordAt"].replace("Z", "+00:00")) == (
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    )
    assert result["latestAcceptedBatchAt"] is not None
    assert result["warnings"] == []
    assert {
        domain: (details["state"], details["recordCount"])
        for domain, details in result["domains"].items()
    } == {
        "recovery": ("current", 1),
        "sleep": ("current", 1),
        "activity": ("current", 1),
        "workouts": ("current", 1),
    }
    serialized = response.text
    assert batch["batchId"] not in serialized
    assert batch["deviceId"] not in serialized
    assert all(record["sampleId"] not in serialized for record in records)


def test_empty_test_batch_new_and_duplicate_have_exact_contract(client: TestClient) -> None:
    batch = make_batch(batch_id="11111111-1111-1111-1111-111111111111")
    headers = ingest_headers(batch["batchId"])

    first = client.post("/v1/healthkit/ingest", headers=headers, json=batch)
    duplicate = client.post("/v1/healthkit/ingest", headers=headers, json=batch)

    expected = b'{"batchId":"11111111-1111-1111-1111-111111111111"}'
    assert first.status_code == 201
    assert first.content == expected
    assert duplicate.status_code == 200
    assert duplicate.content == expected
    assert first.headers["cache-control"] == "no-store"


def test_numeric_category_and_workout_upserts_are_normalized(
    client: TestClient, settings: Settings
) -> None:
    records = [
        make_record(sample_id="10000000-0000-0000-0000-000000000001"),
        make_record(
            sample_type="HKCategoryTypeIdentifierSleepAnalysis",
            sample_id="10000000-0000-0000-0000-000000000002",
            value={"kind": "category", "categoryValue": 3},
            unit="category-code",
        ),
        make_record(
            sample_type="HKWorkoutTypeIdentifier",
            sample_id="10000000-0000-0000-0000-000000000003",
            end="2026-07-16T05:30:00.000Z",
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
        ),
    ]
    batch = make_batch(records=records)

    response = client.post(
        "/v1/healthkit/ingest",
        headers=ingest_headers(batch["batchId"]),
        json=batch,
    )

    assert response.status_code == 201
    with read_connection(settings.db_path) as connection:
        rows = connection.execute(
            """
            SELECT record_kind, numeric_value, category_value, activity_type
            FROM records ORDER BY sample_id
            """
        ).fetchall()
    assert [row["record_kind"] for row in rows] == ["numeric", "category", "workout"]
    assert rows[0]["numeric_value"] == 49
    assert rows[1]["category_value"] == "3"
    assert rows[2]["activity_type"] == 37


def test_batch_id_reuse_with_different_content_is_conflict(client: TestClient) -> None:
    batch = make_batch(batch_id="11111111-1111-1111-1111-111111111111")
    headers = ingest_headers(batch["batchId"])
    assert client.post("/v1/healthkit/ingest", headers=headers, json=batch).status_code == 201
    batch["timezone"] = "UTC"

    response = client.post("/v1/healthkit/ingest", headers=headers, json=batch)

    assert response.status_code == 409
    assert response.json() == {"error": "batch_id_conflict"}


def test_unknown_optional_fields_are_ignored_for_canonical_idempotency(
    client: TestClient,
) -> None:
    batch = make_batch(batch_id="11111111-1111-1111-1111-111111111111")
    batch["futureField"] = {"one": 1}
    headers = ingest_headers(batch["batchId"])
    assert client.post("/v1/healthkit/ingest", headers=headers, json=batch).status_code == 201
    batch["futureField"] = {"different": True}

    response = client.post("/v1/healthkit/ingest", headers=headers, json=batch)

    assert response.status_code == 200


def test_missing_and_mismatched_idempotency_keys_are_unprocessable(
    client: TestClient,
) -> None:
    batch = make_batch()
    missing = ingest_headers(batch["batchId"])
    del missing["Idempotency-Key"]
    mismatch = ingest_headers("00000000-0000-0000-0000-000000000000")

    assert client.post("/v1/healthkit/ingest", headers=missing, json=batch).status_code == 422
    assert client.post("/v1/healthkit/ingest", headers=mismatch, json=batch).status_code == 422


def test_malformed_json_wrong_media_type_and_auth_fail_cleanly(client: TestClient) -> None:
    batch_id = "11111111-1111-1111-1111-111111111111"
    malformed = client.post(
        "/v1/healthkit/ingest",
        headers=ingest_headers(batch_id),
        content=b'{"schemaVersion":',
    )
    wrong_type = client.post(
        "/v1/healthkit/ingest",
        headers={**ingest_headers(batch_id), "Content-Type": "text/plain"},
        content=b"{}",
    )
    absent_token = client.post(
        "/v1/healthkit/ingest",
        headers={"Content-Type": "application/json", "Idempotency-Key": batch_id},
        content=b"{}",
    )
    wrong_token = client.post(
        "/v1/healthkit/ingest",
        headers=ingest_headers(batch_id, token="x" * 40),
        content=b"{}",
    )

    assert malformed.status_code == 400
    assert wrong_type.status_code == 415
    assert absent_token.status_code == 401
    assert wrong_token.status_code == 401
    assert absent_token.headers["www-authenticate"] == "Bearer"


class _ChunkedBody(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Any:
        yield from self._chunks


def test_streaming_body_limit_does_not_rely_on_content_length(settings: Settings) -> None:
    limited = replace(settings, max_body_bytes=64)
    with TestClient(create_app(limited)) as client:
        batch_id = "11111111-1111-1111-1111-111111111111"
        request = client.build_request(
            "POST",
            "/v1/healthkit/ingest",
            headers=ingest_headers(batch_id),
            content=_ChunkedBody([b"{" + (b" " * 40), b" " * 40 + b"}"]),
        )
        request.headers.pop("content-length", None)

        response = client.send(request)

    assert response.status_code == 413


def test_default_twenty_mebibyte_limit_is_enforced(settings: Settings) -> None:
    assert settings.max_body_bytes == 20 * 1024 * 1024
    oversized = b"{" + (b" " * settings.max_body_bytes)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/healthkit/ingest",
            headers=ingest_headers("11111111-1111-1111-1111-111111111111"),
            content=oversized,
        )
    assert response.status_code == 413


def test_schema_validation_rolls_back_entire_batch(client: TestClient, settings: Settings) -> None:
    invalid = make_record()
    del invalid["unit"]
    batch = make_batch(records=[make_record(), invalid])

    response = client.post(
        "/v1/healthkit/ingest",
        headers=ingest_headers(batch["batchId"]),
        json=batch,
    )

    assert response.status_code == 422
    with read_connection(settings.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0


def test_unsupported_schema_is_not_acknowledged(client: TestClient, settings: Settings) -> None:
    batch = make_batch(schema_version=2)

    response = client.post(
        "/v1/healthkit/ingest",
        headers=ingest_headers(batch["batchId"]),
        json=batch,
    )

    assert response.status_code == 422
    assert "batchId" not in response.text
    with read_connection(settings.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0


def _is_deleted(settings: Settings, sample_id: str) -> bool:
    with read_connection(settings.db_path) as connection:
        row = connection.execute(
            "SELECT deleted_at FROM records WHERE sample_id = ?", (sample_id,)
        ).fetchone()
    return row is not None and row["deleted_at"] is not None


def test_deletion_before_upsert_remains_deleted(client: TestClient, settings: Settings) -> None:
    sample_id = "10000000-0000-0000-0000-000000000001"
    deletion = {
        "sampleType": "HKQuantityTypeIdentifierRestingHeartRate",
        "sampleId": sample_id,
    }
    deleted_batch = make_batch(deleted_records=[deletion])
    upsert_batch = make_batch(records=[make_record(sample_id=sample_id)])

    assert (
        client.post(
            "/v1/healthkit/ingest",
            headers=ingest_headers(deleted_batch["batchId"]),
            json=deleted_batch,
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/v1/healthkit/ingest",
            headers=ingest_headers(upsert_batch["batchId"]),
            json=upsert_batch,
        ).status_code
        == 201
    )
    assert _is_deleted(settings, sample_id)


def test_deletion_after_upsert_and_repeated_deletion_are_idempotent(
    client: TestClient, settings: Settings
) -> None:
    sample_id = "10000000-0000-0000-0000-000000000001"
    upsert = make_batch(records=[make_record(sample_id=sample_id)])
    deletion = {
        "sampleType": "HKQuantityTypeIdentifierRestingHeartRate",
        "sampleId": sample_id,
    }
    first_delete = make_batch(deleted_records=[deletion])
    second_delete = make_batch(deleted_records=[deletion])
    for batch in (upsert, first_delete, second_delete):
        assert (
            client.post(
                "/v1/healthkit/ingest",
                headers=ingest_headers(batch["batchId"]),
                json=batch,
            ).status_code
            == 201
        )

    assert _is_deleted(settings, sample_id)
    with read_connection(settings.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0] == 1


def test_simultaneous_duplicate_delivery_creates_one_batch(
    client: TestClient, settings: Settings
) -> None:
    batch = make_batch(
        batch_id="11111111-1111-1111-1111-111111111111",
        records=[make_record(sample_id="10000000-0000-0000-0000-000000000001")],
    )
    headers = ingest_headers(batch["batchId"])

    def deliver() -> int:
        return client.post("/v1/healthkit/ingest", headers=headers, json=batch).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: deliver(), range(2)))

    assert statuses == [200, 201]
    with read_connection(settings.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1


def test_requests_do_not_echo_health_values_on_validation_errors(client: TestClient) -> None:
    body = {
        "schemaVersion": 1,
        "batchId": "not-a-uuid",
        "privateHealthValue": "sensitive-value",
    }
    response = client.post(
        "/v1/healthkit/ingest",
        headers=ingest_headers("not-a-uuid"),
        content=json.dumps(body),
    )
    assert response.status_code == 422
    assert "sensitive-value" not in response.text


async def test_client_disconnect_during_ingest_is_not_an_internal_error(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    received_messages: list[Message] = [
        {"type": "http.request", "body": b"{", "more_body": True},
        {"type": "http.disconnect"},
    ]
    sent_messages: list[Message] = []

    async def receive() -> Message:
        return received_messages.pop(0)

    async def send(message: Message) -> None:
        sent_messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/healthkit/ingest",
        "raw_path": b"/v1/healthkit/ingest",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {settings.ingest_token}".encode()),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    caplog.set_level(logging.INFO, logger="health_coach.operations")

    await create_app(settings)(scope, receive, send)

    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 499
    messages = [
        record.getMessage() for record in caplog.records if record.name == "health_coach.operations"
    ]
    assert '{"event":"internal_error"}' not in messages
    operational_event = next(json.loads(message) for message in messages if "request_id" in message)
    assert operational_event["route"] == "/v1/healthkit/ingest"
    assert operational_event["status"] == 499


def test_unexpected_failures_and_operational_logs_are_privacy_safe(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_value = "private-health-value-123456.789"

    def fail_ingestion(*_: Any, **__: Any) -> None:
        raise RuntimeError(sensitive_value)

    monkeypatch.setattr(IngestionService, "ingest", fail_ingestion)
    caplog.set_level(logging.INFO, logger="health_coach.operations")
    batch = make_batch()
    batch["privateHealthValue"] = sensitive_value

    with TestClient(create_app(settings), raise_server_exceptions=False) as test_client:
        response = test_client.post(
            f"/v1/healthkit/ingest?private={sensitive_value}",
            headers=ingest_headers(batch["batchId"]),
            json=batch,
        )

    assert response.status_code == 500
    assert response.content == b'{"error":"internal_server_error"}'
    messages = [
        record.getMessage() for record in caplog.records if record.name == "health_coach.operations"
    ]
    combined = "\n".join(messages)
    assert sensitive_value not in combined
    assert settings.ingest_token not in combined
    assert batch["batchId"] not in combined
    operational_event = next(json.loads(message) for message in messages if "request_id" in message)
    assert set(operational_event) == {
        "duration_ms",
        "method",
        "request_id",
        "route",
        "status",
    }
    assert operational_event["route"] == "/v1/healthkit/ingest"
    assert operational_event["status"] == 500
