#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
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
RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
ACTIVE_QUEUE_STATUSES = frozenset({"queued", "retrying"})


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
    if path == "/webhooks/github":
        # GitHub webhook -> Feishu notification design removed 2026-08-08
        # (check_suite spam). Acknowledge with 2xx but never forward.
        event = _string(headers.get("X-GitHub-Event"), 64) or "unknown"
        delivery = _string(headers.get("X-GitHub-Delivery"), 200)
        event_id = delivery if EVENT_ID_RE.fullmatch(delivery) else hashlib.sha256(body).hexdigest()
        logger.info(
            "github webhook ignored by design",
            extra={"event": "github_ignored", "github_event": event},
        )
        return event_id, None
    elif path == "/webhooks/sonar":
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
    status: str = "queued"
    last_error_code: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "notification": self.notification,
            "attempts": self.attempts,
            "next_attempt_at": self.next_attempt_at,
            "created_at": self.created_at,
            "status": self.status,
            "last_error_code": self.last_error_code,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueueItem:
        notification = data.get("notification")
        if not isinstance(notification, dict):
            raise ValueError("Queue notification must be an object")
        event_id = data.get("event_id")
        if not isinstance(event_id, str) or not EVENT_ID_RE.fullmatch(event_id):
            raise ValueError("Queue event id is invalid")
        return cls(
            event_id=event_id,
            notification=notification,
            attempts=int(data.get("attempts", 0)),
            next_attempt_at=int(data.get("next_attempt_at", 0)),
            created_at=int(data.get("created_at", 0)),
            status=str(data.get("status", "queued")),
            last_error_code=str(data.get("last_error_code", "")),
        )


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
                    if not isinstance(data, dict):
                        raise ValueError("Queue item must be an object")
                    item = QueueItem.from_dict(data)
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    logger.error(
                        "queue item unreadable",
                        extra={"event": "queue_item_invalid", "error_code": "INVALID_QUEUE_ITEM"},
                    )
                    continue
                if item.status in ACTIVE_QUEUE_STATUSES and item.next_attempt_at <= current:
                    results.append((path, item))
        return results

    def failed(
        self,
        path: Path,
        item: QueueItem,
        error_code: str = "GATEWAY_UNAVAILABLE",
        permanent: bool = False,
    ) -> None:
        item.attempts += 1
        item.last_error_code = error_code
        if permanent:
            item.status = "permanent_failed"
            item.next_attempt_at = 0
        else:
            item.status = "retrying"
            item.next_attempt_at = int(time.time()) + min(300, 2 ** min(item.attempts, 8))
        with self._lock:
            if path.exists():
                self._write(path, item)

    def acknowledged(self, path: Path) -> None:
        with self._lock:
            path.unlink(missing_ok=True)

    def depth(self) -> int:
        return sum(1 for _ in self.directory.glob("*.json"))

    def _count_status(self, statuses: frozenset[str]) -> int:
        count = 0
        with self._lock:
            for path in self.directory.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and str(data.get("status", "queued")) in statuses:
                        count += 1
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
        return count

    def pending_depth(self) -> int:
        return self._count_status(ACTIVE_QUEUE_STATUSES)

    def permanent_failure_depth(self) -> int:
        return self._count_status(frozenset({"permanent_failed"}))


class DeliveryLedger:
    """Metadata-only relay ledger; notification bodies remain in the durable queue."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS delivery_ledger (
                    event_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    transport_accepted INTEGER NOT NULL DEFAULT 0,
                    delivery_confirmed INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error_code TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    transport_accepted_at INTEGER,
                    delivery_confirmed_at INTEGER,
                    next_retry_at INTEGER,
                    terminal_at INTEGER
                );
                """
            )

    def record_queued(self, event_id: str, source: str, now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None else now
        with self._lock:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO delivery_ledger(
                    event_id, source, status, created_at, updated_at
                ) VALUES (?, ?, 'queued', ?, ?)
                """,
                (event_id, source, timestamp, timestamp),
            )

    def record_attempt(self, event_id: str, source: str, now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None else now
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO delivery_ledger(
                    event_id, source, status, attempts, created_at, updated_at
                ) VALUES (?, ?, 'delivering', 1, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    source=excluded.source,
                    status='delivering',
                    attempts=delivery_ledger.attempts + 1,
                    updated_at=excluded.updated_at,
                    last_error_code='',
                    next_retry_at=NULL,
                    terminal_at=NULL
                """,
                (event_id, source, timestamp, timestamp),
            )

    def mark_transport_accepted(self, event_id: str, now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None else now
        with self._lock:
            self._connection.execute(
                """
                UPDATE delivery_ledger
                SET status='transport_accepted', transport_accepted=1,
                    updated_at=?, transport_accepted_at=COALESCE(transport_accepted_at, ?),
                    last_error_code='', next_retry_at=NULL
                WHERE event_id=?
                """,
                (timestamp, timestamp, event_id),
            )

    def mark_retrying(
        self, event_id: str, error_code: str, next_retry_at: int, now: int | None = None
    ) -> None:
        timestamp = int(time.time()) if now is None else now
        with self._lock:
            self._connection.execute(
                """
                UPDATE delivery_ledger
                SET status='retrying', updated_at=?, last_error_code=?, next_retry_at=?,
                    terminal_at=NULL
                WHERE event_id=?
                """,
                (timestamp, error_code, next_retry_at, event_id),
            )

    def mark_permanent_failed(
        self, event_id: str, error_code: str, now: int | None = None
    ) -> None:
        timestamp = int(time.time()) if now is None else now
        with self._lock:
            self._connection.execute(
                """
                UPDATE delivery_ledger
                SET status='permanent_failed', updated_at=?, last_error_code=?,
                    next_retry_at=NULL, terminal_at=?
                WHERE event_id=?
                """,
                (timestamp, error_code, timestamp, event_id),
            )

    def get(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM delivery_ledger WHERE event_id = ?", (event_id,)
            ).fetchone()
        return None if row is None else dict(row)

    def prune(self, retention_seconds: int, now: int | None = None) -> int:
        cutoff = (int(time.time()) if now is None else now) - retention_seconds
        with self._lock:
            cursor = self._connection.execute(
                """
                DELETE FROM delivery_ledger
                WHERE updated_at < ?
                  AND status IN ('transport_accepted', 'delivery_confirmed', 'permanent_failed')
                """,
                (cutoff,),
            )
        return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._connection.close()


@dataclass(slots=True)
class RelayMetrics:
    received: int = 0
    duplicates: int = 0
    delivered: int = 0
    failed: int = 0
    last_success: int = 0
    attempts: int = 0
    retryable_failures: int = 0
    permanent_failures: int = 0
    last_transport_accepted: int = 0

    def render(
        self, queue_depth: int, pending_queue_depth: int = 0, permanent_failure_depth: int = 0
    ) -> bytes:
        lines = [
            "# TYPE feishu_relay_webhooks_received_total counter",
            f"feishu_relay_webhooks_received_total {self.received}",
            "# TYPE feishu_relay_duplicates_total counter",
            f"feishu_relay_duplicates_total {self.duplicates}",
            "# TYPE feishu_relay_deliveries_total counter",
            f'feishu_relay_deliveries_total{{result="success"}} {self.delivered}',
            f'feishu_relay_deliveries_total{{result="error"}} {self.failed}',
            "# TYPE feishu_relay_delivery_attempts_total counter",
            f'feishu_relay_delivery_attempts_total{{result="attempt"}} {self.attempts}',
            f'feishu_relay_delivery_attempts_total{{result="transport_accepted"}} {self.delivered}',
            f'feishu_relay_delivery_attempts_total{{result="retryable_failure"}} '
            f"{self.retryable_failures}",
            f'feishu_relay_delivery_attempts_total{{result="permanent_failure"}} '
            f"{self.permanent_failures}",
            "# TYPE feishu_relay_queue_depth gauge",
            f"feishu_relay_queue_depth {queue_depth}",
            "# TYPE feishu_relay_pending_queue_depth gauge",
            f"feishu_relay_pending_queue_depth {pending_queue_depth}",
            "# TYPE feishu_relay_permanent_failure_depth gauge",
            f"feishu_relay_permanent_failure_depth {permanent_failure_depth}",
            "# TYPE feishu_relay_last_success_timestamp_seconds gauge",
            f"feishu_relay_last_success_timestamp_seconds {self.last_success}",
            "# TYPE feishu_relay_last_transport_accepted_timestamp_seconds gauge",
            "feishu_relay_last_transport_accepted_timestamp_seconds "
            f"{self.last_transport_accepted}",
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
        ledger: DeliveryLedger | None = None,
        max_attempts: int = 5,
    ) -> None:
        super().__init__(name="gateway-delivery", daemon=True)
        self.queue = queue
        self.gateway_url = gateway_url
        self.hmac_secret = hmac_secret
        self.metrics = metrics
        self.ledger = ledger
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.max_attempts = max_attempts

    def run(self) -> None:
        while True:
            for path, item in self.queue.due():
                self._deliver(path, item)
            time.sleep(2)

    def _deliver(self, path: Path, item: QueueItem) -> None:
        source = item.notification.get("source")
        source_name = source if isinstance(source, str) and source else "unknown"
        attempt = item.attempts + 1
        if self.ledger is not None:
            self.ledger.record_attempt(item.event_id, source_name)
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
        success = False
        retryable = True
        error_code = "GATEWAY_UNAVAILABLE"
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                success = 200 <= response.status < 300
                if not success:
                    error_code = f"HTTP_{response.status}"
                    retryable = response.status in RETRYABLE_HTTP_STATUSES
        except urllib.error.HTTPError as exc:
            error_code = f"HTTP_{exc.code}"
            retryable = exc.code in RETRYABLE_HTTP_STATUSES
        except (OSError, urllib.error.URLError, TimeoutError):
            retryable = True
        if success:
            if self.ledger is not None:
                self.ledger.mark_transport_accepted(item.event_id)
            self.queue.acknowledged(path)
            self.metrics.delivered += 1
            self.metrics.attempts += 1
            self.metrics.last_success = int(time.time())
            self.metrics.last_transport_accepted = self.metrics.last_success
            logger.info(
                "gateway delivery succeeded",
                extra={
                    "event": "relay_delivery",
                    "result": "transport_accepted",
                    "attempt": attempt,
                },
            )
            return
        self.metrics.attempts += 1
        self.metrics.failed += 1
        permanent = not retryable or attempt >= self.max_attempts
        self.queue.failed(path, item, error_code=error_code, permanent=permanent)
        if self.ledger is not None:
            if permanent:
                self.ledger.mark_permanent_failed(item.event_id, error_code)
            else:
                self.ledger.mark_retrying(item.event_id, error_code, item.next_attempt_at)
        if permanent:
            self.metrics.permanent_failures += 1
            result = "permanent_failure"
        else:
            self.metrics.retryable_failures += 1
            result = "retryable_failure"
        logger.warning(
            "gateway delivery failed",
            extra={
                "event": "relay_delivery",
                "result": result,
                "attempt": attempt,
                "error_code": error_code,
            },
        )


def handler_factory(
    queue: DurableQueue, metrics: RelayMetrics, ledger: DeliveryLedger | None = None
) -> type[BaseHTTPRequestHandler]:
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
            metrics.received += 1
            if notification is None:
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"status": "ignored", "event_id": event_id}).encode() + b"\n"
                )
                return
            inserted = queue.enqueue(event_id, notification)
            if not inserted:
                metrics.duplicates += 1
            elif ledger is not None:
                source = notification.get("source")
                ledger.record_queued(
                    event_id, source if isinstance(source, str) and source else "unknown"
                )
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"status": "accepted", "event_id": event_id}).encode() + b"\n"
            )

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/healthz":
                body = b'{"status":"ok"}\n'
                content_type = "application/json"
            elif path == "/metrics":
                body = metrics.render(
                    queue.depth(), queue.pending_depth(), queue.permanent_failure_depth()
                )
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
    ledger = DeliveryLedger(
        Path(os.getenv("RELAY_LEDGER_PATH", str(queue.directory.parent / "delivery-ledger.db")))
    )
    ledger.prune(int(os.getenv("RELAY_LEDGER_RETENTION_SECONDS", "604800")))
    metrics = RelayMetrics()
    secret = read_secret(
        Path(os.getenv("RELAY_HMAC_SECRET_FILE", "/etc/feishu-relay/notification_hmac_key"))
    )
    worker = DeliveryWorker(
        queue,
        os.getenv(
            "RELAY_GATEWAY_URL",
            "http://gateway.example.internal:8082/v1/notifications",
        ),
        secret,
        metrics,
        ledger,
        max_attempts=int(os.getenv("RELAY_MAX_ATTEMPTS", "5")),
    )
    worker.start()
    server = ThreadingHTTPServer(
        (os.getenv("RELAY_BIND_HOST", "172.24.0.1"), int(os.getenv("RELAY_PORT", "9090"))),
        handler_factory(queue, metrics, ledger),
    )
    logger.info("relay started", extra={"event": "relay_started", "result": "success"})
    server.serve_forever()


if __name__ == "__main__":
    main()
