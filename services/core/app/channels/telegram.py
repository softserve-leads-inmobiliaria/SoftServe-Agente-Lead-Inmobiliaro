"""Bot de Telegram por *long polling* (B-1, DT-4).

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
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.channels import canonical, consent
from app.channels.identity import resolve_identity
from app.skeleton import Catalog
from app.turn import process_turn

logger = logging.getLogger("core.channels.telegram")

_ENGINE_KEY = "engine"
_CATALOG_KEY = "catalog"

_application: Application | None = None


def build_application(token: str, *, engine: Engine, catalog: Catalog) -> Application:
    application = ApplicationBuilder().token(token).build()
    application.bot_data[_ENGINE_KEY] = engine
    application.bot_data[_CATALOG_KEY] = catalog
    application.add_handler(CommandHandler("start", _on_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    return application


async def start_bot(*, engine: Engine, catalog: Catalog) -> Application | None:
    global _application
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN vacío: el bot de Telegram no arranca")
        return None
    application = build_application(token, engine=engine, catalog=catalog)
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


async def _on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _dispatch(update, context, deep_link=context.args[0] if context.args else None)


async def _on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _dispatch(update, context, deep_link=None)


async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE, *,
                    deep_link: str | None) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    try:
        reply = await reply_to(
            context.application.bot_data[_ENGINE_KEY],
            context.application.bot_data[_CATALOG_KEY],
            telegram_user_id=str(user.id),
            text=message.text or "",
            deep_link=deep_link,
            timestamp=message.date,
        )
    except Exception:
        # Un turno que falla no puede tumbar el polling del bot.
        logger.exception("fallo al procesar un mensaje de Telegram")
        return
    await message.reply_text(reply)


async def reply_to(engine: Engine, catalog: Catalog, *, telegram_user_id: str, text: str,
                   deep_link: str | None = None, timestamp: datetime | None = None) -> str:
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
    outbound = await process_turn(engine, event, catalog)
    reply = canonical.from_canonical(outbound)
    return consent.with_privacy_notice(reply) if first_turn else reply
