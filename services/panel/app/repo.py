"""Consultas del panel (B-5). Un asesor ve los leads de su zona y los que tiene asignados (B-15): la cola
general le puede asignar un lead de otra zona o sin zona, y su aviso tiene que llevar a una ficha que abra."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from db.models import AdvisorNotification, Conversation, Lead, LeadScore, Message, Visit


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
    advisor_id: str | None = None
    qualified_at: datetime | None = None


@dataclass(frozen=True)
class LeadDetail:
    lead: Lead
    score: LeadScore | None
    conversations: list[Conversation]
    visits: list[Visit]
    messages: list[Message]


# Orden de la cola (B-6): primero por calidad del lead, no por fecha de llegada.
_BAND_RANK = case((LeadScore.band == "HOT", 0), (LeadScore.band == "WARM", 1),
                  (LeadScore.band == "COLD", 2), else_=3)


def _visible_to(advisor):
    """Los leads que el asesor puede ver: los de su zona y los que tiene asignados."""
    return or_(Lead.zone == advisor.zone, Lead.advisor_id == advisor.id)


def leads_for_advisor(session: Session, advisor) -> list[LeadSummary]:
    """Cola del asesor: los leads de su zona y los que tiene asignados, los mejores arriba.

    HOT > WARM > COLD > sin calificar; dentro de la banda, mayor `total` primero y el más antiguo
    desempata. Un lead puede tener varias conversaciones (se abre una nueva tras un estado
    terminal, DT-20): se muestra la más reciente, y el lead una sola vez.
    """
    latest_conversation = (
        select(Conversation.id)
        .where(Conversation.lead_id == Lead.id)
        .order_by(Conversation.started_at.desc())
        .limit(1)
        .correlate(Lead)
        .scalar_subquery()
    )
    rows = session.execute(
        select(Lead, Conversation, LeadScore)
        .outerjoin(Conversation, Conversation.id == latest_conversation)
        # outerjoin, no join: un lead sin score todavía tiene que aparecer en la lista.
        .outerjoin(LeadScore, LeadScore.lead_id == Lead.id)
        .where(_visible_to(advisor))
        .order_by(_BAND_RANK, LeadScore.total.desc().nulls_last(), Lead.created_at.asc())
    ).all()
    return [
        LeadSummary(
            id=lead.id, telegram_user_id=lead.telegram_user_id, zone=lead.zone, status=lead.status,
            consent_status=lead.consent_status,
            fsm_state=conversation.fsm_state if conversation else None,
            score_total=score.total if score else None,
            score_band=score.band if score else None,
            last_message_at=conversation.last_message_at if conversation else None,
            advisor_id=lead.advisor_id,
            qualified_at=lead.qualified_at,
        )
        for lead, conversation, score in rows
    ]


def claim_lead(session: Session, advisor, lead_id: uuid.UUID) -> bool:
    """"Marcar en gestión" (B-6): el asesor toma el lead. `Lead.status` pasa a `sql`.

    Devuelve False si el lead no existe o no lo puede ver (mismo 404 uniforme que la ficha).
    Idempotente: marcarlo dos veces no cambia nada. Si nadie lo tenía asignado, queda con quien
    lo tomó; si B-7 ya lo asignó a otro, se respeta esa asignación. Guarda cuándo se tomó (B-12).
    """
    exists = session.execute(
        select(Lead.id).where(Lead.id == lead_id, _visible_to(advisor))).scalar_one_or_none()
    if exists is None:
        return False
    session.execute(
        update(Lead)
        .where(Lead.id == lead_id, Lead.status.in_(("nuevo", "mql")))
        .values(status="sql", advisor_id=func.coalesce(Lead.advisor_id, advisor.id),
                claimed_at=func.coalesce(Lead.claimed_at, func.now()), updated_at=func.now()))
    return True


def lead_detail(session: Session, advisor, lead_id: uuid.UUID) -> LeadDetail | None:
    """Ficha completa. None si el lead no existe **o el asesor no lo puede ver**: mismo 404 para los dos."""
    lead = session.execute(
        select(Lead).where(Lead.id == lead_id, _visible_to(advisor))
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


# --- avisos (B-15) ---------------------------------------------------------------------------------

NOTICES_SHOWN = 50


def notices_for_advisor(session: Session, advisor) -> list[AdvisorNotification]:
    """Los avisos del asesor, los más nuevos primero. Solo los suyos: ni los de su zona ni los de otros."""
    return list(session.execute(
        select(AdvisorNotification).where(AdvisorNotification.advisor_id == advisor.id)
        .order_by(AdvisorNotification.created_at.desc(), AdvisorNotification.id)
        .limit(NOTICES_SHOWN)
    ).scalars())


def unread_notices(session: Session, advisor) -> int:
    return session.execute(
        select(func.count()).select_from(AdvisorNotification)
        .where(AdvisorNotification.advisor_id == advisor.id, AdvisorNotification.read_at.is_(None))
    ).scalar_one()


def open_notice(session: Session, advisor, notice_id: uuid.UUID) -> uuid.UUID | None:
    """Marca el aviso como leído y devuelve su lead. None si no existe o es de otro asesor."""
    notice = session.execute(
        select(AdvisorNotification)
        .where(AdvisorNotification.id == notice_id, AdvisorNotification.advisor_id == advisor.id)
    ).scalar_one_or_none()
    if notice is None:
        return None
    if notice.read_at is None:
        notice.read_at = func.now()
    return notice.lead_id
