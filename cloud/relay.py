#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

MAX_BODY_BYTES = 1_048_576
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
logger = logging.getLogger("feishu-relay")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "message": record.getMessage(),
        }
        for field in ("event", "source", "result", "attempt", "error_code"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)


def read_secret(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("Relay HMAC secret file is missing or unsafe")
    if path.stat().st_mode & 0o077:
        raise RuntimeError("Relay HMAC secret permissions must be 600")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("Relay HMAC secret is empty")
    return value


def canonical_request(timestamp: str, event_id: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{timestamp}\n{event_id}\n{digest}".encode()


def sign_request(secret: str, timestamp: str, event_id: str, body: bytes) -> str:
    return hmac.new(
        secret.encode(), canonical_request(timestamp, event_id, body), hashlib.sha256
    ).hexdigest()


def safe_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2_048:
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _string(value: object, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def normalize_event(path: str, headers: Any, body: bytes) -> tuple[str, dict[str, Any]]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Webhook body must be valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Webhook body must be a JSON object")

    occurred_at = datetime.now(UTC).isoformat()
    if "github" in path:
        source = "github"
        event = _string(headers.get("X-GitHub-Event"), 64) or "unknown"
        delivery = _string(headers.get("X-GitHub-Delivery"), 200)
        event_id = delivery if EVENT_ID_RE.fullmatch(delivery) else hashlib.sha256(body).hexdigest()
        repository = data.get("repository") if isinstance(data.get("repository"), dict) else {}
        workflow = data.get("workflow_run") if isinstance(data.get("workflow_run"), dict) else {}
        repo_name = _string(repository.get("full_name"), 256) or "unknown repository"
        conclusion = _string(workflow.get("conclusion"), 64)
        workflow_name = _string(workflow.get("name"), 256)
        url = safe_url(workflow.get("html_url") or repository.get("html_url"))
        detail = " / ".join(part for part in (repo_name, workflow_name, conclusion) if part)
        notification = {
            "source": source,
            "severity": (
                "warning" if conclusion in {"failure", "cancelled", "timed_out"} else "info"
            ),
            "title": f"GitHub {event}"[:512],
            "text": (detail or "GitHub webhook received")[:10_000],
            "occurredAt": occurred_at,
        }
    elif "sonar" in path:
        source = "sonar"
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        quality_gate = data.get("qualityGate") if isinstance(data.get("qualityGate"), dict) else {}
        project_name = _string(project.get("name") or project.get("key"), 256) or "unknown project"
        status = _string(quality_gate.get("status"), 64) or "unknown"
        event_id = hashlib.sha256(body).hexdigest()
        notification = {
            "source": source,
            "severity": "warning" if status.lower() not in {"ok", "passed"} else "info",
            "title": "Sonar quality gate",
            "text": f"{project_name}: {status}"[:10_000],
            "occurredAt": occurred_at,
        }
        url = safe_url(project.get("url"))
    else:
        raise ValueError("Unsupported webhook path")
    if url:
        notification["url"] = url
    return event_id, notification


@dataclass(slots=True)
class QueueItem:
    event_id: str
    notification: dict[str, Any]
    attempts: int
    next_attempt_at: int
    created_at: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "notification": self.notification,
            "attempts": self.attempts,
            "next_attempt_at": self.next_attempt_at,
            "created_at": self.created_at,
        }


class DurableQueue:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory
        self._lock = threading.RLock()

    def _path(self, event_id: str) -> Path:
        return self.directory / f"{hashlib.sha256(event_id.encode()).hexdigest()}.json"

    def _write(self, path: Path, item: QueueItem) -> None:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.directory, delete=False
        ) as handle:
            json.dump(item.as_dict(), handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        temp_path.chmod(0o600)
        os.replace(temp_path, path)

    def enqueue(self, event_id: str, notification: dict[str, Any]) -> bool:
        path = self._path(event_id)
        with self._lock:
            if path.exists():
                return False
            now = int(time.time())
            self._write(path, QueueItem(event_id, notification, 0, now, now))
        return True

    def due(self, now: int | None = None) -> list[tuple[Path, QueueItem]]:
        current = int(time.time()) if now is None else now
        results: list[tuple[Path, QueueItem]] = []
        with self._lock:
            for path in sorted(self.directory.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    item = QueueItem(**data)
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    logger.error(
                        "queue item unreadable",
                        extra={"event": "queue_item_invalid", "error_code": "INVALID_QUEUE_ITEM"},
                    )
                    continue
                if item.next_attempt_at <= current:
                    results.append((path, item))
        return results

    def failed(self, path: Path, item: QueueItem) -> None:
        item.attempts += 1
        item.next_attempt_at = int(time.time()) + min(300, 2 ** min(item.attempts, 8))
        with self._lock:
            if path.exists():
                self._write(path, item)

    def acknowledged(self, path: Path) -> None:
        with self._lock:
            path.unlink(missing_ok=True)

    def depth(self) -> int:
        return sum(1 for _ in self.directory.glob("*.json"))


@dataclass(slots=True)
class RelayMetrics:
    received: int = 0
    duplicates: int = 0
    delivered: int = 0
    failed: int = 0
    last_success: int = 0

    def render(self, queue_depth: int) -> bytes:
        lines = [
            "# TYPE feishu_relay_webhooks_received_total counter",
            f"feishu_relay_webhooks_received_total {self.received}",
            "# TYPE feishu_relay_duplicates_total counter",
            f"feishu_relay_duplicates_total {self.duplicates}",
            "# TYPE feishu_relay_deliveries_total counter",
            f'feishu_relay_deliveries_total{{result="success"}} {self.delivered}',
            f'feishu_relay_deliveries_total{{result="error"}} {self.failed}',
            "# TYPE feishu_relay_queue_depth gauge",
            f"feishu_relay_queue_depth {queue_depth}",
            "# TYPE feishu_relay_last_success_timestamp_seconds gauge",
            f"feishu_relay_last_success_timestamp_seconds {self.last_success}",
            "",
        ]
        return "\n".join(lines).encode()


class DeliveryWorker(threading.Thread):
    def __init__(
        self,
        queue: DurableQueue,
        gateway_url: str,
        hmac_secret: str,
        metrics: RelayMetrics,
    ) -> None:
        super().__init__(name="gateway-delivery", daemon=True)
        self.queue = queue
        self.gateway_url = gateway_url
        self.hmac_secret = hmac_secret
        self.metrics = metrics

    def run(self) -> None:
        while True:
            for path, item in self.queue.due():
                self._deliver(path, item)
            time.sleep(2)

    def _deliver(self, path: Path, item: QueueItem) -> None:
        body = json.dumps(item.notification, ensure_ascii=False, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = sign_request(self.hmac_secret, timestamp, item.event_id, body)
        request = urllib.request.Request(
            self.gateway_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Event-ID": item.event_id,
                "X-Timestamp": timestamp,
                "X-Signature": signature,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                success = 200 <= response.status < 300
        except (OSError, urllib.error.URLError):
            success = False
        if success:
            self.queue.acknowledged(path)
            self.metrics.delivered += 1
            self.metrics.last_success = int(time.time())
            logger.info(
                "gateway delivery succeeded",
                extra={"event": "relay_delivery", "result": "success"},
            )
            return
        self.queue.failed(path, item)
        self.metrics.failed += 1
        logger.warning(
            "gateway delivery failed",
            extra={
                "event": "relay_delivery",
                "result": "error",
                "attempt": item.attempts,
                "error_code": "GATEWAY_UNAVAILABLE",
            },
        )


def handler_factory(queue: DurableQueue, metrics: RelayMetrics) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length_text = self.headers.get("Content-Length", "0")
            if not length_text.isdigit() or int(length_text) > MAX_BODY_BYTES:
                self.send_error(413)
                return
            body = self.rfile.read(int(length_text))
            try:
                event_id, notification = normalize_event(
                    urlparse(self.path).path, self.headers, body
                )
            except ValueError:
                self.send_error(422)
                return
            inserted = queue.enqueue(event_id, notification)
            metrics.received += 1
            if not inserted:
                metrics.duplicates += 1
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"accepted"}\n')

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/healthz":
                body = b'{"status":"ok"}\n'
                content_type = "application/json"
            elif path == "/metrics":
                body = metrics.render(queue.depth())
                content_type = "text/plain; version=0.0.4"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main() -> None:
    configure_logging()
    queue = DurableQueue(Path(os.getenv("RELAY_QUEUE_DIR", "/srv/webhook-relay/queue")))
    metrics = RelayMetrics()
    secret = read_secret(
        Path(os.getenv("RELAY_HMAC_SECRET_FILE", "/etc/feishu-relay/notification_hmac_key"))
    )
    worker = DeliveryWorker(
        queue,
        os.getenv(
            "RELAY_GATEWAY_URL",
            "http://metratio.tail1f4641.ts.net:8082/v1/notifications",
        ),
        secret,
        metrics,
    )
    worker.start()
    server = ThreadingHTTPServer(
        (os.getenv("RELAY_BIND_HOST", "172.24.0.1"), int(os.getenv("RELAY_PORT", "9090"))),
        handler_factory(queue, metrics),
    )
    logger.info("relay started", extra={"event": "relay_started", "result": "success"})
    server.serve_forever()


if __name__ == "__main__":
    main()
