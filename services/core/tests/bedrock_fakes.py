"""Dobles de prueba de Bedrock (A-2): un cliente con respuestas guiadas y constructores de respuestas Converse."""
import json
from typing import Any


class ScriptedBedrock:
    """Cliente de `bedrock-runtime` falso: devuelve las respuestas en orden y guarda cada petición."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        item = self.responses.pop(0)  # una llamada de más hace fallar la prueba con IndexError
        if isinstance(item, Exception):
            raise item
        return item(request) if callable(item) else item

    def calls_to(self, model_id: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["modelId"] == model_id]


def _response(content: list[dict[str, Any]], stop: str) -> dict[str, Any]:
    return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": stop,
            "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}, "metrics": {"latencyMs": 1}}


def text(value: str) -> dict[str, Any]:
    return _response([{"text": value}], "end_turn")


def tool_use(name: str, arguments: dict[str, Any], tool_use_id: str = "tool-1") -> dict[str, Any]:
    return _response([{"toolUse": {"toolUseId": tool_use_id, "name": name, "input": arguments}}], "tool_use")


def classification(intent: str, **signal: Any) -> dict[str, Any]:
    body = {"intent": intent, "budget_hint": None, "decision_maker": "no_declarado", "urgency": "no_declarada"}
    return text(json.dumps(body | signal))


class FakeCatalog:
    """Catálogo falso con las tools de contracts/mcp; guarda las llamadas que recibe."""

    SPECS = [
        {"name": "search_properties", "description": "Busca propiedades.",
         "input_schema": {"type": "object", "properties": {"zone": {"type": "string"}}}},
        {"name": "get_property", "description": "Ficha de una propiedad.",
         "input_schema": {"type": "object", "properties": {"property_id": {"type": "string"},
                                                           "lead_qualified": {"type": "boolean"}},
                          "required": ["property_id", "lead_qualified"]}},
    ]

    def __init__(self, properties: list[dict[str, Any]] | None = None, error: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.properties = properties if properties is not None else [{"id": "p1", "title": "Casa de prueba", "status": "disponible"}]
        self.error = error

    async def tool_specs(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self.SPECS]

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        from app.catalog_client import CatalogToolError

        self.calls.append((tool, dict(arguments or {})))
        if self.error:
            raise CatalogToolError(self.error)
        if tool == "search_properties":
            return {"properties": self.properties}
        if tool == "get_availability":
            return {"property_id": (arguments or {})["property_id"], "status": "disponible",
                    "checked_at": "2026-01-01T00:00:00+00:00"}
        return {"id": (arguments or {}).get("property_id"), "address_approx": "El Poblado"}
