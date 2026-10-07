"""Registro del asesor en el bot (B-7): `t.me/<bot>?start=adv_<access_token>`.

Asocia el chat de Telegram desde el que se abrió el enlace al asesor dueño del token, para que la
tarjeta del lead calificado tenga a dónde llegar. Se usa el `access_token` y no el `advisor_id` a
propósito: `adv_001` es adivinable, el token no.

Ese chat queda para los avisos (#211): lo que el asesor escriba después ahí no es un lead. Recibe un texto
fijo con el enlace a su panel, y con `/stop` desliga el chat (por ejemplo, para probar el bot como lead con
la misma cuenta: Telegram le da a un bot un solo chat por cuenta).
"""
from __future__ import annotations

import logging
import os
from typing import Final

from sqlalchemy import Engine, select, update

from db.models import Advisor
from db.session import session_scope

logger = logging.getLogger("core.channels.advisor_registration")

REGISTERED_REPLY: Final = (
    "Done, {name}: I'll let you know here every time a lead from {zone} gets qualified or is handed off to you."
)
UNKNOWN_TOKEN_REPLY: Final = "That registration link isn't valid. Ask the team for a new one."
ADVISOR_CHAT_REPLY: Final = (
    "Hi {name}, this chat is where I send you the leads from {zone} when they get qualified or handed off. "
    "You handle them from your panel: {panel}\n\nTo stop getting notices here, send /stop."
)
UNREGISTERED_REPLY: Final = (
    "Done, {name}: I won't send lead notices to this chat anymore. They're still in your panel: {panel}"
)


def register(engine: Engine, *, access_token: str, chat_id: str) -> Advisor | None:
    """Vincula el chat al asesor del token. None si el token no existe (o no se dice cuál falló)."""
    with session_scope(engine) as session:
        advisor = session.execute(
            select(Advisor).where(Advisor.access_token == access_token)).scalar_one_or_none()
        if advisor is None:
            return None
        # Un chat pertenece a un solo asesor: si venía ligado a otro, se suelta primero.
        session.execute(update(Advisor).where(Advisor.telegram_chat_id == chat_id, Advisor.id != advisor.id)
                        .values(telegram_chat_id=None))
        session.execute(update(Advisor).where(Advisor.id == advisor.id)
                        .values(telegram_chat_id=chat_id))
        session.flush()
        logger.info("asesor registrado en Telegram", extra={"advisor_id": advisor.id})
        return advisor


def reply_for(advisor: Advisor | None) -> str:
    if advisor is None:
        return UNKNOWN_TOKEN_REPLY
    return REGISTERED_REPLY.format(name=advisor.name, zone=advisor.zone)


def panel_link(advisor: Advisor) -> str:
    """La cola del asesor en el panel (B-5), con la misma URL pública que el reporte semanal."""
    base = os.environ.get("PANEL_PUBLIC_URL", "http://localhost:8080").rstrip("/")
    return f"{base}/panel/{advisor.access_token}"


def registered_advisor(engine: Engine, chat_id: str) -> Advisor | None:
    """El asesor que registró este chat para los avisos (B-7), o None si es el chat de un lead."""
    with session_scope(engine) as session:
        return session.execute(select(Advisor).where(Advisor.telegram_chat_id == chat_id)).scalar_one_or_none()


def unregister(engine: Engine, chat_id: str) -> None:
    """Desliga el chat del asesor: los avisos siguen en el panel (B-15), pero ya no llegan a Telegram."""
    with session_scope(engine) as session:
        session.execute(update(Advisor).where(Advisor.telegram_chat_id == chat_id).values(telegram_chat_id=None))
    logger.info("asesor dado de baja del chat de Telegram")


def advisor_chat_reply(advisor: Advisor) -> str:
    return ADVISOR_CHAT_REPLY.format(name=advisor.name, zone=advisor.zone, panel=panel_link(advisor))


def unregistered_reply(advisor: Advisor) -> str:
    return UNREGISTERED_REPLY.format(name=advisor.name, panel=panel_link(advisor))
