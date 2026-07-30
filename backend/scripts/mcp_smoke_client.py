from __future__ import annotations

import asyncio
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    url = os.environ["HEALTH_COACH_SMOKE_MCP_URL"]
    token = os.environ["HEALTH_COACH_SMOKE_MCP_TOKEN"]
    async with (
        httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        ) as http_client,
        streamable_http_client(
            url,
            http_client=http_client,
        ) as (read_stream, write_stream, _),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool("health_get_data_freshness")
        if result.isError or result.structuredContent is None:
            raise RuntimeError("MCP freshness call failed")
        if result.structuredContent.get("databaseSchemaVersion") != 2:
            raise RuntimeError("MCP freshness returned an unexpected schema version")


if __name__ == "__main__":
    asyncio.run(main())
