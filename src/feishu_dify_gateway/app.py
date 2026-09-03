from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from .config import Settings
from .errors import GatewayError
from .feishu import FeishuLongConnection
from .metrics import Metrics
from .models import (
    AcceptedResponse,
    AlertmanagerPayload,
    ErrorDetail,
    ErrorResponse,
    Notification,
)
from .security import SignatureError, verify_request
from .service import GatewayService
from .store import StateStore

logger = logging.getLogger(__name__)


def error_response(code: str, message: str, status_code: int) -> JSONResponse:
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def create_app(
    settings: Settings,
    *,
    service: GatewayService | None = None,
    start_long_connection: bool | None = None,
) -> FastAPI:
    if service is None:
        metrics = Metrics()
        gateway = GatewayService.build(settings, StateStore(settings.state_db), metrics)
    else:
        gateway = service
        metrics = gateway.metrics
    enable_ws = settings.ws_enabled if start_long_connection is None else start_long_connection
    connection: FeishuLongConnection | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal connection
        gateway.store.prune(settings.event_retention_seconds)
        if enable_ws:
            connection = FeishuLongConnection(
                settings.feishu_app_id,
                settings.feishu_app_secret,
                gateway.handle_feishu_text,
                metrics,
            )
            connection.start(asyncio.get_running_loop())
        yield
        await gateway.close()

    app = FastAPI(
        title="Feishu-Dify Gateway",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.gateway = gateway
    app.state.metrics = metrics

    @app.middleware("http")
    async def request_observability(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", "")
        if len(request_id) > 64 or not request_id.isascii():
            request_id = ""
        request_id = request_id or uuid.uuid4().hex
        started = time.perf_counter()
        server = request.scope.get("server")
        local_port = server[1] if isinstance(server, tuple) and len(server) == 2 else None
        if request.url.path == "/v1/alerts/alertmanager" and local_port != settings.port:
            blocked_response = error_response("NOT_FOUND", "Resource not found", 404)
            blocked_response.headers["X-Request-ID"] = request_id
            return blocked_response
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "event": "http_request_failed",
                    "request_id": request_id,
                    "method": request.method,
                    "route": request.url.path,
                    "error_code": "UNHANDLED",
                },
            )
            raise
        route = getattr(request.scope.get("route"), "path", request.url.path)
        duration = time.perf_counter() - started
        status_class = f"{response.status_code // 100}xx"
        metrics.http_requests.labels(request.method, route, status_class).inc()
        metrics.http_duration.labels(request.method, route).observe(duration)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request completed",
            extra={
                "event": "http_request_completed",
                "request_id": request_id,
                "method": request.method,
                "route": route,
                "status_class": status_class,
                "duration_ms": round(duration * 1000, 2),
            },
        )
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(_: Request, exc: GatewayError) -> JSONResponse:
        return error_response(exc.code, exc.safe_message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(_: Request, __: RequestValidationError) -> JSONResponse:
        return error_response("VALIDATION_ERROR", "Request validation failed", 422)

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def ready() -> Response:
        feishu_ok = await gateway.core_readiness()
        connection_ok = not enable_ws or (connection is not None and connection.running)
        if feishu_ok and connection_ok:
            return JSONResponse({"status": "ready"})
        return error_response("NOT_READY", "One or more dependencies are unavailable", 503)

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/alerts/alertmanager", response_model=AcceptedResponse)
    async def alertmanager(payload: AlertmanagerPayload) -> AcceptedResponse:
        return await gateway.deliver_alerts(payload)

    @app.post("/v1/notifications", response_model=AcceptedResponse, status_code=202)
    async def notifications(request: Request) -> AcceptedResponse | JSONResponse:
        body = await request.body()
        event_id = request.headers.get("X-Event-ID", "")
        timestamp = request.headers.get("X-Timestamp", "")
        signature = request.headers.get("X-Signature", "")
        try:
            verify_request(
                settings.notification_hmac_key,
                timestamp,
                event_id,
                signature,
                body,
                ttl_seconds=settings.notification_ttl_seconds,
            )
        except SignatureError:
            return error_response("AUTHENTICATION_FAILED", "Request authentication failed", 401)
        try:
            notification = Notification.model_validate_json(body)
        except ValidationError:
            return error_response("VALIDATION_ERROR", "Request validation failed", 422)
        return await gateway.deliver_notification(event_id, notification)

    return app
