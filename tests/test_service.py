from __future__ import annotations

from datetime import UTC, datetime

import pytest

from feishu_dify_gateway.errors import GatewayError
from feishu_dify_gateway.models import AlertmanagerPayload, Notification
from feishu_dify_gateway.service import GatewayService, split_text

from .conftest import FakeControlPlane, FakeDify, FakePrometheus, FakeSender


def test_split_text_is_bounded() -> None:
    chunks = split_text("a" * 7_200)
    assert len(chunks) == 3
    assert all(0 < len(chunk) <= 3_500 for chunk in chunks)


async def test_notification_is_delivered_once(
    service: tuple[GatewayService, FakeSender, FakeDify, FakePrometheus, FakeControlPlane],
) -> None:
    gateway, sender, _, _, _ = service
    notification = Notification(
        source="test",
        severity="info",
        title="title",
        text="text",
        occurredAt=datetime.now(UTC),
    )
    first = await gateway.deliver_notification("event-1", notification)
    second = await gateway.deliver_notification("event-1", notification)
    assert first.accepted == 1
    assert second.deduplicated == 1
    assert len(sender.messages) == 1
    assert len(sender.idempotency_keys) == 1


async def test_delivery_failure_releases_claim(
    service: tuple[GatewayService, FakeSender, FakeDify, FakePrometheus, FakeControlPlane],
) -> None:
    gateway, sender, _, _, _ = service
    sender.failure = GatewayError("FEISHU_SEND_FAILED", "safe")
    notification = Notification(
        source="test",
        severity="warning",
        title="title",
        text="text",
        occurredAt=datetime.now(UTC),
    )
    with pytest.raises(GatewayError):
        await gateway.deliver_notification("event-2", notification)
    sender.failure = None
    result = await gateway.deliver_notification("event-2", notification)
    assert result.accepted == 1


async def test_retry_uses_the_same_feishu_idempotency_key(
    service: tuple[GatewayService, FakeSender, FakeDify, FakePrometheus, FakeControlPlane],
) -> None:
    gateway, sender, _, _, _ = service
    notification = Notification(
        source="test",
        severity="info",
        title="title",
        text="text",
        occurredAt=datetime.now(UTC),
    )
    sender.failure = GatewayError("FEISHU_SEND_FAILED", "safe")
    with pytest.raises(GatewayError):
        await gateway.deliver_notification("stable-event", notification)
    sender.failure = None
    await gateway.deliver_notification("stable-event", notification)
    assert len(sender.idempotency_keys) == 2
    assert sender.idempotency_keys[0] == sender.idempotency_keys[1]


async def test_alertmanager_deduplicates_per_alert(
    service: tuple[GatewayService, FakeSender, FakeDify, FakePrometheus, FakeControlPlane],
) -> None:
    gateway, sender, _, _, _ = service
    payload = AlertmanagerPayload.model_validate(
        {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "TestAlert", "severity": "warning"},
                    "annotations": {"summary": "summary"},
                    "startsAt": "2026-08-02T00:00:00Z",
                    "endsAt": None,
                    "fingerprint": "fingerprint-1",
                }
            ],
        }
    )
    assert (await gateway.deliver_alerts(payload)).accepted == 1
    assert (await gateway.deliver_alerts(payload)).deduplicated == 1
    assert len(sender.messages) == 1


async def test_chat_continues_dify_conversation(
    service: tuple[GatewayService, FakeSender, FakeDify, FakePrometheus, FakeControlPlane],
) -> None:
    gateway, sender, dify, _, _ = service
    await gateway.handle_feishu_text("msg-1", "allowed-user", "hello")
    await gateway.handle_feishu_text("msg-2", "allowed-user", "again")
    assert dify.calls[0][2] == ""
    assert dify.calls[1][2] == "conversation-1"
    assert [message for _, message in sender.messages] == ["answer:hello", "answer:again"]


async def test_unknown_sender_is_not_answered(
    service: tuple[GatewayService, FakeSender, FakeDify, FakePrometheus, FakeControlPlane],
) -> None:
    gateway, sender, dify, _, _ = service
    await gateway.handle_feishu_text("msg-3", "unknown-user", "secret input")
    assert not sender.messages
    assert not dify.calls


async def test_read_only_commands(
    service: tuple[GatewayService, FakeSender, FakeDify, FakePrometheus, FakeControlPlane],
) -> None:
    gateway, sender, _, prometheus, _ = service
    prometheus.alerts = [("ExampleAlert", "firing")]
    for index, command in enumerate(("/help", "/new", "/status", "/alerts")):
        await gateway.handle_feishu_text(f"command-{index}", "allowed-user", command)
    replies = [message for _, message in sender.messages]
    assert "可用命令" in replies[0]
    assert "新的 Dify 会话" in replies[1]
    assert "Feishu: ok" in replies[2]
    assert "ExampleAlert" in replies[3]


async def test_control_plane_commands_route_to_client(
    service: tuple[GatewayService, FakeSender, FakeDify, FakePrometheus, FakeControlPlane],
) -> None:
    gateway, _, _, _, control_plane = service
    commands = (
        "/cp status",
        "/cp approve repair-1",
        "/cp reject repair-1",
        "/cp rollback repair-1",
        "/cp pause",
        "/cp resume",
        "/cp promote cand-1",
    )
    for index, command in enumerate(commands):
        await gateway.handle_feishu_text(f"cp-{index}", "allowed-user", command)
    calls = control_plane.calls
    assert calls[0] == ("GET", "/status", None)
    assert calls[1] == (
        "POST",
        "/v1/approvals/repair-1/decision",
        {"action": "approve", "decided_by": "feishu", "note": ""},
    )
    assert calls[2][1] == "/v1/approvals/repair-1/decision"
    assert calls[3][1] == "/v1/approvals/repair-1/decision"
    assert calls[4] == ("POST", "/v1/control/pause", {"reason": "feishu"})
    assert calls[5] == ("POST", "/v1/control/resume", {"reason": "feishu"})
    assert calls[6] == (
        "POST",
        "/v1/candidates/cand-1/promote",
        {"decided_by": "feishu", "note": ""},
    )
