"""Bot de Telegram por *long polling* (B-1, DT-4, DT-20).

Corre embebido en el `lifespan` de FastAPI (`initialize` + `start` + `updater.start_polling`), no
con `run_polling()`, que tomaría el control del event loop del servidor. Sin token configurado el
bot no arranca: así la suite, el arnés y CI siguen corriendo sin credenciales.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping

from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool
from telegram import Bot, Update
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, ApplicationBuilder, ContextTypes, MessageHandler, filters

from app.agent import Agent
from app.channels import advisor_registration, canonical, consent, notifications
from app.channels.identity import resolve_identity
from app.channels.notifications import Sender
from app.skeleton import Catalog
from app.turn import Transition, process_turn

logger = logging.getLogger("core.channels.telegram")

# Lo que ve el lead si el turno falla: nunca se le deja sin respuesta.
ERROR_REPLY = "Perdona, tuve un problema para responderte. ¿Me lo puedes repetir en un momento?"

# Espera antes de cada reintento del envío al lead: 3 intentos en total.
SEND_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0)
# Si Telegram pide esperar más que esto, no se reintenta: la respuesta ya llegaría fuera de contexto.
MAX_RETRY_AFTER_SECONDS = 30.0

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
        await send_with_retry(lambda: message.reply_text(reply))
    except Exception:
        logger.exception("no se pudo enviar la respuesta por Telegram")


async def send_with_retry(send: Callable[[], Awaitable[Any]],
                          delays: tuple[float, ...] = SEND_RETRY_DELAYS) -> None:
    """Reintenta el envío solo cuando el mensaje seguro no salió (#161).

    - `TimedOut` (subclase de `NetworkError`) no se reintenta: el mensaje pudo llegar y se duplicaría.
    - `BadRequest` (también subclase de `NetworkError`) y `Forbidden` no se reintentan: no cambian.
    - `NetworkError` (fallo de conexión) se reintenta tras la espera de `delays`.
    - `RetryAfter` se reintenta tras la espera que pide Telegram, hasta `MAX_RETRY_AFTER_SECONDS`.

    Si se agotan los intentos, propaga el último error. El turno ya se guardó y no se repite.
    """
    for attempt in range(len(delays) + 1):
        try:
            await send()
            return
        except (TimedOut, BadRequest):
            raise
        except RetryAfter as error:
            wait = _seconds(error.retry_after)
            if attempt == len(delays) or wait > MAX_RETRY_AFTER_SECONDS:
                raise
        except NetworkError:
            if attempt == len(delays):
                raise
            wait = delays[attempt]
        logger.warning("reintentando el envío por Telegram", extra={"attempt": attempt + 1, "wait": wait})
        await asyncio.sleep(wait)


def _seconds(retry_after: Any) -> float:
    # python-telegram-bot lo da en segundos (int) y en versiones nuevas como `timedelta`.
    return float(retry_after.total_seconds() if hasattr(retry_after, "total_seconds") else retry_after)


async def reply_to(engine: Engine, catalog: Catalog, *, telegram_user_id: str, text: str,
                   deep_link: str | None = None, timestamp: datetime | None = None,
                   agent: Agent | None = None, bot: Sender | None = None) -> str:
    """Un turno completo del canal, sin tipos de Telegram: es el punto de prueba del bot."""
    # B-7: un asesor abriendo su enlace de registro no es un lead; no genera turno ni score.
    token = canonical.advisor_token(deep_link)
    if token is not None:
        advisor = await run_in_threadpool(advisor_registration.register, engine,
                                          access_token=token, chat_id=telegram_user_id)
        return advisor_registration.reply_for(advisor)

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
    async def on_qualified(qualified_event: Mapping[str, Any], transition: Transition) -> None:
        if transition.handoff_reason:
            return  # la ficha del traspaso de este mismo turno ya lleva el score: una sola notificación
        await notifications.notify_advisor(
            engine, qualified_event, transition.score.to_lead_score(str(identity.lead_id)),
            bot if bot is not None else get_bot())

    async def on_handoff(handoff_event: Mapping[str, Any], transition: Transition) -> None:
        await notifications.notify_handoff(
            engine, handoff_event, transition.score.to_lead_score(str(identity.lead_id)),
            transition.after, transition.handoff_reason or "", bot if bot is not None else get_bot())

    outbound = await process_turn(engine, event, catalog, agent, on_qualified, on_handoff)
    reply = canonical.from_canonical(outbound)
    return consent.with_privacy_notice(reply) if first_turn else reply
