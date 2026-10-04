"""Servidor MCP del catálogo (C-1b): las 5 tools catalog.* por streamable HTTP en /mcp (DT-2).

Mismo transporte y mismo código cliente que el catálogo falso de CT-8: lo único que cambió al
migrar el catálogo a Postgres (C-1) fue la fuente de datos detrás de `CatalogService`, nunca el
protocolo. Es un servidor MCP real (SDK `mcp`, versión fijada en requirements.txt).
"""
from __future__ import annotations

import json
from typing import Any, Literal

from jsonschema import Draft202012Validator
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.service import CatalogError, CatalogService
from db.repo_root import repo_root
from db.settings import database_url as _database_url

PropertyType = Literal["venta", "renta", "preventa"]

engine = create_engine(_database_url())
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def _error_result(error: CatalogError) -> CallToolResult:
    """Un error viaja como isError=true con el cuerpo de contracts/mcp/catalog_error.schema.json."""
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(error.to_dict()))],
                          isError=True)


def _ok_result(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))],
                          structuredContent=payload)


def _input_schemas() -> dict[str, dict[str, Any]]:
    """El `inputSchema` de cada tool sale de contracts/mcp: el contrato es la única fuente de verdad."""
    schemas = {}
    for path in (repo_root() / "contracts" / "mcp").glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if "inputSchema" in schema:
            schemas[schema["name"]] = schema["inputSchema"]
    return schemas


class _CatalogMCP(FastMCP):
    """Publica y valida los argumentos con el contrato, y traduce los rechazos del SDK a `catalog_error`."""

    _schemas = _input_schemas()
    _validators = {name: Draft202012Validator(schema) for name, schema in _schemas.items()}

    async def list_tools(self) -> list[MCPTool]:
        # El SDK deriva el esquema de la firma de Python, más laxo que el contrato: `lead_profile` saldría
        # como un objeto cualquiera y el LLM no sabría qué campos pide `check_requirements` (#182). Se
        # publica el mismo esquema contra el que se valida.
        tools = await super().list_tools()
        for tool in tools:
            if tool.name in self._schemas:
                tool.inputSchema = self._schemas[tool.name]
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        validator = self._validators.get(name)
        if validator is not None and not validator.is_valid(arguments):
            return _error_result(CatalogError("INVALID_INPUT", "Los argumentos no cumplen el esquema de la tool."))
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            if isinstance(exc.__cause__, ValidationError):
                return _error_result(CatalogError("INVALID_INPUT", "Los argumentos no cumplen el esquema de la tool."))
            return _error_result(CatalogError("UPSTREAM_UNAVAILABLE", "El catálogo no pudo procesar la solicitud."))


def create_mcp(service: CatalogService | None = None) -> FastMCP:
    # Inyectamos la fábrica de sesiones obligatoria que exige CatalogService
    service = service or CatalogService(db_session_factory=SessionLocal)
    mcp = _CatalogMCP(
        "catalog",
        streamable_http_path="/mcp",
        # Los servicios se hablan por la red interna de Docker (Host: catalog:8000): la protección
        # contra DNS rebinding solo acepta localhost, así que se desactiva aquí.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    def run(call: Any, **kwargs: Any) -> Any:
        try:
            return _ok_result(call(**kwargs))
        except CatalogError as error:
            return _error_result(error)

    @mcp.tool()
    def search_properties(zone: str | None = None, budget_range: dict[str, float] | None = None,
                          type: PropertyType | None = None, bedrooms: int | None = None) -> Any:
        """Busca propiedades por filtros opcionales. Sin coincidencias devuelve properties: []."""
        return run(service.search_properties, zone=zone, budget_range=budget_range, type=type,
                   bedrooms=bedrooms)

    @mcp.tool()
    def get_property(property_id: str, lead_qualified: bool) -> Any:
        """Ficha completa. address_exact solo viene con lead_qualified=true."""
        return run(service.get_property, property_id=property_id, lead_qualified=lead_qualified)

    @mcp.tool()
    def get_availability(property_id: str) -> Any:
        """Estado de disponibilidad de una propiedad en este momento."""
        return run(service.get_availability, property_id=property_id)

    @mcp.tool()
    def get_promotions(property_id: str | None = None) -> Any:
        """Promociones vigentes, opcionalmente de una propiedad."""
        return run(service.get_promotions, property_id=property_id)

    @mcp.tool()
    def check_requirements(property_id: str, lead_profile: dict[str, Any]) -> Any:
        """Verifica si el lead cumple los requisitos de compra o renta de una propiedad: enganche mínimo
        (venta y preventa), ingreso mensual mínimo (renta) y referencias (renta). Úsala cuando el lead ya
        dijo cuánto tiene de enganche o cuánto gana al mes. Los tres campos de lead_profile van siempre,
        en pesos; si uno no aplica a esa operación o el lead no lo dijo, 0 o false."""
        return run(service.check_requirements, property_id=property_id, lead_profile=lead_profile)

    return mcp
