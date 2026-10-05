"""Esqueleto end-to-end (CT-4): evento inbound -> fake core -> (catálogo por MCP) -> evento outbound.

Sin lógica de negocio real, a propósito: la respuesta sale del fake LLM (CT-7) y, cuando el mensaje
es una búsqueda, se consulta el catálogo por MCP con el tipo de inmueble que nombró el lead (#188) y
se le agrega un dato de la respuesta, para probar que los servicios están de verdad conectados.
Cuando exista el core real (A-1...), reemplaza este módulo.
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


# Plural para el aviso de "no hay de ese tipo" (#188).
_KIND_PLURAL = {"departamento": "departamentos", "casa": "casas", "terreno": "terrenos",
                "local": "locales", "estudio": "estudios"}


async def respond(event: Mapping[str, Any], catalog: Catalog, *, intent: str | None = None,
                  text: str | None = None) -> Reply:
    """`intent` y `text` los fija el guardrail de entrada cuando el mensaje no debe seguir el flujo normal."""
    llm = FakeLLM()
    intent = intent or llm.classify_intent(event["text"])
    outbound = FakeCore(llm).handle_event(event, intent=intent, text=text)
    tool_results: dict[str, Any] = {}
    if intent == "busqueda_propiedad":
        # El fake solo mapea el tipo de inmueble (#188); la zona y el presupuesto son del LLM real.
        kind = llm.extract_property_kind(event["text"])
        arguments = {"kind": kind} if kind else {}
        tool_results["search_properties"] = await catalog.call("search_properties", arguments)
        found = tool_results["search_properties"]["properties"]
        if found:
            outbound["text"] += f" Por ejemplo: {found[0]['title']}."
        elif kind:
            outbound["text"] = (f"No tengo {_KIND_PLURAL[kind]} disponibles con esos filtros. "
                                + outbound["text"])
    return Reply(outbound, intent, tool_results)


async def handle_event(event: Mapping[str, Any], catalog: Catalog) -> dict[str, Any]:
    return (await respond(event, catalog)).outbound
