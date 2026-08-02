from __future__ import annotations

import pytest

from feishu_dify_gateway.security import SignatureError, sign_request, verify_request


def test_signature_round_trip() -> None:
    body = b'{"safe":true}'
    signature = sign_request("secret", "1000", "event-1", body)
    verify_request("secret", "1000", "event-1", signature, body, ttl_seconds=300, now=1000)


@pytest.mark.parametrize(
    ("timestamp", "event_id", "signature", "now"),
    [
        ("1000", "event-1", "0" * 64, 1000),
        ("1000", "bad event", "0" * 64, 1000),
        ("1000", "event-1", "0" * 64, 1400),
        ("not-a-number", "event-1", "0" * 64, 1000),
    ],
)
def test_signature_rejects_invalid_requests(
    timestamp: str, event_id: str, signature: str, now: int
) -> None:
    with pytest.raises(SignatureError):
        verify_request(
            "secret",
            timestamp,
            event_id,
            signature,
            b"{}",
            ttl_seconds=300,
            now=now,
        )
