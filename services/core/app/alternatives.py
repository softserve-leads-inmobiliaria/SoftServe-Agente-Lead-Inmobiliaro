"""Alternativas para una búsqueda sin resultados (#213, DT-57).

Si `search_properties` no trae ninguna propiedad, se repite una vez sin la zona; si tampoco trae nada, sin el
tipo de inmueble (con la zona), y por último sin la operación (`type`: venta, renta), con zona y tipo. Lo que salga va en `alternatives`, con el filtro que se quitó en
`alternatives_without`. `properties` sigue vacía: la búsqueda cuenta como demanda no atendida (N-5), puede
volverse alerta (A-18) y no suma al ajuste de la necesidad (A-15).

Lo decide el código, como la agenda y las alertas: en vivo, el LLM no repetía la búsqueda aunque el
prompt se lo pidiera, y volvía a preguntarle la ciudad al lead.
"""
from __future__ import annotations

from typing import Any, Mapping

# En este orden: la zona, el tipo de inmueble y al final la operación, porque quien renta no compra. Sin la
# operación, "the house in Usaquen" después de buscar rentas contestaba que no había casas en Usaquén, y había
# una en venta (prueba de jurado M4, #247).
RELAXED = ("zone", "kind", "type")
# La operación solo se suelta si el lead nombró el tipo de inmueble: "la casa en Usaquén" en venta es la misma
# casa; "algo en preventa en Laureles" no se contesta con una renta cualquiera de Laureles.
_NEEDS = {"type": "kind"}


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
            needed = _NEEDS.get(dropped, dropped)
            if not (arguments or {}).get(dropped) or not (arguments or {}).get(needed):
                continue
            relaxed = await self._catalog.call(tool, {k: v for k, v in arguments.items() if k != dropped})
            found = relaxed.get("properties") if isinstance(relaxed, Mapping) else None
            if found:
                return {**result, "alternatives": found, "alternatives_without": dropped}
        return result
