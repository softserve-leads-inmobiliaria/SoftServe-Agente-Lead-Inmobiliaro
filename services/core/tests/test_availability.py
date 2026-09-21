"""A-4: la disponibilidad se revalida en código tras cualquier búsqueda o ficha."""
import asyncio
from typing import Any

import pytest
from bedrock_fakes import ScriptedBedrock, classification, text, tool_use

from app.agent import BedrockAgent, TurnInput
from app.availability import UNVERIFIED, AvailabilityGuard
from app.bedrock_llm import BedrockLLM
from app.catalog_client import CatalogToolError
from app.rate_limit import RateLimits


class StaleCatalog:
    """Catálogo cuyo listado dice `disponible` para todo, pero cuya disponibilidad vigente es otra."""

    def __init__(self, current: dict[str, str | Exception]) -> None:
        self.current = current
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def tool_specs(self) -> list[dict[str, Any]]:
        return [{"name": "search_properties", "description": "", "input_schema": {"type": "object"}}]

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((tool, dict(arguments or {})))
        if tool == "search_properties":
            return {"properties": [{"id": pid, "title": f"Propiedad {pid}", "price": 1000, "status": "disponible"}
                                   for pid in self.current]}
        if tool == "get_property":
            return {"id": arguments["property_id"], "title": "Ficha", "status": "disponible"}
        if tool == "get_availability":
            value = self.current[arguments["property_id"]]
            if isinstance(value, Exception):
                raise value
            return {"property_id": arguments["property_id"], "status": value, "checked_at": "2026-01-01T00:00:00+00:00"}
        raise AssertionError(tool)


def run(coro):
    return asyncio.run(coro)


def test_search_never_offers_apartada_or_cerrada_as_disponible():
    guard = AvailabilityGuard(StaleCatalog({"a": "disponible", "b": "apartado", "c": "cerrado"}))
    result = run(guard.call("search_properties", {}))
    assert [p["id"] for p in result["properties"]] == ["a"]
    assert all(p["status"] == "disponible" for p in result["properties"])
    assert {p["id"]: p["status"] for p in result["unavailable"]} == {"b": "apartado", "c": "cerrado"}


def test_search_keeps_the_time_the_status_was_checked():
    result = run(AvailabilityGuard(StaleCatalog({"a": "disponible"})).call("search_properties", {}))
    assert result["properties"][0]["availability_checked_at"] == "2026-01-01T00:00:00+00:00"


def test_search_with_no_matches_makes_no_availability_calls():
    catalog = StaleCatalog({})
    result = run(AvailabilityGuard(catalog).call("search_properties", {}))
    assert result["properties"] == [] and result["unavailable"] == []
    assert [tool for tool, _ in catalog.calls] == ["search_properties"]


def test_get_property_replaces_a_stale_status_with_the_current_one():
    result = run(AvailabilityGuard(StaleCatalog({"a": "apartado"})).call("get_property", {"property_id": "a"}))
    assert result["status"] == "apartado"
    assert result["availability_checked_at"] == "2026-01-01T00:00:00+00:00"


def test_fails_closed_when_availability_cannot_be_verified():
    error = CatalogToolError({"code": "UPSTREAM_ERROR", "message": "caído"})
    guard = AvailabilityGuard(StaleCatalog({"a": error, "b": "disponible"}))
    result = run(guard.call("search_properties", {}))
    assert [p["id"] for p in result["properties"]] == ["b"]
    assert result["unavailable"] == [{"id": "a", "title": "Propiedad a", "status": UNVERIFIED}]
    ficha = run(AvailabilityGuard(StaleCatalog({"a": error})).call("get_property", {"property_id": "a"}))
    assert ficha["status"] == UNVERIFIED  # nunca se deja el "disponible" viejo


def test_other_tools_pass_through_untouched():
    class Plain:
        async def tool_specs(self):
            return []

        async def call(self, tool, arguments=None):
            return {"promotions": []}

    assert run(AvailabilityGuard(Plain()).call("get_promotions", {})) == {"promotions": []}


def test_the_agent_and_the_stored_results_only_see_revalidated_data():
    """De punta a punta: el LLM recibe el resultado ya filtrado y `tool_results` (A-5) guarda lo mismo."""
    client = ScriptedBedrock(classification("busqueda_propiedad"), tool_use("search_properties", {}),
                             text("Tengo una opción."))
    agent = BedrockAgent(BedrockLLM(client, "chat", "clf", RateLimits(10, 60.0)))
    guard = AvailabilityGuard(StaleCatalog({"a": "disponible", "b": "apartado"}))
    result = run(agent.run(TurnInput(event={}, text="busco casa", history=[], lead_qualified=False), guard))

    stored = result.tool_results["search_properties"]
    assert [p["id"] for p in stored["properties"]] == ["a"]
    sent_back = client.calls_to("chat")[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert sent_back == stored
    assert "apartado" not in str(stored["properties"])


@pytest.mark.parametrize("status", ["apartado", "cerrado"])
def test_search_scoring_signal_counts_only_available_matches(status):
    guard = AvailabilityGuard(StaleCatalog({"a": status}))
    result = run(guard.call("search_properties", {}))
    assert result["properties"] == []  # matching_properties (A-9) sale de aquí: 0, no 1
