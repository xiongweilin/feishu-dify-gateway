from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

ShortText = Annotated[str, Field(min_length=1, max_length=512)]
LongText = Annotated[str, Field(min_length=1, max_length=10_000)]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Notification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: Literal["github", "sonar", "windows-maintenance", "watchdog", "test", "control-plane"]
    severity: Literal["info", "warning", "critical"]
    title: ShortText
    text: LongText
    occurred_at: datetime = Field(alias="occurredAt")
    url: HttpUrl | None = None


class Alert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generator_url: str = Field(default="", alias="generatorURL", max_length=2_048)
    fingerprint: str = Field(min_length=1, max_length=256)


class AlertmanagerPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str = "4"
    status: Literal["firing", "resolved"]
    alerts: list[Alert] = Field(min_length=1, max_length=100)


class AcceptedResponse(BaseModel):
    accepted: int
    deduplicated: int


class NotificationAcceptedResponse(AcceptedResponse):
    event_id: str
    status: Literal[
        "deduplicated",
        "delivering",
        "retrying",
        "permanent_failed",
        "delivery_confirmed",
    ]
    transport_accepted: bool = Field(alias="transportAccepted")
    delivery_confirmed: bool = Field(alias="deliveryConfirmed")

    model_config = ConfigDict(populate_by_name=True)


class SyntheticPrepareResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId")
    status: Literal["prepared"]
    transport_accepted: bool = Field(alias="transportAccepted")
    delivery_confirmed: bool = Field(alias="deliveryConfirmed")
    external_send_started: bool = Field(alias="externalSendStarted")
    requires_manual_confirmation: bool = Field(alias="requiresManualConfirmation")


class SyntheticProbeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["ready"]
    synthetic_enabled: bool = Field(alias="syntheticEnabled")
    external_send_started: bool = Field(alias="externalSendStarted")
    requires_manual_confirmation: bool = Field(alias="requiresManualConfirmation")


class DeliveryLedgerResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId")
    source: str
    status: Literal[
        "prepared",
        "delivering",
        "retrying",
        "permanent_failed",
        "transport_accepted",
        "delivery_confirmed",
    ]
    transport_accepted: bool = Field(alias="transportAccepted")
    delivery_confirmed: bool = Field(alias="deliveryConfirmed")
    attempts: int
    last_error_code: str = Field(alias="lastErrorCode")
    created_at: int = Field(alias="createdAt")
    updated_at: int = Field(alias="updatedAt")
    transport_accepted_at: int | None = Field(default=None, alias="transportAcceptedAt")
    delivery_confirmed_at: int | None = Field(default=None, alias="deliveryConfirmedAt")
    next_retry_at: int | None = Field(default=None, alias="nextRetryAt")
    terminal_at: int | None = Field(default=None, alias="terminalAt")
    synthetic: bool


class FeishuTokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: int
    msg: str = ""
    tenant_access_token: str | None = None
    expire: int = 0


class FeishuMessageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: int
    msg: str = ""
    data: dict[str, object] | None = None


class AdministrativeCommunicationRequest(BaseModel):
    """One metadata-addressed internal Feishu message.

    The text is accepted only for immediate transport.  The gateway never
    persists this model or its body; only a digest and bounded delivery state
    enter the ledger.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    event_id: ShortText = Field(alias="eventId")
    recipient_open_id: ShortText = Field(alias="recipientOpenId")
    text: LongText
    draft_kind: ShortText = Field(alias="draftKind")


AdministrativeCommunicationStatus = Literal[
    "prepared",
    "delivering",
    "retrying",
    "transport_accepted",
    "delivery_confirmed",
    "outcome_unknown",
    "permanent_failed",
    "deduplicated",
]


class AdministrativeCommunicationAcceptedResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId")
    status: AdministrativeCommunicationStatus
    transport_accepted: bool = Field(alias="transportAccepted")
    delivery_confirmed: bool = Field(alias="deliveryConfirmed")
    provider_message_ref: str | None = Field(default=None, alias="providerMessageRef")


class AdministrativeCommunicationLedgerResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId")
    status: str
    transport_accepted: bool = Field(alias="transportAccepted")
    delivery_confirmed: bool = Field(alias="deliveryConfirmed")
    attempts: int
    body_digest: str = Field(alias="bodyDigest")
    recipient_digest: str = Field(alias="recipientDigest")
    provider_message_ref: str | None = Field(default=None, alias="providerMessageRef")
    last_error_code: str = Field(alias="lastErrorCode")
    created_at: int = Field(alias="createdAt")
    updated_at: int = Field(alias="updatedAt")
