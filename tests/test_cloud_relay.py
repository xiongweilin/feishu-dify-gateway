from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from cloud.relay import DeliveryLedger, DeliveryWorker, DurableQueue, RelayMetrics, normalize_event


def test_github_webhook_is_acknowledged_but_not_forwarded() -> None:
    body = json.dumps(
        {
            "repository": {"full_name": "owner/repo", "private_secret": "do-not-keep"},
            "workflow_run": {
                "name": "CI",
                "conclusion": "failure",
                "html_url": "https://example.invalid/run/1",
                "logs": "do-not-keep",
            },
            "raw_private_data": "do-not-keep",
        }
    ).encode()
    event_id, notification = normalize_event(
        "/webhooks/github", {"X-GitHub-Event": "check_suite", "X-GitHub-Delivery": "d-1"}, body
    )
    assert event_id == "d-1"
    assert notification is None


def test_queue_keeps_item_until_acknowledged(tmp_path: Path) -> None:
    queue = DurableQueue(tmp_path / "queue")
    assert queue.enqueue("event", {"source": "test"}) is True
    assert queue.enqueue("event", {"source": "test"}) is False
    due = queue.due(now=2**31)
    assert len(due) == 1
    path, item = due[0]
    queue.failed(path, item)
    assert queue.depth() == 1
    queue.acknowledged(path)
    assert queue.depth() == 0


def test_similar_but_unapproved_webhook_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported webhook path"):
        normalize_event("/untrusted/github", {}, b"{}")


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_relay_ledger_distinguishes_transport_acceptance(tmp_path: Path) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery-ledger.db")
    ledger.record_queued("event", "test", now=10)
    ledger.record_attempt("event", "test", now=11)
    ledger.mark_transport_accepted("event", now=12)
    entry = ledger.get("event")
    ledger.close()
    assert entry is not None
    assert entry["status"] == "transport_accepted"
    assert entry["transport_accepted"] == 1
    assert entry["delivery_confirmed"] == 0
    assert entry["attempts"] == 1


def test_relay_retries_then_records_transport_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = DurableQueue(tmp_path / "queue")
    ledger = DeliveryLedger(tmp_path / "delivery-ledger.db")
    queue.enqueue("event", {"source": "test"})
    ledger.record_queued("event", "test")
    path, item = queue.due(now=2**31)[0]
    calls = 0

    def flaky_urlopen(request: object, timeout: int) -> FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError("http://gateway.invalid", 503, "unavailable", None, None)
        return FakeResponse(202)

    monkeypatch.setattr("cloud.relay.urllib.request.urlopen", flaky_urlopen)
    worker = DeliveryWorker(queue, "http://gateway.invalid", "test-secret", RelayMetrics(), ledger)
    worker._deliver(path, item)
    retry_path, retry_item = queue.due(now=2**31)[0]
    worker._deliver(retry_path, retry_item)
    entry = ledger.get("event")
    ledger.close()
    assert calls == 2
    assert entry is not None
    assert entry["status"] == "transport_accepted"
    assert entry["attempts"] == 2
    assert queue.pending_depth() == 0


def test_relay_records_permanent_failure_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = DurableQueue(tmp_path / "queue")
    ledger = DeliveryLedger(tmp_path / "delivery-ledger.db")
    queue.enqueue("event", {"source": "test"})
    ledger.record_queued("event", "test")
    path, item = queue.due(now=2**31)[0]

    def rejected_urlopen(request: object, timeout: int) -> FakeResponse:
        raise urllib.error.HTTPError("http://gateway.invalid", 422, "rejected", None, None)

    monkeypatch.setattr("cloud.relay.urllib.request.urlopen", rejected_urlopen)
    worker = DeliveryWorker(queue, "http://gateway.invalid", "test-secret", RelayMetrics(), ledger)
    worker._deliver(path, item)
    entry = ledger.get("event")
    ledger.close()
    assert entry is not None
    assert entry["status"] == "permanent_failed"
    assert entry["last_error_code"] == "HTTP_422"
    assert queue.pending_depth() == 0
    assert queue.permanent_failure_depth() == 1


def test_relay_retry_budget_ends_in_permanent_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = DurableQueue(tmp_path / "queue")
    ledger = DeliveryLedger(tmp_path / "delivery-ledger.db")
    queue.enqueue("event", {"source": "test"})
    ledger.record_queued("event", "test")

    def unavailable_urlopen(request: object, timeout: int) -> FakeResponse:
        raise urllib.error.URLError("unavailable")

    monkeypatch.setattr("cloud.relay.urllib.request.urlopen", unavailable_urlopen)
    worker = DeliveryWorker(
        queue,
        "http://gateway.invalid",
        "test-secret",
        RelayMetrics(),
        ledger,
        max_attempts=2,
    )
    path, item = queue.due(now=2**31)[0]
    worker._deliver(path, item)
    path, item = queue.due(now=2**31)[0]
    worker._deliver(path, item)
    entry = ledger.get("event")
    ledger.close()
    assert entry is not None
    assert entry["status"] == "permanent_failed"
    assert entry["attempts"] == 2
    assert queue.pending_depth() == 0
