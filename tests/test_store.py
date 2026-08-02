from __future__ import annotations

from pathlib import Path

from feishu_dify_gateway.store import StateStore


def test_claim_complete_and_release_are_idempotent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    assert store.claim_event("event") is True
    assert store.claim_event("event") is False
    store.release_event("event")
    assert store.claim_event("event") is True
    store.mark_processed("event")
    store.release_event("event")
    assert store.claim_event("event") is False
    store.close()


def test_conversation_mapping_contains_no_message_body(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.set_conversation("user-hash", "conversation-id")
    assert store.conversation_for("user-hash") == "conversation-id"
    store.clear_conversation("user-hash")
    assert store.conversation_for("user-hash") == ""
    assert b"message body" not in path.read_bytes()
    store.close()
