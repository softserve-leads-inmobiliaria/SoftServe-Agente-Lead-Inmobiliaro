"""Servidor MCP del catálogo falso (CT-8): las 5 tools catalog.* por streamable HTTP en /mcp (DT-2).

Es un servidor MCP real (SDK `mcp`, versión fijada en requirements.txt), no un mock genérico: el
cliente de `core` se conecta con el mismo protocolo que usará contra el catálogo real.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from jsonschema import Draft202012Validator
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.repo_root import repo_root
from db.settings import database_url as _database_url

from .service import CatalogError, CatalogService

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


def _input_validators() -> dict[str, Draft202012Validator]:
    """El `inputSchema` de cada tool sale de contracts/mcp: el contrato es la única fuente de verdad."""
    validators = {}
    for path in (repo_root() / "contracts" / "mcp").glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if "inputSchema" in schema:
            validators[schema["name"]] = Draft202012Validator(schema["inputSchema"])
    return validators


class _CatalogMCP(FastMCP):
    """Valida los argumentos contra el contrato y traduce los rechazos del SDK a `catalog_error`."""

    _validators = _input_validators()

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
        """Verifica requisitos de compra/renta contra el perfil declarado del lead."""
        return run(service.check_requirements, property_id=property_id, lead_profile=lead_profile)

    return mcp
