"""Registro del asesor en el bot (B-7): `t.me/<bot>?start=adv_<access_token>`.

Asocia el chat de Telegram desde el que se abrió el enlace al asesor dueño del token, para que la
tarjeta del lead calificado tenga a dónde llegar. Se usa el `access_token` y no el `advisor_id` a
propósito: `adv_001` es adivinable, el token no.
"""
from __future__ import annotations

import logging
from typing import Final

from sqlalchemy import Engine, select, update

from db.models import Advisor
from db.session import session_scope

logger = logging.getLogger("core.channels.advisor_registration")

REGISTERED_REPLY: Final = (
    "Listo, {name}: te voy a avisar por acá cada vez que un lead de {zone} quede calificado."
)
UNKNOWN_TOKEN_REPLY: Final = "Ese enlace de registro no es válido. Pedile al equipo uno nuevo."


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
