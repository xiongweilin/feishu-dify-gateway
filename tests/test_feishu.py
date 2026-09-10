from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from feishu_dify_gateway.errors import GatewayError
from feishu_dify_gateway.feishu import (
    AdministrativeIngressClient,
    FeishuEventMetadata,
    FeishuSender,
    extract_feishu_event_metadata,
)
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


def test_long_connection_metadata_mapping_excludes_message_content() -> None:
    data = SimpleNamespace(
        header=SimpleNamespace(
            event_id="event-1",
            event_type="im.message.receive_v1",
            tenant_key="tenant-1",
            create_time="1893456000000",
            token="callback-token",
        ),
        event=SimpleNamespace(
            sender=SimpleNamespace(
                sender_id=SimpleNamespace(open_id="ou-1"),
                tenant_key="tenant-1",
            ),
            message=SimpleNamespace(
                message_id="om-1",
                root_id=None,
                parent_id=None,
                thread_id="thread-1",
                create_time=1893456000000,
                message_type="text",
                content='{"text":"must not cross the boundary"}',
            ),
        ),
    )

    metadata = extract_feishu_event_metadata(data)

    assert metadata is not None
    assert metadata.event_id == "event-1"
    assert metadata.tenant_key == "tenant-1"
    assert metadata.thread_id == "thread-1"
    payload = metadata.provider_envelope()
    assert "content" not in json.dumps(payload)
    assert payload["event"]["message"]["root_id"] == "thread-1"


async def test_administrative_ingress_client_sends_metadata_only_and_retries() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503)
        return httpx.Response(202, json={"accepted": True})

    client = AdministrativeIngressClient(
        "http://administrative.invalid",
        transport=httpx.MockTransport(handler),
        retry_base_seconds=0,
    )
    metadata = FeishuEventMetadata(
        event_id="event-1",
        event_type="im.message.receive_v1",
        tenant_key="tenant-1",
        message_id="om-1",
        root_id="om-1",
        parent_id=None,
        thread_id=None,
        sender_open_id="ou-1",
        create_time="1893456000000",
        verification_token="callback-token",
        message_type="text",
    )
    try:
        await client.send_metadata(metadata)
    finally:
        await client.close()

    assert len(requests) == 2
    assert "content" not in json.dumps(requests[0])
    assert requests[0]["header"]["event_id"] == "event-1"
    assert requests[0]["event"]["message"]["message_id"] == "om-1"


async def test_administrative_ingress_client_fails_closed_without_token() -> None:
    client = AdministrativeIngressClient(
        "http://administrative.invalid",
        transport=httpx.MockTransport(
            lambda _: pytest.fail("an unverifiable event must not be sent")
        ),
    )
    metadata = FeishuEventMetadata(
        event_id="event-1",
        event_type="im.message.receive_v1",
        tenant_key="tenant-1",
        message_id="om-1",
        root_id="om-1",
        parent_id=None,
        thread_id=None,
        sender_open_id="ou-1",
        create_time="1893456000000",
        verification_token=None,
        message_type="text",
    )
    try:
        with pytest.raises(GatewayError) as captured:
            await client.send_metadata(metadata)
    finally:
        await client.close()

    assert captured.value.code == "ADMIN_INGRESS_AUTH_UNAVAILABLE"
