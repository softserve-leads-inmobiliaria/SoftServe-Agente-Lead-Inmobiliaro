"""Revalidación de disponibilidad (A-4): un guardrail de código, no de prompt.

`AvailabilityGuard` envuelve al catálogo. Tras `search_properties` y `get_property` consulta
`get_availability` y sustituye el `status` por el que responde en ese momento. El agente, sea el
falso o el LLM real, solo ve resultados ya revalidados, y esos mismos son los que se guardan en
`last_tool_results` (base de la factualidad, A-5).

Falla cerrado: si la disponibilidad de una propiedad no se puede verificar, no se ofrece como
disponible.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.catalog_client import CatalogToolError, UnknownTool

AVAILABLE = "disponible"
UNVERIFIED = "sin_verificar"
_GUARDED = ("search_properties", "get_property")


class AvailabilityGuard:
    """Mismo protocolo que el catálogo (`call`, `tool_specs`), con la disponibilidad revalidada."""

    def __init__(self, catalog: Any) -> None:
        self._catalog = catalog

    async def tool_specs(self) -> list[dict[str, Any]]:
        return await self._catalog.tool_specs()

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = await self._catalog.call(tool, arguments)
        if tool == "search_properties":
            return await self._search(result)
        if tool == "get_property":
            return await self._property(result)
        return result

    async def _status(self, property_id: str) -> tuple[str, str | None]:
        """Estado vigente y momento de la verificación; `sin_verificar` si el catálogo no lo da."""
        try:
            availability = await self._catalog.call("get_availability", {"property_id": property_id})
        except (CatalogToolError, UnknownTool):
            return UNVERIFIED, None
        status = availability.get("status")
        return (status, availability.get("checked_at")) if isinstance(status, str) else (UNVERIFIED, None)

    async def _search(self, result: dict[str, Any]) -> dict[str, Any]:
        offered, unavailable = await self._revalidate(result.get("properties", []))
        revalidated = {**result, "properties": offered, "unavailable": unavailable}
        if "alternatives" in result:
            # #213: las alternativas de una búsqueda vacía se ofrecen igual, así que se revalidan igual. Las
            # que ya no están disponibles se descartan: el lead no preguntó por ellas.
            alternatives, _ = await self._revalidate(result["alternatives"])
            revalidated["alternatives"] = alternatives
        return revalidated

    async def _revalidate(self, found: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        # Solo lo que el listado da por disponible se revalida: es lo único que puede haberse quedado viejo
        # de forma dañina. Lo demás ya se sabe que no se ofrece, y así se evita una llamada por propiedad.
        current = await asyncio.gather(*(self._status(p.get("id")) if p.get("status") == AVAILABLE else _known(p)
                                         for p in found))
        offered, unavailable = [], []
        for prop, (status, checked_at) in zip(found, current, strict=True):
            if status == AVAILABLE:
                offered.append({**prop, "status": status, "availability_checked_at": checked_at})
            else:
                # Se conserva qué pasó con ella: si el lead pregunta por esa propiedad, se le dice la verdad.
                unavailable.append({"id": prop.get("id"), "title": prop.get("title"), "status": status})
        return offered, unavailable

    async def _property(self, result: dict[str, Any]) -> dict[str, Any]:
        status, checked_at = await self._status(result.get("id"))
        return {**result, "status": status, "availability_checked_at": checked_at}


async def _known(prop: dict[str, Any]) -> tuple[str, None]:
    status = prop.get("status")
    return (status if isinstance(status, str) and status != AVAILABLE else UNVERIFIED), None
