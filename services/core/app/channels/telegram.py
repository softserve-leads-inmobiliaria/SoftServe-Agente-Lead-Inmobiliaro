"""Bot de Telegram por *long polling* (B-1, DT-4, DT-20).

Corre embebido en el `lifespan` de FastAPI (`initialize` + `start` + `updater.start_polling`), no
con `run_polling()`, que tomaría el control del event loop del servidor. Sin token configurado el
bot no arranca: así la suite, el arnés y CI siguen corriendo sin credenciales.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping

from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.agent import Agent
from app.channels import advisor_registration, canonical, consent, formatting, notifications
from app.channels.identity import resolve_identity
from app.channels.notifications import Sender
from app.skeleton import Catalog
from app.turn import Transition, TurnReply, process_turn_reply

logger = logging.getLogger("core.channels.telegram")

# Lo que ve el lead si el turno falla: nunca se le deja sin respuesta.
ERROR_REPLY = "Perdona, tuve un problema para responderte. ¿Me lo puedes repetir en un momento?"

# Espera antes de cada reintento del envío al lead: 3 intentos en total.
SEND_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0)
# Si Telegram pide esperar más que esto, no se reintenta: la respuesta ya llegaría fuera de contexto.
MAX_RETRY_AFTER_SECONDS = 30.0
# Tope de Telegram para el texto que acompaña una foto (en unidades UTF-16); un mensaje admite 4096.
CAPTION_MAX_CHARS = 1024

# B-11 (DT-51): cada botón se traduce a un mensaje del lead con texto fijo, que sigue el flujo normal
# (guardrail de entrada, FSM y score) igual que si lo hubiera escrito. En inglés, como todo lo que lee el
# lead (#202): un texto en español lo frenaría el filtro de idioma. El `callback_data` no cambia.
VISIT_BUTTON, ADVISOR_BUTTON = "visita", "asesor"
BUTTON_LABELS: Mapping[str, str] = {VISIT_BUTTON: "Schedule a visit", ADVISOR_BUTTON: "Talk to an agent"}
BUTTON_TEXTS: Mapping[str, str] = {VISIT_BUTTON: "I'd like to schedule a visit",
                                   ADVISOR_BUTTON: "I want to talk to an agent"}
_BUTTON_PREFIX = "lead:"

_ENGINE_KEY = "engine"
_CATALOG_KEY = "catalog"
_AGENT_KEY = "agent"

_application: Application | None = None


@dataclass(frozen=True)
class ChannelReply:
    """Lo que el canal le manda al lead: el texto que ya pasó los guardrails, la foto de la propiedad del
    turno si la tiene (B-10) y los botones que van debajo (B-11)."""
    text: str
    photo_url: str | None = None
    buttons: tuple[str, ...] = ()


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
    application.add_handler(CallbackQueryHandler(
        _on_button, pattern=rf"^{_BUTTON_PREFIX}({VISIT_BUTTON}|{ADVISOR_BUTTON})$"))
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
    await _answer(context, message, str(user.id), text, deep_link=deep_link, timestamp=message.date)


async def _on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Un botón de B-11: se contesta como si el lead hubiera escrito su texto fijo."""
    query = update.callback_query
    user, chat = update.effective_user, update.effective_chat
    if query is None or user is None or chat is None:
        return
    text = BUTTON_TEXTS.get((query.data or "").removeprefix(_BUTTON_PREFIX))
    try:
        await query.answer()  # quita el "cargando" del botón en el cliente
        # Sin el teclado, un segundo toque no vuelve a pedir lo mismo.
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        logger.warning("no se pudo cerrar el botón de Telegram", exc_info=True)
    if text is None:
        return
    # Se responde en el chat y no sobre el mensaje del botón: Telegram puede ya no entregarlo.
    await _answer(context, _ChatReplier(chat), str(user.id), text)


async def _answer(context: ContextTypes.DEFAULT_TYPE, target: Any, telegram_user_id: str, text: str, *,
                  deep_link: str | None = None, timestamp: datetime | None = None) -> None:
    try:
        reply = await reply_turn(
            context.application.bot_data[_ENGINE_KEY],
            context.application.bot_data[_CATALOG_KEY],
            telegram_user_id=telegram_user_id,
            text=text,
            deep_link=deep_link,
            timestamp=timestamp,
            agent=context.application.bot_data.get(_AGENT_KEY),
        )
    except Exception:
        # Un turno que falla no puede tumbar el polling ni dejar al lead sin respuesta.
        logger.exception("fallo al procesar un mensaje de Telegram")
        reply = ChannelReply(ERROR_REPLY)
    try:
        await send_reply(target, reply)
    except Exception:
        logger.exception("no se pudo enviar la respuesta por Telegram")


class _ChatReplier:
    """`reply_text`/`reply_photo` sobre un chat, con la misma forma que un `Message`."""

    def __init__(self, chat: Any) -> None:
        self._chat = chat

    async def reply_text(self, text: str, **kwargs: Any) -> Any:
        return await self._chat.send_message(text, **kwargs)

    async def reply_photo(self, photo: str, **kwargs: Any) -> Any:
        return await self._chat.send_photo(photo, **kwargs)


async def send_reply(message: Any, reply: ChannelReply) -> None:
    """Manda la respuesta al lead; con la foto de la propiedad como cabecera si la hay (B-10, DT-50).

    El *caption* es el mismo texto que ya pasó los guardrails. Si no hay foto, si el texto no cabe en un
    *caption* o si Telegram rechaza la foto (URL caída, formato), se manda solo el texto. Un `TimedOut`
    no cae al texto: la foto pudo llegar y el lead recibiría la respuesta dos veces (#161).

    El Markdown del agente va como HTML de Telegram (#187, DT-53). El tope del *caption* se mide sobre el
    texto visible, que es como lo cuenta Telegram; si rechaza el HTML del mensaje, va el texto plano.
    """
    rendered = formatting.render(reply.text)
    markup = {"reply_markup": keyboard(reply.buttons)} if reply.buttons else {}
    if reply.photo_url and _utf16_len(rendered.plain) <= CAPTION_MAX_CHARS:
        try:
            await send_with_retry(lambda: message.reply_photo(
                reply.photo_url, caption=rendered.html, parse_mode=ParseMode.HTML, **markup))
            return
        except TimedOut:
            raise
        except Exception:
            logger.warning("no se pudo mandar la foto de la propiedad; va solo el texto", exc_info=True)
    try:
        await send_with_retry(lambda: message.reply_text(rendered.html, parse_mode=ParseMode.HTML,
                                                         **markup))
    except BadRequest:
        logger.warning("Telegram rechazó el formato de la respuesta; va en texto plano", exc_info=True)
        await send_with_retry(lambda: message.reply_text(rendered.plain, **markup))


def keyboard(buttons: tuple[str, ...]) -> InlineKeyboardMarkup:
    """Los botones de B-11, uno por fila para que el texto completo quepa en un teléfono."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(BUTTON_LABELS[button],
                                                       callback_data=_BUTTON_PREFIX + button)]
                                 for button in buttons])


def buttons_for(turn: TurnReply) -> tuple[str, ...]:
    """Los botones bajo la respuesta (B-11, DT-51): solo si el turno trata de una propiedad.

    "Schedule a visit" solo cuando la FSM queda en `RECOMMEND`, el único estado en que pedir la visita
    arranca la reserva (B-8): en otro, el botón contestaría algo genérico sin agendar nada.
    """
    if turn.property is None:
        return ()
    return (VISIT_BUTTON, ADVISOR_BUTTON) if turn.fsm_state == "RECOMMEND" else (ADVISOR_BUTTON,)


def _utf16_len(text: str) -> int:
    # Telegram mide el texto en unidades UTF-16: un emoji fuera del plano básico cuenta como dos.
    return len(text.encode("utf-16-le")) // 2


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
    reply = await reply_turn(engine, catalog, telegram_user_id=telegram_user_id, text=text,
                             deep_link=deep_link, timestamp=timestamp, agent=agent, bot=bot)
    return reply.text


async def reply_turn(engine: Engine, catalog: Catalog, *, telegram_user_id: str, text: str,
                     deep_link: str | None = None, timestamp: datetime | None = None,
                     agent: Agent | None = None, bot: Sender | None = None) -> ChannelReply:
    """Como `reply_to`, pero con lo que el canal arma alrededor del texto (la foto y los botones)."""
    # B-7: un asesor abriendo su enlace de registro no es un lead; no genera turno ni score.
    token = canonical.advisor_token(deep_link)
    if token is not None:
        advisor = await run_in_threadpool(advisor_registration.register, engine,
                                          access_token=token, chat_id=telegram_user_id)
        return ChannelReply(advisor_registration.reply_for(advisor))

    identity = await run_in_threadpool(resolve_identity, engine, telegram_user_id)

    # B-2: la baja es determinista y corta antes del LLM.
    if consent.is_opt_out(text):
        await run_in_threadpool(consent.withdraw_consent, engine, identity.lead_id)
        return ChannelReply(consent.OPT_OUT_CONFIRMATION)

    # Se consulta antes del turno: `process_turn` marca actividad en la conversación.
    first_turn = await run_in_threadpool(consent.is_first_turn, engine, identity.lead_id)

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

    turn = await process_turn_reply(engine, event, catalog, agent, on_qualified, on_handoff)
    reply = canonical.from_canonical(turn.outbound)
    photo = turn.property.get("photo_url") if turn.property else None
    return ChannelReply(consent.with_privacy_notice(reply) if first_turn else reply,
                        photo if isinstance(photo, str) and photo else None, buttons_for(turn))
