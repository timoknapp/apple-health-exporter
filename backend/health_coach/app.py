from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .analytics import HealthAnalytics
from .config import Settings
from .db import is_ready, migrate_database
from .ingestion import BatchConflictError, IngestionService
from .mcp_server import build_mcp_server
from .models import IngestBatch

LOGGER = logging.getLogger("health_coach.operations")


class PayloadTooLargeError(ValueError):
    pass


class OperationalMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        request_id = uuid4().hex
        status = 500

        async def send_with_headers(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            path = str(scope.get("path", ""))
            route = _normalized_route(path)
            event = {
                "request_id": request_id,
                "method": scope.get("method", ""),
                "route": route,
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1_000, 3),
            }
            LOGGER.info(json.dumps(event, separators=(",", ":"), sort_keys=True))


class RejectBrowserOriginMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and str(scope.get("path", "")).startswith("/mcp"):
            headers = _header_map(scope)
            if "origin" in headers:
                await _send_json(send, 403, {"error": "browser_origin_not_allowed"})
                return
        await self.app(scope, receive, send)


class BearerAuthApp:
    def __init__(
        self,
        app: ASGIApp,
        expected_token: str,
        *,
        protected_prefix: str,
    ) -> None:
        self.app = app
        self.expected_token = expected_token
        self.protected_prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if path != self.protected_prefix and not path.startswith(f"{self.protected_prefix}/"):
            await self.app(scope, receive, send)
            return
        authorization = _header_map(scope).get("authorization")
        if not _valid_bearer_token(authorization, self.expected_token):
            await _send_json(
                send,
                401,
                {"error": "unauthorized"},
                extra_headers=[(b"www-authenticate", b"Bearer")],
            )
            return
        await self.app(scope, receive, send)


class BoundedBodyApp:
    def __init__(self, app: ASGIApp, maximum_bytes: int) -> None:
        self.app = app
        self.maximum_bytes = maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        content_length = _header_map(scope).get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.maximum_bytes:
                    await _send_json(send, 413, {"error": "payload_too_large"})
                    return
            except ValueError:
                await _send_json(send, 400, {"error": "invalid_content_length"})
                return

        messages: list[Message] = []
        total = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            total += len(message.get("body", b""))
            if total > self.maximum_bytes:
                await _send_json(send, 413, {"error": "payload_too_large"})
                return
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            if messages:
                return messages.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    ingestion = IngestionService(resolved.db_path, busy_timeout_ms=resolved.sqlite_busy_timeout_ms)
    analytics = HealthAnalytics(resolved)
    mcp = build_mcp_server(resolved)
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        migrate_database(resolved.db_path, busy_timeout_ms=resolved.sqlite_busy_timeout_ms)
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Private Health Coach",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.mcp = mcp

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        if not is_ready(resolved.db_path):
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.get("/v1/healthkit/status")
    async def database_status(request: Request) -> JSONResponse:
        if not _valid_bearer_token(request.headers.get("authorization"), resolved.ingest_token):
            return _error_response(
                401,
                "unauthorized",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return JSONResponse(await asyncio.to_thread(analytics.data_freshness))

    @app.post("/v1/healthkit/ingest")
    async def ingest(request: Request) -> JSONResponse:
        if not _valid_bearer_token(request.headers.get("authorization"), resolved.ingest_token):
            return _error_response(
                401,
                "unauthorized",
                headers={"WWW-Authenticate": "Bearer"},
            )

        media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media_type != "application/json":
            return _error_response(415, "unsupported_media_type")

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                return _error_response(400, "invalid_content_length")
            if declared_length < 0:
                return _error_response(400, "invalid_content_length")
            if declared_length > resolved.max_body_bytes:
                return _error_response(413, "payload_too_large")

        try:
            body = await _read_bounded_body(request, resolved.max_body_bytes)
        except PayloadTooLargeError:
            return _error_response(413, "payload_too_large")
        try:
            raw_payload = json.loads(
                body,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {value}")
                ),
            )
        except UnicodeDecodeError, json.JSONDecodeError, ValueError:
            return _error_response(400, "invalid_json")
        if not isinstance(raw_payload, dict):
            return _error_response(422, "schema_validation_failed")

        schema_version = raw_payload.get("schemaVersion")
        if isinstance(schema_version, bool) or schema_version != 1:
            error = (
                "unsupported_schema_version"
                if isinstance(schema_version, int)
                else "schema_validation_failed"
            )
            return _error_response(422, error)

        idempotency_key = request.headers.get("idempotency-key")
        raw_batch_id = raw_payload.get("batchId")
        if (
            not isinstance(idempotency_key, str)
            or not isinstance(raw_batch_id, str)
            or idempotency_key != raw_batch_id
        ):
            return _error_response(422, "idempotency_key_mismatch")

        try:
            batch = IngestBatch.model_validate(raw_payload)
        except ValidationError:
            return _error_response(422, "schema_validation_failed")

        try:
            result = ingestion.ingest(batch, raw_payload)
        except BatchConflictError:
            return _error_response(409, "batch_id_conflict")
        return JSONResponse(
            {"batchId": raw_batch_id},
            status_code=201 if result.created else 200,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return _error_response(422, "schema_validation_failed")

    @app.exception_handler(ClientDisconnect)
    async def client_disconnect(_: Request, __: ClientDisconnect) -> JSONResponse:
        return _error_response(499, "client_disconnected")

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, __: Exception) -> JSONResponse:
        LOGGER.error('{"event":"internal_error"}')
        return _error_response(500, "internal_server_error")

    bounded_mcp_app = BoundedBodyApp(mcp_app, resolved.mcp_max_body_bytes)
    protected_mcp_app = BearerAuthApp(
        bounded_mcp_app,
        resolved.mcp_token,
        protected_prefix="/mcp",
    )
    app.mount("/", protected_mcp_app, name="mcp")
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(resolved.allowed_hosts),
        www_redirect=False,
    )
    app.add_middleware(RejectBrowserOriginMiddleware)
    app.add_middleware(OperationalMiddleware)
    return app


async def _read_bounded_body(request: Request, maximum_bytes: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum_bytes:
            raise PayloadTooLargeError
        body.extend(chunk)
    return bytes(body)


def _valid_bearer_token(authorization: str | None, expected_token: str) -> bool:
    if authorization is None:
        candidate = ""
    else:
        scheme, separator, candidate = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not candidate:
            candidate = ""
    return hmac.compare_digest(candidate, expected_token)


def _header_map(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _error_response(
    status: int,
    error: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status, headers=headers)


async def _send_json(
    send: Send,
    status: int,
    body: dict[str, str],
    *,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(encoded)).encode("ascii")),
        (b"cache-control", b"no-store"),
        *(extra_headers or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": encoded})


def _normalized_route(path: str) -> str:
    if path in {
        "/healthz",
        "/readyz",
        "/v1/healthkit/ingest",
        "/v1/healthkit/status",
    }:
        return path
    if path == "/mcp" or path.startswith("/mcp/"):
        return "/mcp"
    return "unmatched"
