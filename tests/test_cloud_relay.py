from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloud.relay import DurableQueue, normalize_event


def test_github_normalization_keeps_only_allowlisted_fields() -> None:
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
        "/webhooks/github", {"X-GitHub-Event": "workflow_run", "X-GitHub-Delivery": "d-1"}, body
    )
    serialized = json.dumps(notification)
    assert event_id == "d-1"
    assert notification["severity"] == "warning"
    assert "owner/repo" in notification["text"]
    assert "do-not-keep" not in serialized


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
