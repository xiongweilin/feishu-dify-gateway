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
