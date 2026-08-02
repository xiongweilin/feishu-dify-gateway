from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from time import perf_counter

import httpx
from pydantic import ValidationError

from .errors import GatewayError
from .metrics import Metrics
from .models import FeishuMessageResponse, FeishuTokenResponse

logger = logging.getLogger(__name__)


class FeishuSender:
    def __init__(
        self,
        base_url: str,
        app_id: str,
        app_secret: str,
        metrics: Metrics,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._app_id = app_id
        self._app_secret = app_secret
        self._metrics = metrics
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0), transport=transport)
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            try:
                response = await self._client.post(
                    f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": self._app_id, "app_secret": self._app_secret},
                )
                response.raise_for_status()
                parsed = FeishuTokenResponse.model_validate(response.json())
            except (httpx.HTTPError, ValueError, ValidationError) as exc:
                raise GatewayError("FEISHU_AUTH_FAILED", "Feishu authentication failed") from exc
            if parsed.code != 0 or not parsed.tenant_access_token:
                raise GatewayError("FEISHU_AUTH_FAILED", "Feishu authentication failed")
            self._token = parsed.tenant_access_token
            self._token_expires_at = time.time() + max(60, parsed.expire - 60)
            return self._token

    async def send_text(self, recipient_open_id: str, text: str) -> None:
        started = perf_counter()
        try:
            token = await self._access_token()
            response = await self._client.post(
                f"{self._base_url}/open-apis/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": recipient_open_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
            response.raise_for_status()
            parsed = FeishuMessageResponse.model_validate(response.json())
            if parsed.code != 0:
                raise GatewayError("FEISHU_SEND_FAILED", "Feishu rejected the message")
        except GatewayError:
            self._metrics.external_requests.labels("feishu", "error").inc()
            raise
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            self._metrics.external_requests.labels("feishu", "error").inc()
            raise GatewayError("FEISHU_SEND_FAILED", "Feishu message delivery failed") from exc
        finally:
            self._metrics.external_duration.labels("feishu").observe(perf_counter() - started)
        self._metrics.external_requests.labels("feishu", "success").inc()

    async def ready(self) -> bool:
        try:
            await self._access_token()
            return True
        except GatewayError:
            return False

    async def close(self) -> None:
        await self._client.aclose()


class FeishuLongConnection:
    """Runs the official SDK long connection in a daemon thread."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        handler: Callable[[str, str, str], Awaitable[None]],
        metrics: Metrics,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._handler = handler
        self._metrics = metrics
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._thread is not None:
            return

        def run() -> None:
            try:
                import lark_oapi as lark  # type: ignore[import-untyped]
                from lark_oapi.api.im.v1 import (  # type: ignore[import-untyped]
                    P2ImMessageReceiveV1,
                )

                def on_message(data: P2ImMessageReceiveV1) -> None:
                    try:
                        event = data.event
                        if event is None or event.message is None or event.sender is None:
                            return
                        if event.message.message_type != "text":
                            return
                        sender_id = event.sender.sender_id
                        if sender_id is None or not sender_id.open_id:
                            return
                        content = json.loads(event.message.content or "{}")
                        text = content.get("text")
                        if not isinstance(text, str) or not text.strip():
                            return
                        event_id = event.message.message_id or data.header.event_id
                        if not isinstance(event_id, str) or not event_id:
                            return

                        async def dispatch() -> None:
                            await self._handler(event_id, sender_id.open_id, text.strip())

                        future = asyncio.run_coroutine_threadsafe(dispatch(), loop)

                        def completed(result: Future[None]) -> None:
                            try:
                                result.result()
                            except Exception:
                                logger.exception(
                                    "feishu event processing failed",
                                    extra={
                                        "event": "feishu_event_processing_failed",
                                        "error_code": "EVENT_PROCESSING_FAILED",
                                    },
                                )

                        future.add_done_callback(completed)
                    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                        logger.warning(
                            "feishu event rejected",
                            extra={"event": "feishu_event_rejected", "error_code": "INVALID_EVENT"},
                        )

                dispatcher = (
                    lark.EventDispatcherHandler.builder("", "")
                    .register_p2_im_message_receive_v1(on_message)
                    .build()
                )
                client = lark.ws.Client(
                    self._app_id,
                    self._app_secret,
                    event_handler=dispatcher,
                    log_level=lark.LogLevel.WARNING,
                )
                self._metrics.long_connection_up.set(1)
                self._running.set()
                client.start()
            except Exception:
                self._running.clear()
                self._metrics.long_connection_up.set(0)
                logger.exception(
                    "feishu long connection stopped",
                    extra={
                        "event": "feishu_long_connection_stopped",
                        "error_code": "WS_STOPPED",
                    },
                )

        self._thread = threading.Thread(target=run, name="feishu-long-connection", daemon=True)
        self._thread.start()
