"""Cliente MCP del catálogo: `core` habla con `catalog` por streamable HTTP en /mcp (DT-2).

Las tools se descubren con `tools/list` en la primera conexión y se guardan en memoria: el cliente
no conoce nombres de tools ni esquemas. Por eso es el mismo código contra el catálogo falso (CT-8) y
contra el real (C-1): cambia solo `CATALOG_SERVICE_URL`, nunca una rama de código (A-3).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_URL = "http://localhost:8001"

T = TypeVar("T")


class CatalogToolError(Exception):
    """La tool respondió con isError=true; `error` es el cuerpo de catalog_error.schema.json."""

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error.get("message", "error del catálogo"))
        self.error = error


class UnknownTool(Exception):
    """La tool pedida no la publica el catálogo; se rechaza sin hacer la llamada."""

    def __init__(self, tool: str, available: list[str]) -> None:
        super().__init__(f"El catálogo no publica la tool '{tool}' (disponibles: {', '.join(available)})")
        self.tool = tool
        self.available = available


@dataclass(frozen=True)
class ToolSpec:
    """Una tool tal como la publica el catálogo."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


class CatalogClient:
    def __init__(self, base_url: str | None = None) -> None:
        base = base_url or os.environ.get("CATALOG_SERVICE_URL", DEFAULT_URL)
        self._url = base.rstrip("/") + "/mcp"
        self._tools: dict[str, ToolSpec] | None = None

    async def _run(self, action: Callable[[ClientSession], Awaitable[T]], refresh: bool = False) -> T:
        """Abre una sesión, descubre las tools si hace falta y ejecuta `action` en la misma conexión."""
        async with streamable_http_client(self._url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                if refresh or self._tools is None:
                    self._tools = {t.name: ToolSpec(t.name, t.description or "", t.inputSchema, t.outputSchema)
                                   for t in (await session.list_tools()).tools}
                return await action(session)

    async def discover(self, refresh: bool = False) -> dict[str, ToolSpec]:
        """Las tools que publica el catálogo, por nombre. Con `refresh` vuelve a preguntarle."""
        if refresh or self._tools is None:
            async def nothing(_: ClientSession) -> None:
                return None

            await self._run(nothing, refresh=True)
        return dict(self._tools or {})

    async def tool_specs(self) -> list[dict[str, Any]]:
        """Las tools descubiertas en el formato que espera un LLM con tool use (A-2, A-4)."""
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in (await self.discover()).values()]

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        async def invoke(session: ClientSession) -> Any:
            # Sin lanzar aquí dentro: la sesión MCP corre en un grupo de tareas que envuelve excepciones.
            if tool not in (self._tools or {}):
                return None
            return await session.call_tool(tool, arguments or {})

        result = await self._run(invoke)
        if result is None:
            # Puede ser un caché viejo (el catálogo publicó una tool después): se vuelve a descubrir una vez.
            result = await self._run(invoke, refresh=True)
        if result is None:
            raise UnknownTool(tool, sorted(self._tools or {}))
        if result.isError:
            raise CatalogToolError(json.loads(result.content[0].text))
        return result.structuredContent
