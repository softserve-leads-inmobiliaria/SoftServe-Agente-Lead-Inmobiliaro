"""Esqueleto end-to-end (CT-4): evento inbound -> fake core -> (catálogo por MCP) -> evento outbound.

Sin lógica de negocio real, a propósito: la respuesta sale del fake LLM (CT-7) y, cuando el mensaje
es una búsqueda, se le agrega un dato traído del catalog fake por MCP para probar que los servicios
están de verdad conectados. Cuando exista el core real (A-1...), reemplaza este módulo.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol

from app.fakes.fake_core import FakeCore
from app.fakes.fake_llm import FakeLLM


class Catalog(Protocol):
    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...


async def handle_event(event: Mapping[str, Any], catalog: Catalog) -> dict[str, Any]:
    llm = FakeLLM()
    outbound = FakeCore(llm).handle_event(event)
    if llm.classify_intent(event["text"]) == "busqueda_propiedad":
        found = (await catalog.call("search_properties", {}))["properties"]
        if found:
            outbound["text"] += f" Por ejemplo: {found[0]['title']}."
    return outbound
