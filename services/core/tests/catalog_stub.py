"""Respuestas de catálogo realistas para los tests que pasan por `process_turn` (que revalida disponibilidad, A-4)."""
from typing import Any

PROPERTY = {"id": "prop_test", "title": "Casa de prueba", "status": "disponible"}
CHECKED_AT = "2026-01-01T00:00:00+00:00"


def stub_call(tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if tool == "get_availability":
        return {"property_id": (arguments or {})["property_id"], "status": "disponible", "checked_at": CHECKED_AT}
    if tool == "get_property":
        return dict(PROPERTY) | {"address_approx": "Providencia"}
    return {"properties": [dict(PROPERTY)]}
