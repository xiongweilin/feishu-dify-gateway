from __future__ import annotations

import json

import httpx
import pytest

from feishu_dify_gateway.errors import GatewayError
from feishu_dify_gateway.feishu import FeishuSender
from feishu_dify_gateway.metrics import Metrics


async def test_feishu_retry_reuses_server_idempotency_uuid() -> None:
    message_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token", "expire": 7_200},
            )
        payload = json.loads(request.content)
        message_payloads.append(payload)
        if len(message_payloads) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"code": 0, "msg": "ok"})

    sender = FeishuSender(
        "https://open.feishu.invalid",
        "app-id",
        "app-secret",
        Metrics(),
        transport=httpx.MockTransport(handler),
        retry_base_seconds=0,
    )
    try:
        await sender.send_text("open-id", "hello", "stable-uuid")
    finally:
        await sender.close()
    assert [payload["uuid"] for payload in message_payloads] == [
        "stable-uuid",
        "stable-uuid",
    ]


async def test_feishu_exhausted_5xx_returns_safe_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token", "expire": 7_200},
            )
        return httpx.Response(503, text="sensitive upstream body")

    sender = FeishuSender(
        "https://open.feishu.invalid",
        "app-id",
        "app-secret",
        Metrics(),
        transport=httpx.MockTransport(handler),
        max_attempts=2,
        retry_base_seconds=0,
    )
    try:
        with pytest.raises(GatewayError) as captured:
            await sender.send_text("open-id", "hello", "stable-uuid")
    finally:
        await sender.close()
    assert captured.value.code == "FEISHU_SEND_FAILED"
    assert "sensitive upstream body" not in captured.value.safe_message


async def test_feishu_4xx_is_marked_non_retryable_without_leaking_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "token", "expire": 7_200},
            )
        return httpx.Response(400, text="sensitive upstream body")

    sender = FeishuSender(
        "https://open.feishu.invalid",
        "app-id",
        "app-secret",
        Metrics(),
        transport=httpx.MockTransport(handler),
        max_attempts=2,
        retry_base_seconds=0,
    )
    try:
        with pytest.raises(GatewayError) as captured:
            await sender.send_text("open-id", "hello", "stable-uuid")
    finally:
        await sender.close()
    assert captured.value.status_code == 400
    assert "sensitive upstream body" not in captured.value.safe_message
