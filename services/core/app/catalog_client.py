"""Cliente MCP del catálogo (CT-4): `core` habla con `catalog` por streamable HTTP en /mcp (DT-2)."""
from __future__ import annotations

import json
import os
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_URL = "http://localhost:8001"


class CatalogToolError(Exception):
    """La tool respondió con isError=true; `error` es el cuerpo de catalog_error.schema.json."""

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error.get("message", "error del catálogo"))
        self.error = error


class CatalogClient:
    def __init__(self, base_url: str | None = None) -> None:
        base = base_url or os.environ.get("CATALOG_SERVICE_URL", DEFAULT_URL)
        self._url = base.rstrip("/") + "/mcp"

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        async with streamable_http_client(self._url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments or {})
        if result.isError:
            raise CatalogToolError(json.loads(result.content[0].text))
        return result.structuredContent
