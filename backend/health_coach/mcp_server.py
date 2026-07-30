from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from datetime import date as Date
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from .analytics import HealthAnalytics, QueryValidationError
from .catalog import Aggregation, MetricName, Sport, metric_catalog_resource
from .config import Settings


def build_mcp_server(settings: Settings) -> FastMCP:
    server = FastMCP(
        "Private Health Coach",
        instructions="Read-only, bounded fitness-coaching data. Not medical advice.",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    analytics = HealthAnalytics(settings)

    @server.tool(structured_output=True)
    async def health_get_data_freshness() -> dict[str, Any]:
        """Return privacy-safe database and broad-domain freshness information."""
        return await _run_query(analytics.data_freshness)

    @server.tool(structured_output=True)
    async def health_get_daily_summary(
        date: Date | None = None,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """Return available recovery, sleep, activity, and workouts for one local day."""
        return await _run_query(analytics.daily_summary, date, timezone)

    @server.tool(structured_output=True)
    async def health_get_metric_series(
        metric: MetricName,
        start_date: Date,
        aggregation: Aggregation,
        end_date: Date | None = None,
        limit: Annotated[int, Field(ge=1, le=1_000)] | None = None,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """Return an allowlisted, bounded metric series with explicit units."""
        return await _run_query(
            analytics.metric_series,
            metric,
            start_date,
            end_date,
            aggregation,
            limit=limit,
            timezone=timezone,
        )

    @server.tool(structured_output=True)
    async def health_get_sleep_summary(
        start_date: Date,
        end_date: Date | None = None,
        include_nights: bool = True,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """Return merged, source-selected sleep stages for at most 180 days."""
        return await _run_query(
            analytics.sleep_summary,
            start_date,
            end_date,
            include_nights=include_nights,
            timezone=timezone,
        )

    @server.tool(structured_output=True)
    async def health_get_workouts(
        start_date: Date,
        end_date: Date | None = None,
        sport: Sport | None = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """Return deduplicated workouts and bounded measurement summaries."""
        return await _run_query(
            analytics.workouts,
            start_date,
            end_date,
            sport=sport,
            limit=limit,
            timezone=timezone,
        )

    @server.tool(structured_output=True)
    async def health_get_training_load(
        end_date: Date | None = None,
        days: Annotated[int, Field(ge=7, le=365)] = 28,
        sport: Sport | None = None,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """Return explainable workout volume distributions without invented scores."""
        return await _run_query(
            analytics.training_load,
            end_date,
            days=days,
            sport=sport,
            timezone=timezone,
        )

    @server.tool(structured_output=True)
    async def health_get_recovery_summary(
        end_date: Date | None = None,
        recent_days: Annotated[int, Field(ge=1, le=14)] = 7,
        baseline_days: Annotated[int, Field(ge=14, le=90)] = 28,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """Compare recent recovery observations with a separate prior baseline."""
        return await _run_query(
            analytics.recovery_summary,
            end_date,
            recent_days=recent_days,
            baseline_days=baseline_days,
            timezone=timezone,
        )

    @server.resource(
        "healthcoach://catalog/metrics",
        mime_type="application/json",
    )
    def metric_catalog() -> str:
        """Describe allowlisted metrics, units, aggregations, and limits."""
        return _json_resource(metric_catalog_resource())

    @server.resource(
        "healthcoach://about/data-policy",
        mime_type="application/json",
    )
    def data_policy() -> str:
        """Describe the bounded read projection and its non-medical scope."""
        return _json_resource(
            {
                "schemaVersion": 1,
                "sourceSelection": (
                    "Queries select one deterministic preferred source per local day. "
                    "Sleep selects longest coverage, then source preference."
                ),
                "deduplication": (
                    "Overlapping same-sport workouts are collapsed at 80 percent overlap. "
                    "Overlapping sleep stages are interval-merged."
                ),
                "deletions": "Rows marked deleted are excluded from every MCP result.",
                "timezone": (
                    f"Local-day calculations default to {settings.timezone} and use IANA "
                    "timezone/DST rules."
                ),
                "privacy": (
                    "Tools do not expose device IDs, sample IDs, batch IDs, raw JSON, "
                    "metadata, tokens, or database paths."
                ),
                "scope": (
                    "This service provides fitness-coaching context and is not medical "
                    "advice, diagnosis, treatment, or emergency monitoring."
                ),
                "parallelReads": True,
            }
        )

    return server


async def _run_query[ResultT](
    function: Callable[..., ResultT],
    *args: Any,
    **kwargs: Any,
) -> ResultT:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except QueryValidationError as error:
        raise ToolError(str(error)) from None
    except sqlite3.Error:
        raise ToolError("The health database query failed.") from None


def _json_resource(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
