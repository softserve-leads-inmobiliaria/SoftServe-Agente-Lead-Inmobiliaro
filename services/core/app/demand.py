"""Demanda insatisfecha (N-5): qué buscan los leads que no encuentran nada.

`SearchLog` envuelve al catálogo durante un turno y anota los filtros de cada `search_properties` que
el catálogo contestó sin resultados. Solo los filtros: ni el lead ni su texto. El turno los guarda en
`unmet_searches`, en la misma transacción de `advance`.
"""
from __future__ import annotations

from typing import Any, Mapping

from db.models import PROPERTY_KINDS, PROPERTY_TYPES

UnmetSearch = dict[str, Any]  # zone, type, kind, bedrooms, budget_min, budget_max; None si no lo filtró


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value


def unmet_filters(arguments: Mapping[str, Any] | None) -> UnmetSearch:
    """Los filtros de la búsqueda, tal como se guardan. Lo que no pasaría las restricciones de la tabla se
    deja en None: un argumento raro del LLM no puede tumbar el turno."""
    arguments = arguments or {}
    zone = arguments.get("zone")
    property_type = arguments.get("type")  # la operación (venta/renta/preventa); `kind` es el inmueble
    kind = arguments.get("kind")
    bedrooms = arguments.get("bedrooms")
    budget = arguments.get("budget_range")
    budget = budget if isinstance(budget, Mapping) else {}
    return {
        "zone": zone.strip() if isinstance(zone, str) and zone.strip() else None,
        "type": property_type if property_type in PROPERTY_TYPES else None,
        "kind": kind if kind in PROPERTY_KINDS else None,  # #209, DT-54
        "bedrooms": bedrooms if isinstance(bedrooms, int) and not isinstance(bedrooms, bool) and bedrooms >= 0
        else None,
        "budget_min": _number(budget.get("min")),
        "budget_max": _number(budget.get("max")),
    }


class SearchLog:
    """Mismo protocolo que el catálogo (`call`, `tool_specs`); anota las búsquedas sin resultados.

    Va por debajo del guardrail de disponibilidad: cuenta lo que contestó el catálogo, antes de revalidar.
    La misma búsqueda repetida en el turno se anota una vez.
    """

    def __init__(self, catalog: Any) -> None:
        self._catalog = catalog
        self._unmet: dict[tuple[Any, ...], UnmetSearch] = {}
        self._first: tuple[UnmetSearch, int] | None = None

    async def tool_specs(self) -> list[dict[str, Any]]:
        return await self._catalog.tool_specs()

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = await self._catalog.call(tool, arguments)
        if tool == "search_properties" and isinstance(result, Mapping):
            found = result.get("properties")
            filters = unmet_filters(arguments)
            if self._first is None:
                self._first = (filters, len(found) if isinstance(found, list) else 0)
            if not found:
                self._unmet.setdefault(tuple(filters.values()), filters)
        return result

    @property
    def unmet(self) -> list[UnmetSearch]:
        return list(self._unmet.values())

    @property
    def asked_search(self) -> tuple[UnmetSearch, int] | None:
        """Los filtros de la primera búsqueda del turno y cuántas propiedades trajo (A-18), o None.

        Es la que pidió el lead: si no trae nada, el agente repite la búsqueda con un filtro menos para
        ofrecer alternativas (#213), y la alerta tiene que ser de lo que el lead buscaba.
        """
        return self._first
