"""Core fake (CT-4): recibe eventos canónicos por HTTP y responde con el esqueleto end-to-end.

El bot de Telegram (B-1) corre embebido en el `lifespan` de esta misma app y llama a `process_turn`
en proceso; `POST /events` se mantiene para el arnés E2E, que habla por HTTP.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from app import scheduler, tracing
from app.catalog_client import CatalogClient
from app.channels.telegram import get_bot, start_bot, stop_bot
from app.llm_config import build_agent
from app.turn import process_turn
from db.logging_config import configure_json_logging
from db.session import get_engine

configure_json_logging()
logger = logging.getLogger("core")
tracing.configure()  # sin OTEL_EXPORTER_OTLP_ENDPOINT queda apagado
catalog = CatalogClient()
agent = build_agent()  # LLM_PROVIDER=fake (por defecto) o bedrock; una config inválida falla al arrancar


@asynccontextmanager
async def lifespan(app: FastAPI):
    application = await start_bot(engine=get_engine(), catalog=catalog, agent=agent)
    # A-17 (DT-3, DT-44): el scheduler corre en este mismo proceso y envía por el bot de arriba.
    every = scheduler.poll_seconds()
    task = None
    if every > 0:
        channel = scheduler.TelegramChannel(asyncio.get_running_loop(), get_bot)
        task = asyncio.create_task(scheduler.run_forever(scheduler.Scheduler(get_engine(), channel), every))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
        await stop_bot(application)


app = FastAPI(title="core (fake)", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/events")
async def post_event(event: dict[str, Any]) -> dict[str, Any]:
    try:
        return await process_turn(get_engine(), event, catalog, agent)
    except ValueError as exc:  # evento incompleto o no inbound
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("fallo al procesar el evento")
        raise HTTPException(status_code=503, detail="dependencia no disponible (catálogo o base de datos)") from exc
