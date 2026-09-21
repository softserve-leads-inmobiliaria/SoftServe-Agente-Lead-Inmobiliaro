"""Consultas del panel (B-5). Todo se filtra por la zona del asesor, sin excepción."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Conversation, Lead, LeadScore, Message, Visit


@dataclass(frozen=True)
class LeadSummary:
    id: uuid.UUID
    telegram_user_id: str
    zone: str | None
    status: str
    consent_status: str
    fsm_state: str | None
    score_total: int | None
    score_band: str | None
    last_message_at: datetime | None


@dataclass(frozen=True)
class LeadDetail:
    lead: Lead
    score: LeadScore | None
    conversations: list[Conversation]
    visits: list[Visit]
    messages: list[Message]


def leads_for_advisor(session: Session, advisor) -> list[LeadSummary]:
    """Leads de la zona del asesor, con su estado de FSM y su score si ya lo tienen."""
    rows = session.execute(
        select(Lead, Conversation, LeadScore)
        .outerjoin(Conversation, Conversation.lead_id == Lead.id)
        # outerjoin, no join: un lead sin score todavía tiene que aparecer en la lista.
        .outerjoin(LeadScore, LeadScore.lead_id == Lead.id)
        .where(Lead.zone == advisor.zone)
        .order_by(Lead.created_at.desc())
    ).all()
    return [
        LeadSummary(
            id=lead.id, telegram_user_id=lead.telegram_user_id, zone=lead.zone, status=lead.status,
            consent_status=lead.consent_status,
            fsm_state=conversation.fsm_state if conversation else None,
            score_total=score.total if score else None,
            score_band=score.band if score else None,
            last_message_at=conversation.last_message_at if conversation else None,
        )
        for lead, conversation, score in rows
    ]


def lead_detail(session: Session, advisor, lead_id: uuid.UUID) -> LeadDetail | None:
    """Ficha completa. None si el lead no existe **o es de otra zona**: mismo 404 para los dos."""
    lead = session.execute(
        select(Lead).where(Lead.id == lead_id, Lead.zone == advisor.zone)
    ).scalar_one_or_none()
    if lead is None:
        return None

    conversations = list(session.execute(
        select(Conversation).where(Conversation.lead_id == lead.id)
        .order_by(Conversation.started_at)
    ).scalars())
    visits = list(session.execute(
        select(Visit).where(Visit.lead_id == lead.id).order_by(Visit.scheduled_at)
    ).scalars())
    messages: list[Message] = []
    if conversations:
        messages = list(session.execute(
            select(Message)
            .where(Message.conversation_id.in_([c.id for c in conversations]))
            .order_by(Message.created_at, Message.id)  # usa ix_messages_conversation_created
        ).scalars())
    return LeadDetail(
        lead=lead,
        score=session.get(LeadScore, lead.id),
        conversations=conversations,
        visits=visits,
        messages=messages,
    )


def as_context(detail: LeadDetail) -> dict[str, Any]:
    """La ficha como contexto plano para la plantilla."""
    return {
        "lead": detail.lead, "score": detail.score, "visits": detail.visits,
        "messages": detail.messages,
        "fsm_state": detail.conversations[-1].fsm_state if detail.conversations else None,
    }
