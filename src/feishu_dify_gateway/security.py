from __future__ import annotations

import hashlib
import hmac
import re
import time

EVENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


class SignatureError(ValueError):
    """Raised when an authenticated internal request is invalid."""


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_request(timestamp: str, event_id: str, body: bytes) -> bytes:
    return f"{timestamp}\n{event_id}\n{body_digest(body)}".encode()


def sign_request(secret: str, timestamp: str, event_id: str, body: bytes) -> str:
    return hmac.new(
        secret.encode(), canonical_request(timestamp, event_id, body), hashlib.sha256
    ).hexdigest()


def verify_request(
    secret: str,
    timestamp: str,
    event_id: str,
    signature: str,
    body: bytes,
    *,
    ttl_seconds: int,
    now: int | None = None,
) -> None:
    if not EVENT_ID_RE.fullmatch(event_id):
        raise SignatureError("Invalid event ID")
    if not timestamp.isascii() or not timestamp.isdigit():
        raise SignatureError("Invalid timestamp")
    current = int(time.time()) if now is None else now
    if abs(current - int(timestamp)) > ttl_seconds:
        raise SignatureError("Request timestamp is outside the accepted window")
    expected = sign_request(secret, timestamp, event_id, body)
    if not hmac.compare_digest(expected, signature.lower()):
        raise SignatureError("Invalid signature")


def pseudonymous_user(secret: str, open_id: str) -> str:
    return hmac.new(secret.encode(), open_id.encode(), hashlib.sha256).hexdigest()
