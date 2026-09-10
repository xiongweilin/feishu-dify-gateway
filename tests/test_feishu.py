from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from feishu_dify_gateway.errors import GatewayError
from feishu_dify_gateway.feishu import (
    AdministrativeIngressClient,
    FeishuEventMetadata,
    FeishuLongConnection,
    FeishuSender,
    extract_feishu_event_metadata,
    is_administrative_route,
)
from feishu_dify_gateway.metrics import Metrics


def _install_fake_lark(
    monkeypatch: pytest.MonkeyPatch,
    event: object,
    delivered: threading.Event,
) -> list[concurrent.futures.Future[None]]:
    """Install a one-shot fake of the lark SDK long connection.

    ``delivered`` is set once the fake client hands the event to the gateway
    callback. The returned list records every dispatch coroutine the callback
    schedules, so a test can await the dispatch and can also observe that a
    discarded event schedules nothing at all.
    """
    import lark_oapi as lark

    callbacks: list[object] = []

    class FakeDispatcherBuilder:
        @classmethod
        def builder(cls, *_: str) -> FakeDispatcherBuilder:
            return cls()

        def register_p2_im_message_receive_v1(self, handler: object) -> FakeDispatcherBuilder:
            callbacks.append(handler)
            return self

        def build(self) -> object:
            return object()

    class FakeClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def start(self) -> None:
            assert callbacks
            callbacks[0](event)
            delivered.set()

    monkeypatch.setattr(lark, "EventDispatcherHandler", FakeDispatcherBuilder)
    monkeypatch.setattr(lark.ws, "Client", FakeClient)

    scheduled: list[concurrent.futures.Future[None]] = []
    real_schedule = asyncio.run_coroutine_threadsafe

    def schedule(
        coroutine: Coroutine[Any, Any, None], loop: asyncio.AbstractEventLoop
    ) -> concurrent.futures.Future[None]:
        future = real_schedule(coroutine, loop)
        scheduled.append(future)
        return future

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", schedule)
    return scheduled


def _feishu_message_event(
    *,
    event_id: str,
    message_id: str,
    message_type: str,
    content: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        header=SimpleNamespace(
            event_id=event_id,
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
                message_id=message_id,
                root_id=None,
                parent_id=None,
                thread_id=None,
                create_time=1893456000000,
                message_type=message_type,
                content=content,
            ),
        ),
    )


async def _dispatch_single_event(
    connection: FeishuLongConnection,
    scheduled: list[concurrent.futures.Future[None]],
    delivered: threading.Event,
) -> None:
    connection.start(asyncio.get_running_loop())
    assert await asyncio.to_thread(delivered.wait, 2)
    if scheduled:
        await asyncio.wait_for(asyncio.wrap_future(scheduled[0]), timeout=2)


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
        shared_secret="gateway-secret",
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


async def test_administrative_ingress_client_fails_closed_without_shared_secret() -> None:
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


async def test_administrative_ingress_client_authenticates_without_provider_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202, json={"accepted": True})

    client = AdministrativeIngressClient(
        "http://administrative.invalid",
        shared_secret="gateway-secret",
        transport=httpx.MockTransport(handler),
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
        await client.send_metadata(metadata)
    finally:
        await client.close()

    assert len(requests) == 1
    assert requests[0].headers["X-Administrative-Ingress-Token"] == "gateway-secret"
    assert "content" not in requests[0].content.decode()


async def test_long_connection_forwards_non_text_metadata_without_reading_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lark_oapi as lark

    received: list[FeishuEventMetadata] = []
    received_event = asyncio.Event()

    async def handle_text(*_: str) -> None:
        pytest.fail("non-text events must not enter control-plane text dispatch")

    async def handle_metadata(metadata: FeishuEventMetadata) -> None:
        received.append(metadata)
        received_event.set()

    callback: list[object] = []

    class FakeDispatcherBuilder:
        @classmethod
        def builder(cls, *_: str) -> FakeDispatcherBuilder:
            return cls()

        def register_p2_im_message_receive_v1(self, handler: object) -> FakeDispatcherBuilder:
            callback.append(handler)
            return self

        def build(self) -> object:
            return object()

    class FakeClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def start(self) -> None:
            assert callback
            callback[0](
                SimpleNamespace(
                    header=SimpleNamespace(
                        event_id="event-file",
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
                            message_id="om-file",
                            root_id=None,
                            parent_id=None,
                            thread_id="thread-file",
                            create_time=1893456000000,
                            message_type="file",
                            content="must-not-be-read-as-json",
                        ),
                    ),
                )
            )

    monkeypatch.setattr(lark, "EventDispatcherHandler", FakeDispatcherBuilder)
    monkeypatch.setattr(lark.ws, "Client", FakeClient)

    connection = FeishuLongConnection(
        "app-id",
        "app-secret",
        handle_text,
        Metrics(),
        metadata_handler=handle_metadata,
    )
    connection.start(asyncio.get_running_loop())
    await asyncio.wait_for(received_event.wait(), timeout=2)

    assert len(received) == 1
    assert received[0].event_id == "event-file"
    assert received[0].message_type == "file"
    assert "content" not in json.dumps(received[0].provider_envelope())


async def test_long_connection_routes_admin_text_only_to_administrative_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane_calls: list[tuple[str, str, str]] = []
    metadata_calls: list[FeishuEventMetadata] = []

    async def handle_text(event_id: str, sender_id: str, text: str) -> None:
        control_plane_calls.append((event_id, sender_id, text))

    async def handle_metadata(metadata: FeishuEventMetadata) -> None:
        metadata_calls.append(metadata)

    delivered = threading.Event()
    scheduled = _install_fake_lark(
        monkeypatch,
        _feishu_message_event(
            event_id="event-admin",
            message_id="om-admin",
            message_type="text",
            content=json.dumps({"text": "/admin onboard employee:1"}),
        ),
        delivered,
    )
    connection = FeishuLongConnection(
        "app-id",
        "app-secret",
        handle_text,
        Metrics(),
        metadata_handler=handle_metadata,
        administrative_route_prefix="/admin",
    )
    await _dispatch_single_event(connection, scheduled, delivered)

    assert len(metadata_calls) == 1
    assert metadata_calls[0].event_id == "event-admin"
    assert metadata_calls[0].message_type == "text"
    assert control_plane_calls == []


async def test_long_connection_routes_ordinary_text_only_to_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane_calls: list[tuple[str, str, str]] = []
    metadata_calls: list[FeishuEventMetadata] = []

    async def handle_text(event_id: str, sender_id: str, text: str) -> None:
        control_plane_calls.append((event_id, sender_id, text))

    async def handle_metadata(metadata: FeishuEventMetadata) -> None:
        metadata_calls.append(metadata)

    delivered = threading.Event()
    scheduled = _install_fake_lark(
        monkeypatch,
        _feishu_message_event(
            event_id="event-ordinary",
            message_id="om-ordinary",
            message_type="text",
            content=json.dumps({"text": "foo"}),
        ),
        delivered,
    )
    connection = FeishuLongConnection(
        "app-id",
        "app-secret",
        handle_text,
        Metrics(),
        metadata_handler=handle_metadata,
        administrative_route_prefix="/admin",
    )
    await _dispatch_single_event(connection, scheduled, delivered)

    # The control-plane handler receives the provider message id as its event
    # identifier, exactly as before the exclusive-routing change.
    assert control_plane_calls == [("om-ordinary", "ou-1", "foo")]
    assert metadata_calls == []


async def test_long_connection_routes_non_text_image_to_metadata_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane_calls: list[tuple[str, str, str]] = []
    metadata_calls: list[FeishuEventMetadata] = []

    async def handle_text(event_id: str, sender_id: str, text: str) -> None:
        control_plane_calls.append((event_id, sender_id, text))

    async def handle_metadata(metadata: FeishuEventMetadata) -> None:
        metadata_calls.append(metadata)

    delivered = threading.Event()
    scheduled = _install_fake_lark(
        monkeypatch,
        _feishu_message_event(
            event_id="event-image",
            message_id="om-image",
            message_type="image",
            content='{"image_key":"img-1"}',
        ),
        delivered,
    )
    connection = FeishuLongConnection(
        "app-id",
        "app-secret",
        handle_text,
        Metrics(),
        metadata_handler=handle_metadata,
        administrative_route_prefix="/admin",
    )
    await _dispatch_single_event(connection, scheduled, delivered)

    assert len(metadata_calls) == 1
    assert metadata_calls[0].event_id == "event-image"
    assert metadata_calls[0].message_type == "image"
    assert control_plane_calls == []


async def test_long_connection_drops_non_text_without_administrative_ingress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane_calls: list[tuple[str, str, str]] = []

    async def handle_text(event_id: str, sender_id: str, text: str) -> None:
        control_plane_calls.append((event_id, sender_id, text))

    delivered = threading.Event()
    scheduled = _install_fake_lark(
        monkeypatch,
        _feishu_message_event(
            event_id="event-file",
            message_id="om-file",
            message_type="file",
            content="must-not-be-read-as-json",
        ),
        delivered,
    )
    connection = FeishuLongConnection(
        "app-id",
        "app-secret",
        handle_text,
        Metrics(),
    )
    connection.start(asyncio.get_running_loop())
    assert await asyncio.to_thread(delivered.wait, 2)

    assert scheduled == []
    assert control_plane_calls == []


def test_administrative_route_prefix_is_transport_only() -> None:
    assert is_administrative_route("/admin onboard employee:1", "/admin")
    assert is_administrative_route("  /admin  ", "/admin")
    assert not is_administrative_route("/administrator onboard employee:1", "/admin")
    assert not is_administrative_route("onboard employee:1", "/admin")
