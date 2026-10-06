"""Consentimiento y baja del lead (B-2).

El opt-out es determinista y se resuelve en el canal, antes de cualquier LLM: la baja de un lead no
puede depender de que un modelo la interprete bien.
"""
from __future__ import annotations

import unicodedata
import uuid
from typing import Final

from sqlalchemy import Engine, select, update

from app import alerts, jobs
from app.input_guard import is_opt_out as core_is_opt_out
from db.models import AuditLog, Conversation, Lead
from db.session import session_scope

PRIVACY_NOTICE: Final = (
    "Before we start: I only use your information to help with your search and, if you'd like, to put "
    "you in touch with an agent. Type STOP at any time and we won't message you again."
)
OPT_OUT_CONFIRMATION: Final = (
    "Done, you're unsubscribed and I won't message you again. If you change your mind, write to me anytime."
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
    # Inglés (#202, DT-55): lo demás lo cubre el criterio del core (`input_guard`).
    "unsubscribe",
    "delete my data",
    "erase my data",
    "remove my data",
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


def is_first_turn(engine: Engine, lead_id: uuid.UUID) -> bool:
    """True mientras ninguna conversación del lead haya registrado actividad (B-2: una vez por lead).

    Es por lead y no por conversación: tras un estado terminal (p. ej. `HUMAN_HANDOFF`) el canal abre
    una conversación nueva, y el lead que ya vio el aviso no debe recibirlo otra vez (#189).

    Se consulta ANTES de `process_turn`: `StateStore.touch()` mueve `last_message_at` en cada turno.
    """
    with session_scope(engine) as session:
        active = session.execute(
            select(Conversation.id)
            .where(Conversation.lead_id == lead_id,
                   Conversation.last_message_at > Conversation.started_at)
            .limit(1)
        ).first()
        return active is None


def withdraw_consent(engine: Engine, lead_id: uuid.UUID) -> None:
    """Marca la baja de inmediato: `consent_status=withdrawn` y el lead fuera del funnel.

    Deja el mismo rastro de auditoría (`input.opt_out`) que la baja que detecta el core, cancela lo
    que el *scheduler* tenía pendiente para el lead (A-17) y desactiva sus alertas (A-18).
    """
    with session_scope(engine) as session:
        session.execute(
            update(Lead)
            .where(Lead.id == lead_id)
            .values(consent_status="withdrawn", status="closed_lost")
        )
        jobs.cancel_for_lead(session, lead_id)
        alerts.deactivate_for_lead(session, lead_id)
        session.add(AuditLog(actor="channel", action="input.opt_out",
                             payload={"lead_id": str(lead_id), "channel": "telegram"}))


def with_privacy_notice(reply: str) -> str:
    return f"{PRIVACY_NOTICE}\n\n{reply}"
