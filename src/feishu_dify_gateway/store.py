from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class StateStore:
    """SQLite state containing metadata only; message bodies are never persisted."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    user_hash TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )

    def is_processed(self, event_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return row is not None

    def claim_event(self, event_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO processed_events(event_id, status, created_at) "
                "VALUES (?, 'processing', ?)",
                (event_id, int(time.time())),
            )
        return cursor.rowcount == 1

    def mark_processed(self, event_id: str, status: str = "delivered") -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO processed_events(event_id, status, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(event_id) DO UPDATE SET status=excluded.status",
                (event_id, status, int(time.time())),
            )

    def release_event(self, event_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM processed_events WHERE event_id = ? AND status = 'processing'",
                (event_id,),
            )

    def conversation_for(self, user_hash: str) -> str:
        with self._lock:
            row = self._connection.execute(
                "SELECT conversation_id FROM conversations WHERE user_hash = ?", (user_hash,)
            ).fetchone()
        return "" if row is None else str(row["conversation_id"])

    def set_conversation(self, user_hash: str, conversation_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO conversations(user_hash, conversation_id, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(user_hash) DO UPDATE SET "
                "conversation_id=excluded.conversation_id, updated_at=excluded.updated_at",
                (user_hash, conversation_id, int(time.time())),
            )

    def clear_conversation(self, user_hash: str) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM conversations WHERE user_hash = ?", (user_hash,))

    def prune(self, retention_seconds: int) -> int:
        cutoff = int(time.time()) - retention_seconds
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM processed_events WHERE created_at < ?", (cutoff,)
            )
        return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._connection.close()
