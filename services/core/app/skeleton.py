"""Esqueleto end-to-end (CT-4): evento inbound -> fake core -> (catálogo por MCP) -> evento outbound.

Sin lógica de negocio real, a propósito: la respuesta sale del fake LLM (CT-7) y, cuando el mensaje
es una búsqueda, se le agrega un dato traído del catalog fake por MCP para probar que los servicios
están de verdad conectados. Cuando exista el core real (A-1...), reemplaza este módulo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from app.fakes.fake_core import FakeCore
from app.fakes.fake_llm import FakeLLM


class Catalog(Protocol):
    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Reply:
    outbound: dict[str, Any]
    intent: str
    tool_results: dict[str, Any]  # payloads de las tools llamadas en este turno, por nombre


async def respond(event: Mapping[str, Any], catalog: Catalog, *, intent: str | None = None,
                  text: str | None = None) -> Reply:
    """`intent` y `text` los fija el guardrail de entrada cuando el mensaje no debe seguir el flujo normal."""
    llm = FakeLLM()
    intent = intent or llm.classify_intent(event["text"])
    outbound = FakeCore(llm).handle_event(event, intent=intent, text=text)
    tool_results: dict[str, Any] = {}
    if intent == "busqueda_propiedad":
        tool_results["search_properties"] = await catalog.call("search_properties", {})
        found = tool_results["search_properties"]["properties"]
        if found:
            outbound["text"] += f" Por ejemplo: {found[0]['title']}."
    return Reply(outbound, intent, tool_results)


async def handle_event(event: Mapping[str, Any], catalog: Catalog) -> dict[str, Any]:
    return (await respond(event, catalog)).outbound
