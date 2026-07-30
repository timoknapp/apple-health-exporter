from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from health_coach.app import create_app
from health_coach.config import Settings
from health_coach.db import read_connection

from .conftest import MCP_TOKEN, ingest_headers, make_batch, make_record

EXPECTED_TOOLS = {
    "health_get_data_freshness",
    "health_get_daily_summary",
    "health_get_metric_series",
    "health_get_sleep_summary",
    "health_get_workouts",
    "health_get_training_load",
    "health_get_recovery_summary",
}


@asynccontextmanager
async def _mcp_session(
    settings: Settings,
) -> AsyncIterator[tuple[ClientSession, httpx.AsyncClient]]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {MCP_TOKEN}"},
            ) as http_client,
            streamable_http_client(
                "http://testserver/mcp",
                http_client=http_client,
            ) as (read_stream, write_stream, _),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            yield session, http_client


def _structured(result: Any) -> dict[str, Any]:
    assert result.isError is False
    assert isinstance(result.structuredContent, dict)
    return result.structuredContent


@pytest.mark.asyncio
async def test_mcp_requires_separate_bearer_token(settings: Settings) -> None:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/mcp",
                headers={"Content-Type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            wrong = await client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer " + ("x" * 40),
                    "Content-Type": "application/json",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )

    assert response.status_code == 401
    assert wrong.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_official_client_initializes_at_exact_mcp_path(settings: Settings) -> None:
    async with _mcp_session(settings) as (session, client):
        tools = await session.list_tools()
        doubled_path = await client.post(
            "/mcp/mcp",
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
        )

    assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS
    assert doubled_path.status_code == 404


@pytest.mark.asyncio
async def test_date_bound_tool_schemas_offer_timezone_and_optional_end_dates(
    settings: Settings,
) -> None:
    async with _mcp_session(settings) as (session, _):
        tools = await session.list_tools()
        today = datetime.now(ZoneInfo("UTC")).date().isoformat()
        omitted_end = await session.call_tool(
            "health_get_metric_series",
            {
                "metric": "resting_heart_rate",
                "start_date": today,
                "aggregation": "daily_median",
                "timezone": "UTC",
            },
        )

    schemas = {tool.name: tool.inputSchema for tool in tools.tools}
    for tool_name in EXPECTED_TOOLS - {"health_get_data_freshness"}:
        assert "timezone" in schemas[tool_name]["properties"]
    for tool_name in {
        "health_get_metric_series",
        "health_get_sleep_summary",
        "health_get_workouts",
        "health_get_training_load",
        "health_get_recovery_summary",
    }:
        assert "end_date" not in schemas[tool_name].get("required", [])
    assert _structured(omitted_end)["range"]["endDate"] == today


@pytest.mark.asyncio
async def test_resources_list_and_read_with_official_client(settings: Settings) -> None:
    async with _mcp_session(settings) as (session, _):
        resources = await session.list_resources()
        metric_catalog = await session.read_resource("healthcoach://catalog/metrics")
        data_policy = await session.read_resource("healthcoach://about/data-policy")

    assert {str(resource.uri) for resource in resources.resources} == {
        "healthcoach://catalog/metrics",
        "healthcoach://about/data-policy",
    }
    assert '"hrv_sdnn"' in metric_catalog.contents[0].text
    assert "medical advice" in data_policy.contents[0].text


@pytest.mark.asyncio
async def test_every_required_tool_is_callable(settings: Settings) -> None:
    async with _mcp_session(settings) as (session, _):
        results = {
            "freshness": await session.call_tool("health_get_data_freshness"),
            "daily": await session.call_tool("health_get_daily_summary", {"date": "2026-07-16"}),
            "series": await session.call_tool(
                "health_get_metric_series",
                {
                    "metric": "resting_heart_rate",
                    "start_date": "2026-07-16",
                    "end_date": "2026-07-16",
                    "aggregation": "daily_median",
                    "timezone": "UTC",
                },
            ),
            "sleep": await session.call_tool(
                "health_get_sleep_summary",
                {
                    "start_date": "2026-07-16",
                    "end_date": "2026-07-16",
                    "include_nights": True,
                    "timezone": "UTC",
                },
            ),
            "workouts": await session.call_tool(
                "health_get_workouts",
                {
                    "start_date": "2026-07-16",
                    "end_date": "2026-07-16",
                    "limit": 20,
                    "timezone": "UTC",
                },
            ),
            "load": await session.call_tool(
                "health_get_training_load",
                {"end_date": "2026-07-16", "days": 28, "timezone": "UTC"},
            ),
            "recovery": await session.call_tool(
                "health_get_recovery_summary",
                {
                    "end_date": "2026-07-16",
                    "recent_days": 7,
                    "baseline_days": 28,
                    "timezone": "UTC",
                },
            ),
        }

    assert all(_structured(result)["schemaVersion"] == 1 for result in results.values())


@pytest.mark.asyncio
async def test_invalid_tool_arguments_fail_cleanly(settings: Settings) -> None:
    async with _mcp_session(settings) as (session, _):
        invalid_metric = await session.call_tool(
            "health_get_metric_series",
            {
                "metric": "raw_sql",
                "start_date": "2026-07-16",
                "end_date": "2026-07-16",
                "aggregation": "raw",
            },
        )
        invalid_range = await session.call_tool(
            "health_get_sleep_summary",
            {
                "start_date": "2025-01-01",
                "end_date": "2026-07-16",
            },
        )

    assert invalid_metric.isError is True
    assert invalid_range.isError is True
    assert str(settings.db_path) not in str(invalid_range.content)


@pytest.mark.asyncio
async def test_parallel_read_only_calls_are_supported(settings: Settings) -> None:
    async with _mcp_session(settings) as (session, _):
        first, second, third = await asyncio.gather(
            session.call_tool("health_get_data_freshness"),
            session.call_tool("health_get_daily_summary", {"date": "2026-07-16"}),
            session.call_tool(
                "health_get_training_load",
                {"end_date": "2026-07-16", "days": 7},
            ),
        )

    assert all(result.isError is False for result in (first, second, third))


@pytest.mark.asyncio
async def test_rest_ingest_then_mcp_read_then_deletion(settings: Settings) -> None:
    sample_id = "10000000-0000-0000-0000-000000000001"
    upsert = make_batch(
        records=[
            make_record(
                sample_id=sample_id,
                start="2026-07-16T04:30:00.000Z",
                value={"kind": "numeric", "numericValue": 49},
            )
        ]
    )
    deletion = make_batch(
        deleted_records=[
            {
                "sampleType": "HKQuantityTypeIdentifierRestingHeartRate",
                "sampleId": sample_id,
            }
        ]
    )

    async with _mcp_session(settings) as (session, client):
        accepted = await client.post(
            "/v1/healthkit/ingest",
            headers=ingest_headers(upsert["batchId"]),
            json=upsert,
        )
        before = _structured(
            await session.call_tool(
                "health_get_metric_series",
                {
                    "metric": "resting_heart_rate",
                    "start_date": "2026-07-16",
                    "end_date": "2026-07-16",
                    "aggregation": "daily_median",
                },
            )
        )
        removed = await client.post(
            "/v1/healthkit/ingest",
            headers=ingest_headers(deletion["batchId"]),
            json=deletion,
        )
        after = _structured(
            await session.call_tool(
                "health_get_metric_series",
                {
                    "metric": "resting_heart_rate",
                    "start_date": "2026-07-16",
                    "end_date": "2026-07-16",
                    "aggregation": "daily_median",
                },
            )
        )

    assert accepted.status_code == 201
    assert before["points"] == [{"date": "2026-07-16", "value": 49.0}]
    assert removed.status_code == 201
    assert after["points"] == []


@pytest.mark.asyncio
async def test_tool_results_do_not_expose_sensitive_storage_fields(
    settings: Settings,
) -> None:
    sample_id = "10000000-0000-0000-0000-000000000001"
    record = make_record(
        sample_id=sample_id,
        value={"kind": "numeric", "numericValue": 51},
    )
    batch = make_batch(records=[record])
    async with _mcp_session(settings) as (session, client):
        response = await client.post(
            "/v1/healthkit/ingest",
            headers=ingest_headers(batch["batchId"]),
            json=batch,
        )
        assert response.status_code == 201
        outputs = [
            _structured(await session.call_tool("health_get_data_freshness")),
            _structured(
                await session.call_tool("health_get_daily_summary", {"date": "2026-07-16"})
            ),
        ]

    serialized = str(outputs)
    for forbidden in (
        sample_id,
        batch["batchId"],
        batch["deviceId"],
        settings.mcp_token,
        str(settings.db_path),
        "raw_record_json",
        "metadata_json",
        "device_json",
    ):
        assert forbidden not in serialized


def test_read_connection_used_by_analytics_is_query_only(settings: Settings) -> None:
    app = create_app(settings)

    async def initialize() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(initialize())
    with read_connection(settings.db_path) as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
