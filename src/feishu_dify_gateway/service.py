from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Awaitable
from typing import Protocol

from .config import Settings
from .dify import DifyClient
from .errors import GatewayError
from .feishu import FeishuSender
from .metrics import Metrics
from .models import AcceptedResponse, Alert, AlertmanagerPayload, DifyChatResponse, Notification
from .prometheus import PrometheusClient
from .security import pseudonymous_user
from .store import StateStore

logger = logging.getLogger(__name__)


class Sender(Protocol):
    async def send_text(self, recipient_open_id: str, text: str) -> None: ...

    async def ready(self) -> bool: ...

    async def close(self) -> None: ...


class ChatClient(Protocol):
    async def chat(self, query: str, user: str, conversation_id: str = "") -> DifyChatResponse: ...

    async def ready(self) -> bool: ...

    async def close(self) -> None: ...


class MonitorClient(Protocol):
    async def ready(self) -> bool: ...

    async def active_alerts(self) -> list[tuple[str, str]]: ...

    async def close(self) -> None: ...


def split_text(text: str, limit: int = 3_500) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    chunks: list[str] = []
    remaining = stripped
    while len(remaining) > limit:
        split_at = max(remaining.rfind("\n", 0, limit), remaining.rfind(" ", 0, limit))
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


class GatewayService:
    def __init__(
        self,
        settings: Settings,
        store: StateStore,
        metrics: Metrics,
        sender: Sender,
        dify: ChatClient,
        prometheus: MonitorClient,
    ) -> None:
        self.settings = settings
        self.store = store
        self.metrics = metrics
        self.sender = sender
        self.dify = dify
        self.prometheus = prometheus

    @classmethod
    def build(cls, settings: Settings, store: StateStore, metrics: Metrics) -> GatewayService:
        return cls(
            settings,
            store,
            metrics,
            FeishuSender(
                settings.feishu_base_url,
                settings.feishu_app_id,
                settings.feishu_app_secret,
                metrics,
            ),
            DifyClient(
                settings.dify_base_url,
                settings.dify_health_url,
                settings.dify_api_key,
                metrics,
            ),
            PrometheusClient(settings.prometheus_base_url),
        )

    async def _send_chunks(self, recipient: str, text: str) -> None:
        chunks = split_text(text)
        if not chunks:
            raise GatewayError("EMPTY_MESSAGE", "Message is empty", 422)
        for chunk in chunks:
            await self.sender.send_text(recipient, chunk)

    async def deliver_notification(
        self, event_id: str, notification: Notification
    ) -> AcceptedResponse:
        if not self.store.claim_event(f"notification:{event_id}"):
            self.metrics.deliveries.labels(notification.source, "deduplicated").inc()
            return AcceptedResponse(accepted=0, deduplicated=1)
        key = f"notification:{event_id}"
        message = f"[{notification.severity.upper()}] {notification.title}\n{notification.text}"
        if notification.url is not None:
            message += f"\n{notification.url}"
        try:
            await self._send_chunks(self.settings.feishu_alert_recipient_open_id, message)
        except Exception:
            self.store.release_event(key)
            self.metrics.deliveries.labels(notification.source, "error").inc()
            raise
        self.store.mark_processed(key)
        self.metrics.deliveries.labels(notification.source, "success").inc()
        self.metrics.last_success.labels("notification").set(time.time())
        logger.info(
            "notification delivered",
            extra={
                "event": "notification_delivered",
                "source": notification.source,
                "result": "success",
            },
        )
        return AcceptedResponse(accepted=1, deduplicated=0)

    async def deliver_alerts(self, payload: AlertmanagerPayload) -> AcceptedResponse:
        accepted = 0
        deduplicated = 0
        for alert in payload.alerts:
            key = self._alert_key(alert)
            if not self.store.claim_event(key):
                deduplicated += 1
                self.metrics.deliveries.labels("alertmanager", "deduplicated").inc()
                continue
            try:
                await self._send_chunks(
                    self.settings.feishu_alert_recipient_open_id, self._format_alert(alert)
                )
            except Exception:
                self.store.release_event(key)
                self.metrics.deliveries.labels("alertmanager", "error").inc()
                raise
            self.store.mark_processed(key)
            accepted += 1
            self.metrics.deliveries.labels("alertmanager", "success").inc()
        if accepted:
            self.metrics.last_success.labels("alertmanager").set(time.time())
        return AcceptedResponse(accepted=accepted, deduplicated=deduplicated)

    @staticmethod
    def _alert_key(alert: Alert) -> str:
        ending = "" if alert.ends_at is None else alert.ends_at.isoformat()
        material = f"{alert.fingerprint}|{alert.status}|{alert.starts_at.isoformat()}|{ending}"
        return "alert:" + hashlib.sha256(material.encode()).hexdigest()

    @staticmethod
    def _format_alert(alert: Alert) -> str:
        name = alert.labels.get("alertname", "UnnamedAlert")[:128]
        severity = alert.labels.get("severity", "unknown")[:32]
        summary = alert.annotations.get("summary", "")[:1_024]
        description = alert.annotations.get("description", "")[:2_048]
        status = "FIRING" if alert.status == "firing" else "RESOLVED"
        parts = [f"[{status}] {name}", f"severity: {severity}"]
        if summary:
            parts.append(summary)
        if description:
            parts.append(description)
        return "\n".join(parts)

    async def handle_feishu_text(self, event_id: str, open_id: str, text: str) -> None:
        if not hmac.compare_digest(open_id, self.settings.feishu_allowed_open_id):
            self.metrics.deliveries.labels("feishu-chat", "forbidden").inc()
            logger.warning(
                "unauthorized feishu sender",
                extra={
                    "event": "feishu_sender_rejected",
                    "source": "feishu-chat",
                    "result": "forbidden",
                },
            )
            return
        key = "feishu:" + hashlib.sha256(event_id.encode()).hexdigest()
        if not self.store.claim_event(key):
            self.metrics.deliveries.labels("feishu-chat", "deduplicated").inc()
            return
        user_hash = pseudonymous_user(self.settings.user_hmac_key, open_id)
        try:
            reply = await self._command_or_chat(text, user_hash)
            await self._send_chunks(open_id, reply)
        except Exception:
            self.store.release_event(key)
            self.metrics.deliveries.labels("feishu-chat", "error").inc()
            raise
        self.store.mark_processed(key)
        self.metrics.deliveries.labels("feishu-chat", "success").inc()
        self.metrics.last_success.labels("chat").set(time.time())

    async def _command_or_chat(self, text: str, user_hash: str) -> str:
        command = text.strip().lower()
        if command == "/help":
            return (
                "可用命令：\n"
                "/help 查看帮助\n"
                "/new 开始新会话\n"
                "/status 查看只读健康状态\n"
                "/alerts 查看当前告警"
            )
        if command == "/new":
            self.store.clear_conversation(user_hash)
            return "已开始新的 Dify 会话。"
        if command == "/status":
            feishu_ok, dify_ok, prometheus_ok = await self.readiness()
            return (
                "只读状态：\n"
                f"Feishu: {'ok' if feishu_ok else 'down'}\n"
                f"Dify: {'ok' if dify_ok else 'down'}\n"
                f"Prometheus: {'ok' if prometheus_ok else 'down'}"
            )
        if command == "/alerts":
            alerts = await self.prometheus.active_alerts()
            if not alerts:
                return "当前没有可见的 active alert。"
            lines = [f"- {name}: {state}" for name, state in alerts]
            return "当前告警：\n" + "\n".join(lines)
        conversation_id = self.store.conversation_for(user_hash)
        response = await self.dify.chat(text, user_hash, conversation_id)
        self.store.set_conversation(user_hash, response.conversation_id)
        return response.answer

    async def readiness(self) -> tuple[bool, bool, bool]:
        feishu_check: Awaitable[bool] = self.sender.ready()
        dify_check: Awaitable[bool] = self.dify.ready()
        prometheus_check: Awaitable[bool] = self.prometheus.ready()
        import asyncio

        feishu_ok, dify_ok, prometheus_ok = await asyncio.gather(
            feishu_check, dify_check, prometheus_check
        )
        return feishu_ok, dify_ok, prometheus_ok

    async def close(self) -> None:
        await self.sender.close()
        await self.dify.close()
        await self.prometheus.close()
        self.store.close()
