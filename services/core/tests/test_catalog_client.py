"""Tests de A-3: el cliente MCP descubre las tools y sirve igual contra cualquier catálogo.

Levantan servidores MCP reales (streamable HTTP, uvicorn en un hilo). Hay dos servidores distintos
entre sí, y el mismo `CatalogClient` los habla sin ningún cambio de código.
"""
import asyncio
import json
import socket
import threading
import time
from typing import Any

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent

from app.catalog_client import CatalogClient, CatalogToolError, ToolSpec, UnknownTool


class CountingMCP(FastMCP):
    """FastMCP que anota qué tools le llaman los clientes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tool_calls: list[str] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.tool_calls.append(name)
        return await super().call_tool(name, arguments)


def _serve(mcp: FastMCP):
    """Publica `mcp` en un puerto libre; devuelve (url, detener)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "el servidor MCP no arrancó"

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=5)

    return f"http://127.0.0.1:{port}", stop


def _mcp(name: str) -> CountingMCP:
    return CountingMCP(name, transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


def _ok(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))], structuredContent=payload)


def build_catalog_like() -> CountingMCP:
    """Un servidor con las tools de catálogo de contracts/mcp (lo que publica el catalog)."""
    mcp = _mcp("catalog")

    @mcp.tool()
    def search_properties(zone: str | None = None) -> Any:
        """Busca propiedades por filtros opcionales."""
        return _ok({"properties": [{"title": "Casa en " + (zone or "cualquier zona")}]})

    @mcp.tool()
    def get_property(property_id: str, lead_qualified: bool) -> Any:
        """Ficha completa de una propiedad."""
        if property_id == "missing":
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(
                {"code": "NOT_FOUND", "message": "No existe esa propiedad."}))], isError=True)
        return _ok({"id": property_id, "qualified": lead_qualified})

    return mcp


def build_other_catalog() -> CountingMCP:
    """Otro servidor con otras tools y otras descripciones: representa un catálogo distinto."""
    mcp = _mcp("otro-catalogo")

    @mcp.tool()
    def get_property(property_id: str, lead_qualified: bool) -> Any:
        """Ficha de propiedad (versión del catálogo real)."""
        return _ok({"id": property_id, "source": "otro"})

    @mcp.tool()
    def list_neighborhoods() -> Any:
        """Barrios con oferta."""
        return _ok({"neighborhoods": ["Providencia", "Las Condes"]})

    return mcp


@pytest.fixture
def catalog_like():
    mcp = build_catalog_like()
    url, stop = _serve(mcp)
    yield mcp, url
    stop()


def run(coro):
    return asyncio.run(coro)


def test_discovers_the_tools_the_server_publishes(catalog_like):
    _, url = catalog_like
    tools = run(CatalogClient(url).discover())
    assert sorted(tools) == ["get_property", "search_properties"]
    spec = tools["get_property"]
    assert isinstance(spec, ToolSpec) and spec.description == "Ficha completa de una propiedad."
    assert spec.input_schema["required"] == ["property_id", "lead_qualified"]


def test_calls_a_discovered_tool_and_returns_its_structured_output(catalog_like):
    _, url = catalog_like
    assert run(CatalogClient(url).call("search_properties", {"zone": "Providencia"})) == {
        "properties": [{"title": "Casa en Providencia"}]}


def test_the_same_client_serves_a_different_catalog_without_code_changes(catalog_like):
    _, url = catalog_like
    other_mcp = build_other_catalog()
    other_url, stop = _serve(other_mcp)
    try:
        first, second = CatalogClient(url), CatalogClient(other_url)
        assert sorted(run(first.discover())) == ["get_property", "search_properties"]
        assert sorted(run(second.discover())) == ["get_property", "list_neighborhoods"]
        assert run(second.call("list_neighborhoods")) == {"neighborhoods": ["Providencia", "Las Condes"]}
        assert run(second.call("get_property", {"property_id": "p1", "lead_qualified": False}))["source"] == "otro"
    finally:
        stop()


def test_discovery_is_cached_until_a_refresh_is_asked(catalog_like):
    mcp, url = catalog_like
    client = CatalogClient(url)
    run(client.call("search_properties", {}))

    @mcp.tool()
    def get_promotions() -> Any:
        """Promociones vigentes."""
        return _ok({"promotions": []})

    assert "get_promotions" not in run(client.discover())
    assert "get_promotions" in run(client.discover(refresh=True))


def test_an_unknown_tool_is_rejected_without_calling_the_server(catalog_like):
    mcp, url = catalog_like
    with pytest.raises(UnknownTool) as exc:
        run(CatalogClient(url).call("borrar_todo", {}))
    assert exc.value.tool == "borrar_todo" and "search_properties" in exc.value.available
    assert mcp.tool_calls == []


def test_a_tool_published_after_startup_is_picked_up(catalog_like):
    mcp, url = catalog_like
    client = CatalogClient(url)
    run(client.discover())

    @mcp.tool()
    def get_promotions() -> Any:
        """Promociones vigentes."""
        return _ok({"promotions": []})

    assert run(client.call("get_promotions")) == {"promotions": []}
    assert "get_promotions" in run(client.discover())


def test_a_tool_error_carries_the_catalog_error_body(catalog_like):
    _, url = catalog_like
    with pytest.raises(CatalogToolError) as exc:
        run(CatalogClient(url).call("get_property", {"property_id": "missing", "lead_qualified": False}))
    assert exc.value.error == {"code": "NOT_FOUND", "message": "No existe esa propiedad."}


def test_tool_specs_are_ready_for_an_llm(catalog_like):
    _, url = catalog_like
    specs = run(CatalogClient(url).tool_specs())
    assert {s["name"] for s in specs} == {"get_property", "search_properties"}
    assert all(set(s) == {"name", "description", "input_schema"} and s["description"] for s in specs)


def test_url_comes_from_the_environment_when_not_given(monkeypatch, catalog_like):
    _, url = catalog_like
    monkeypatch.setenv("CATALOG_SERVICE_URL", url + "/")
    assert "search_properties" in run(CatalogClient().discover())


def test_an_unreachable_catalog_fails_instead_of_returning_an_empty_list():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(Exception):  # noqa: B017 - el tipo lo decide el transporte
        run(CatalogClient(f"http://127.0.0.1:{port}").discover())
