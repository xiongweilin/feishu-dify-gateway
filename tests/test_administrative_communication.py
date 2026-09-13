from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from feishu_dify_gateway.app import create_app
from feishu_dify_gateway.security import sign_request
from feishu_dify_gateway.service import GatewayService

from .conftest import FakeControlPlane, FakePrometheus, FakeSender


def _signed(settings, event_id: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-Event-ID": event_id,
        "X-Timestamp": timestamp,
        "X-Signature": sign_request(
            settings.administrative_communication_hmac_key,
            timestamp,
            event_id,
            body,
        ),
    }


def test_administrative_communication_is_idempotent_and_metadata_only(
    settings,
    service: tuple[GatewayService, FakeSender, FakePrometheus, FakeControlPlane],
) -> None:
    gateway, sender, _, _ = service
    settings = replace(settings, administrative_communication_hmac_key="m9-transport-key")
    body = json.dumps(
        {
            "eventId": "m9-event-1",
            "recipientOpenId": "ou_committer",
            "text": "private commitment body",
            "draftKind": "confirmation",
        },
        separators=(",", ":"),
    ).encode()
    app = create_app(settings, service=gateway, start_long_connection=False)
    with TestClient(app, base_url=f"http://testserver:{settings.port}") as client:
        first = client.post(
            "/v1/administrative/communications",
            content=body,
            headers=_signed(settings, "m9-event-1", body),
        )
        assert first.status_code == 202
        assert first.json()["status"] == "transport_accepted"
        assert first.json()["deliveryConfirmed"] is False

        replay = client.post(
            "/v1/administrative/communications",
            content=body,
            headers=_signed(settings, "m9-event-1", body),
        )
        assert replay.status_code == 202
        assert replay.json()["status"] == "transport_accepted"

        ledger = client.get(
            "/v1/administrative/communications/m9-event-1",
            headers=_signed(settings, "m9-event-1", b""),
        )
        assert ledger.status_code == 200
        assert ledger.json()["deliveryConfirmed"] is False

        client.base_url = client.base_url.copy_with(port=settings.public_port)
        assert (
            client.post(
                "/v1/administrative/communications",
                content=body,
                headers=_signed(settings, "m9-event-1", body),
            ).status_code
            == 404
        )

    assert len(sender.messages) == 1
    connection = sqlite3.connect(settings.state_db)
    try:
        raw = connection.execute(
            "SELECT body_digest, recipient_digest FROM administrative_communication_ledger"
        ).fetchone()
    finally:
        connection.close()
    assert raw is not None
    assert "private commitment body" not in str(raw)


def test_administrative_communication_rejects_identity_rebinding(
    settings,
    service: tuple[GatewayService, FakeSender, FakePrometheus, FakeControlPlane],
) -> None:
    gateway, sender, _, _ = service
    settings = replace(settings, administrative_communication_hmac_key="m9-transport-key")
    app = create_app(settings, service=gateway, start_long_connection=False)
    first_body = json.dumps(
        {
            "eventId": "m9-event-2",
            "recipientOpenId": "ou_committer",
            "text": "first",
            "draftKind": "confirmation",
        },
        separators=(",", ":"),
    ).encode()
    second_body = first_body.replace(b"first", b"second")
    with TestClient(app, base_url=f"http://testserver:{settings.port}") as client:
        assert (
            client.post(
                "/v1/administrative/communications",
                content=first_body,
                headers=_signed(settings, "m9-event-2", first_body),
            ).status_code
            == 202
        )
        response = client.post(
            "/v1/administrative/communications",
            content=second_body,
            headers=_signed(settings, "m9-event-2", second_body),
        )
    assert response.status_code == 409
    assert len(sender.messages) == 1
