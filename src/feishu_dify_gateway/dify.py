from __future__ import annotations

from time import perf_counter

import httpx
from pydantic import ValidationError

from .errors import GatewayError
from .metrics import Metrics
from .models import DifyChatResponse


class DifyClient:
    def __init__(
        self,
        base_url: str,
        health_url: str,
        api_key: str,
        metrics: Metrics,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._health_url = health_url
        self._api_key = api_key
        self._metrics = metrics
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(90.0), transport=transport)

    async def chat(self, query: str, user: str, conversation_id: str = "") -> DifyChatResponse:
        started = perf_counter()
        try:
            response = await self._client.post(
                f"{self._base_url}/chat-messages",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "inputs": {},
                    "query": query,
                    "response_mode": "blocking",
                    "conversation_id": conversation_id,
                    "user": user,
                },
            )
            response.raise_for_status()
            parsed = DifyChatResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            self._metrics.external_requests.labels("dify", "error").inc()
            raise GatewayError("DIFY_UNAVAILABLE", "Dify request failed") from exc
        finally:
            self._metrics.external_duration.labels("dify").observe(perf_counter() - started)
        self._metrics.external_requests.labels("dify", "success").inc()
        return parsed

    async def ready(self) -> bool:
        try:
            response = await self._client.get(self._health_url, timeout=5.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()
