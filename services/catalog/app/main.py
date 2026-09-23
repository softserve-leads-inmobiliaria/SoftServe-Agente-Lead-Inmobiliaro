"""Catalog Service: FastAPI con /health y el servidor MCP montado en /mcp (DT-2), sobre Postgres (C-1)."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.server import create_mcp
from db.logging_config import configure_json_logging

configure_json_logging()

mcp = create_mcp()
mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # El transporte streamable HTTP necesita su gestor de sesiones corriendo mientras vive la app.
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="catalog", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Se monta al final: /health se resuelve antes y todo lo demás (incluido /mcp) cae en el servidor MCP.
app.mount("/", mcp_app)
