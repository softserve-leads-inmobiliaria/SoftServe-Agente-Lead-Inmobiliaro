"""Identidad del lead en el canal (B-4, DT-12).

El canal resuelve (o crea) el `Lead` real y su `Conversation` ANTES de construir el evento
canónico: el `_ensure_rows()` de `StateStore` solo cubre eventos de leads desconocidos (el arnés).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.fsm import TERMINAL_STATES
from db.models import Conversation, FsmContext, Lead
from db.session import session_scope


@dataclass(frozen=True)
class Identity:
    lead_id: uuid.UUID
    conversation_id: uuid.UUID


def resolve_lead(session: Session, telegram_user_id: str) -> uuid.UUID:
    """Idempotente: el mismo `telegram_user_id` siempre devuelve el mismo `Lead.id`.

    `ON CONFLICT DO NOTHING` sobre el índice único deja que dos primeros mensajes simultáneos del
    mismo usuario creen una sola fila; el SELECT posterior lee la que haya ganado.
    """
    session.execute(
        insert(Lead)
        .values(id=uuid.uuid4(), telegram_user_id=telegram_user_id)
        .on_conflict_do_nothing(index_elements=[Lead.telegram_user_id])
    )
    return session.execute(
        select(Lead.id).where(Lead.telegram_user_id == telegram_user_id)
    ).scalar_one()


def resolve_conversation(session: Session, lead_id: uuid.UUID) -> uuid.UUID:
    """Reusa la conversación viva del lead; abre una nueva si la última quedó en estado terminal.

    La conversación nueva hereda del contexto anterior lo que es del lead y no de la conversación:
    la señal del score (`score_signal`, #84) y si ya está calificado (`lead_qualified`). El score
    (`lead_scores`) y la calificación (`Lead.qualified_at`) son por lead; si el contexto arrancara
    vacío, el primer turno recalcularía el score desde cero y lo sobreescribiría, y el agente dejaría
    de saber que puede revelar la dirección exacta.

    Se bloquea la fila del `Lead` (`FOR UPDATE`) para que dos mensajes simultáneos de un lead nuevo
    no abran dos conversaciones: la segunda transacción espera y encuentra la ya creada.
    """
    session.execute(select(Lead.id).where(Lead.id == lead_id).with_for_update())
    latest = session.execute(
        select(Conversation)
        .where(Conversation.lead_id == lead_id)
        .order_by(Conversation.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is not None and latest.fsm_state not in TERMINAL_STATES:
        return latest.id

    conversation_id = uuid.uuid4()
    session.execute(insert(Conversation).values(id=conversation_id, lead_id=lead_id))
    previous = session.get(FsmContext, latest.id) if latest is not None else None
    inherited = {} if previous is None else {"score_signal": previous.score_signal,
                                             "lead_qualified": previous.lead_qualified}
    session.execute(insert(FsmContext).values(conversation_id=conversation_id, **inherited))
    return conversation_id


def resolve_identity(engine: Engine, telegram_user_id: str) -> Identity:
    """Lead + conversación del mensaje entrante, en una sola transacción."""
    with session_scope(engine) as session:
        lead_id = resolve_lead(session, telegram_user_id)
        conversation_id = resolve_conversation(session, lead_id)
        return Identity(lead_id=lead_id, conversation_id=conversation_id)
