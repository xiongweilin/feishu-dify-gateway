from __future__ import annotations

import httpx
import pytest

from feishu_dify_gateway.dify import DifyClient
from feishu_dify_gateway.errors import GatewayError
from feishu_dify_gateway.metrics import Metrics


async def test_dify_blocking_response_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer api-key"
        payload = request.read().decode()
        assert '"response_mode":"blocking"' in payload
        return httpx.Response(
            200,
            json={"answer": "response", "conversation_id": "conversation-1"},
        )

    client = DifyClient(
        "http://dify.invalid/v1",
        "http://dify.invalid/health",
        "api-key",
        Metrics(),
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await client.chat("query", "user-hash")
    finally:
        await client.close()
    assert response.answer == "response"
    assert response.conversation_id == "conversation-1"


async def test_dify_timeout_returns_safe_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("upstream detail", request=request)

    client = DifyClient(
        "http://dify.invalid/v1",
        "http://dify.invalid/health",
        "api-key",
        Metrics(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(GatewayError) as captured:
            await client.chat("query", "user-hash")
    finally:
        await client.close()
    assert captured.value.code == "DIFY_UNAVAILABLE"
    assert "upstream detail" not in captured.value.safe_message
