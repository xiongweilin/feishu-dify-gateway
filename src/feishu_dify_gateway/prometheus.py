from __future__ import annotations

import httpx


class PrometheusClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0), transport=transport)

    async def ready(self) -> bool:
        try:
            response = await self._client.get(f"{self._base_url}/-/ready")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def active_alerts(self) -> list[tuple[str, str]]:
        try:
            response = await self._client.get(f"{self._base_url}/api/v1/alerts")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        if not isinstance(payload, dict) or payload.get("status") != "success":
            return []
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("alerts"), list):
            return []
        results: list[tuple[str, str]] = []
        for item in data["alerts"][:20]:
            if not isinstance(item, dict):
                continue
            labels = item.get("labels")
            state = item.get("state")
            if not isinstance(labels, dict) or not isinstance(state, str):
                continue
            name = labels.get("alertname")
            if isinstance(name, str) and name:
                results.append((name[:128], state[:32]))
        return results

    async def close(self) -> None:
        await self._client.aclose()
