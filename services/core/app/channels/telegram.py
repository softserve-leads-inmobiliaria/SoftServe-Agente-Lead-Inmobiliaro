"""Bot de Telegram por *long polling* (B-1, DT-4, DT-20).

Corre embebido en el `lifespan` de FastAPI (`initialize` + `start` + `updater.start_polling`), no
con `run_polling()`, que tomaría el control del event loop del servidor. Sin token configurado el
bot no arranca: así la suite, el arnés y CI siguen corriendo sin credenciales.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool
from telegram import Bot, Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, MessageHandler, filters

from app.agent import Agent
from app.channels import canonical, consent
from app.channels.identity import resolve_identity
from app.skeleton import Catalog
from app.turn import process_turn

logger = logging.getLogger("core.channels.telegram")

# Lo que ve el lead si el turno falla: nunca se le deja sin respuesta.
ERROR_REPLY = "Perdona, tuve un problema para responderte. ¿Me lo puedes repetir en un momento?"

_ENGINE_KEY = "engine"
_CATALOG_KEY = "catalog"
_AGENT_KEY = "agent"

_application: Application | None = None


def _quiet_http_logs() -> None:
    """La URL de la API de Telegram lleva el token del bot: `httpx` la registra a nivel INFO."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def build_application(token: str, *, engine: Engine, catalog: Catalog, agent: Agent | None = None) -> Application:
    # Un solo manejador para todo texto, comandos incluidos: `/stop` (la baja) no puede quedar sin
    # atender. `concurrent_updates` evita que un turno lento (p. ej. esperando cupo del LLM) bloquee
    # a los demás leads; el orden de un mismo lead lo garantiza el bloqueo por conversación.
    application = ApplicationBuilder().token(token).concurrent_updates(True).build()
    application.bot_data[_ENGINE_KEY] = engine
    application.bot_data[_CATALOG_KEY] = catalog
    application.bot_data[_AGENT_KEY] = agent
    application.add_handler(MessageHandler(filters.TEXT, _on_message))
    return application


async def start_bot(*, engine: Engine, catalog: Catalog, agent: Agent | None = None) -> Application | None:
    global _application
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN vacío: el bot de Telegram no arranca")
        return None
    _quiet_http_logs()
    application = build_application(token, engine=engine, catalog=catalog, agent=agent)
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    _application = application
    logger.info("bot de Telegram escuchando por long polling")
    return application


async def stop_bot(application: Application | None = None) -> None:
    global _application
    target = application or _application
    if target is None:
        return
    if target.updater is not None and target.updater.running:
        await target.updater.stop()
    await target.stop()
    await target.shutdown()
    _application = None


def get_bot() -> Bot | None:
    """El `Bot` ya autenticado, para que B-7 no cree un segundo cliente con el mismo token."""
    return _application.bot if _application is not None else None


async def _on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    text, deep_link = canonical.split_command(message.text or "")
    try:
        reply = await reply_to(
            context.application.bot_data[_ENGINE_KEY],
            context.application.bot_data[_CATALOG_KEY],
            telegram_user_id=str(user.id),
            text=text,
            deep_link=deep_link,
            timestamp=message.date,
            agent=context.application.bot_data.get(_AGENT_KEY),
        )
    except Exception:
        # Un turno que falla no puede tumbar el polling ni dejar al lead sin respuesta.
        logger.exception("fallo al procesar un mensaje de Telegram")
        reply = ERROR_REPLY
    try:
        await message.reply_text(reply)
    except Exception:
        logger.exception("no se pudo enviar la respuesta por Telegram")


async def reply_to(engine: Engine, catalog: Catalog, *, telegram_user_id: str, text: str,
                   deep_link: str | None = None, timestamp: datetime | None = None,
                   agent: Agent | None = None) -> str:
    """Un turno completo del canal, sin tipos de Telegram: es el punto de prueba del bot."""
    identity = await run_in_threadpool(resolve_identity, engine, telegram_user_id)

    # B-2: la baja es determinista y corta antes del LLM.
    if consent.is_opt_out(text):
        await run_in_threadpool(consent.withdraw_consent, engine, identity.lead_id)
        return consent.OPT_OUT_CONFIRMATION

    # Se consulta antes del turno: `process_turn` marca actividad en la conversación.
    first_turn = await run_in_threadpool(consent.is_first_turn, engine, identity.conversation_id)

    event: dict[str, Any] = canonical.to_canonical(
        lead_id=identity.lead_id,
        conversation_id=identity.conversation_id,
        text=text,
        timestamp=timestamp,
        **canonical.parse_deep_link(deep_link),
    )
    outbound = await process_turn(engine, event, catalog, agent)
    reply = canonical.from_canonical(outbound)
    return consent.with_privacy_notice(reply) if first_turn else reply
