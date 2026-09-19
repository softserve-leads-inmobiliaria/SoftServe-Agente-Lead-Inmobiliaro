"""Core fake (CT-4): recibe eventos canónicos por HTTP y responde con el esqueleto end-to-end."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException

from app.catalog_client import CatalogClient
from app.skeleton import handle_event

logger = logging.getLogger("core")
app = FastAPI(title="core (fake)")
catalog = CatalogClient()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/events")
async def post_event(event: dict[str, Any]) -> dict[str, Any]:
    try:
        return await handle_event(event, catalog)
    except ValueError as exc:  # evento incompleto o no inbound
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("fallo al procesar el evento")
        raise HTTPException(status_code=503, detail="catálogo no disponible") from exc
