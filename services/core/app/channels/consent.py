"""Consentimiento y baja del lead (B-2).

El opt-out es determinista y se resuelve en el canal, antes de cualquier LLM: la baja de un lead no
puede depender de que un modelo la interprete bien.
"""
from __future__ import annotations

import unicodedata
import uuid
from typing import Final

from sqlalchemy import Engine, select, update

from app.input_guard import is_opt_out as core_is_opt_out
from db.models import AuditLog, Conversation, Lead
from db.session import session_scope

PRIVACY_NOTICE: Final = (
    "Antes de empezar: uso tus datos solo para orientarte en tu búsqueda y, si te interesa, "
    "ponerte en contacto con un asesor. Escribe STOP cuando quieras y no volvemos a escribirte."
)
OPT_OUT_CONFIRMATION: Final = (
    "Listo, registré tu baja y no volveré a escribirte. Si cambias de idea, escríbeme cuando quieras."
)

_OPT_OUT_PHRASES: Final[tuple[str, ...]] = (
    "/stop",
    "stop",
    "baja",
    "darme de baja",
    "no me escriban",
    "no me escribas",
    "cancelar suscripcion",
    "eliminen mis datos",
    "borren mis datos",
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.strip().lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def is_opt_out(text: str) -> bool:
    """Baja por las frases del canal o por el criterio del core (A-8): quien pida la baja de cualquiera
    de las dos maneras la recibe, sin importar por qué camino llegue el mensaje."""
    normalized = _normalize(text)
    if not normalized:
        return False
    return (normalized in _OPT_OUT_PHRASES
            or any(phrase in normalized for phrase in _OPT_OUT_PHRASES if " " in phrase)
            or core_is_opt_out(text))


def is_first_turn(engine: Engine, conversation_id: uuid.UUID) -> bool:
    """True mientras la conversación no haya registrado actividad.

    Se consulta ANTES de `process_turn`: `StateStore.touch()` mueve `last_message_at` en cada turno.
    """
    with session_scope(engine) as session:
        row = session.execute(
            select(Conversation.started_at, Conversation.last_message_at)
            .where(Conversation.id == conversation_id)
        ).one_or_none()
        if row is None:
            return True
        started_at, last_message_at = row
        return last_message_at <= started_at


def withdraw_consent(engine: Engine, lead_id: uuid.UUID) -> None:
    """Marca la baja de inmediato: `consent_status=withdrawn` y el lead fuera del funnel.

    Deja el mismo rastro de auditoría (`input.opt_out`) que la baja que detecta el core.
    """
    with session_scope(engine) as session:
        session.execute(
            update(Lead)
            .where(Lead.id == lead_id)
            .values(consent_status="withdrawn", status="closed_lost")
        )
        session.add(AuditLog(actor="channel", action="input.opt_out",
                             payload={"lead_id": str(lead_id), "channel": "telegram"}))


def with_privacy_notice(reply: str) -> str:
    return f"{PRIVACY_NOTICE}\n\n{reply}"
