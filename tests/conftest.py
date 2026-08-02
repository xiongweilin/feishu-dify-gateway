from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from feishu_dify_gateway.config import Settings
from feishu_dify_gateway.metrics import Metrics
from feishu_dify_gateway.models import DifyChatResponse
from feishu_dify_gateway.service import GatewayService
from feishu_dify_gateway.store import StateStore


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.idempotency_keys: list[str] = []
        self.is_ready = True
        self.failure: Exception | None = None

    async def send_text(
        self, recipient_open_id: str, text: str, idempotency_key: str
    ) -> None:
        self.idempotency_keys.append(idempotency_key)
        if self.failure is not None:
            raise self.failure
        self.messages.append((recipient_open_id, text))

    async def ready(self) -> bool:
        return self.is_ready

    async def close(self) -> None:
        return None


class FakeDify:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.is_ready = True

    async def chat(self, query: str, user: str, conversation_id: str = "") -> DifyChatResponse:
        self.calls.append((query, user, conversation_id))
        return DifyChatResponse(answer=f"answer:{query}", conversation_id="conversation-1")

    async def ready(self) -> bool:
        return self.is_ready

    async def close(self) -> None:
        return None


class FakePrometheus:
    def __init__(self) -> None:
        self.is_ready = True
        self.alerts: list[tuple[str, str]] = []

    async def ready(self) -> bool:
        return self.is_ready

    async def active_alerts(self) -> list[tuple[str, str]]:
        return list(self.alerts)

    async def close(self) -> None:
        return None


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        feishu_app_id="app-id",
        feishu_app_secret="app-secret",
        feishu_allowed_open_id="allowed-user",
        feishu_alert_recipient_open_id="alert-user",
        dify_api_key="dify-key",
        user_hmac_key="user-key",
        notification_hmac_key="notify-key",
        state_db=tmp_path / "state.db",
        ws_enabled=False,
    )


@pytest.fixture
async def service(
    settings: Settings,
) -> AsyncIterator[tuple[GatewayService, FakeSender, FakeDify, FakePrometheus]]:
    sender = FakeSender()
    dify = FakeDify()
    prometheus = FakePrometheus()
    gateway = GatewayService(
        settings,
        StateStore(settings.state_db),
        Metrics(),
        sender,
        dify,
        prometheus,
    )
    yield gateway, sender, dify, prometheus
    await gateway.close()
