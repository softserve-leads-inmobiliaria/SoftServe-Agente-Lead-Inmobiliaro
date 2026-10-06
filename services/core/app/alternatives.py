"""Alternativas para una búsqueda sin resultados (#213, DT-57).

Si `search_properties` no trae ninguna propiedad, se repite una vez sin la zona y, si tampoco trae nada,
sin el tipo de inmueble (con la zona). Lo que salga va en `alternatives`, con el filtro que se quitó en
`alternatives_without`. `properties` sigue vacía: la búsqueda cuenta como demanda no atendida (N-5), puede
volverse alerta (A-18) y no suma al ajuste de la necesidad (A-15).

Lo decide el código, como la agenda y las alertas: en vivo, el LLM no repetía la búsqueda aunque el
prompt se lo pidiera, y volvía a preguntarle la ciudad al lead.
"""
from __future__ import annotations

from typing import Any, Mapping

RELAXED = ("zone", "kind")  # en este orden: primero se suelta la zona, después el tipo de inmueble


class Alternatives:
    """Mismo protocolo que el catálogo (`call`, `tool_specs`); completa las búsquedas vacías."""

    def __init__(self, catalog: Any) -> None:
        self._catalog = catalog

    async def tool_specs(self) -> list[dict[str, Any]]:
        return await self._catalog.tool_specs()

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = await self._catalog.call(tool, arguments)
        if tool != "search_properties" or not isinstance(result, Mapping) or result.get("properties"):
            return result
        for dropped in RELAXED:
            if not (arguments or {}).get(dropped):
                continue
            relaxed = await self._catalog.call(tool, {k: v for k, v in arguments.items() if k != dropped})
            found = relaxed.get("properties") if isinstance(relaxed, Mapping) else None
            if found:
                return {**result, "alternatives": found, "alternatives_without": dropped}
        return result
