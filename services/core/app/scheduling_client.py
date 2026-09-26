"""Cliente HTTP de la agenda del catálogo (C-2, DT-32): `POST /scheduling/<operación>`.

La agenda no va por MCP para que el LLM no la vea: la llama solo el código de `core`. Usa la misma
`CATALOG_SERVICE_URL` que el cliente MCP (`catalog_client.py`), porque las dos viven en el mismo
servicio. Cualquier fallo se traduce a `SchedulingError` con el código del contrato
(contracts/scheduling/scheduling_error.schema.json): quien llama decide si hay alternativa.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import httpx

from app.catalog_client import DEFAULT_URL

TIMEOUT_SECONDS = 5.0


class SchedulingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SchedulingClient:
    def __init__(self, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        base = base_url or os.environ.get("CATALOG_SERVICE_URL", DEFAULT_URL)
        self._url = base.rstrip("/") + "/scheduling"
        self._transport = transport  # los tests inyectan uno falso; en producción, la red

    async def _post(self, operation: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(f"{self._url}/{operation}", json=body)
        except httpx.HTTPError as exc:
            raise SchedulingError("UPSTREAM_UNAVAILABLE", f"La agenda no respondió ({type(exc).__name__}).") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.is_success:
            return payload
        raise SchedulingError(payload.get("error_code", "UPSTREAM_UNAVAILABLE"),
                              payload.get("message", f"La agenda respondió {response.status_code}."))

    async def assign_advisor(self, lead_id: str, zone: str | None = None, language: str = "es",
                             registered_only: bool = False) -> dict[str, Any]:
        """El asesor del lead: el que ya tenía o uno nuevo, que queda escrito en `leads.advisor_id`."""
        body: dict[str, Any] = {"lead_id": lead_id, "language": language, "registered_only": registered_only}
        if zone is not None:
            body["zone"] = zone
        return await self._post("assign_advisor", body)


@lru_cache(maxsize=1)
def get_scheduling() -> SchedulingClient:
    """El cliente de la agenda del proceso; los tests lo reemplazan con `monkeypatch`."""
    return SchedulingClient()
