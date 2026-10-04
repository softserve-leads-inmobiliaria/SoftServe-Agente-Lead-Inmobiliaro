"""Consultas del panel (B-5). Un asesor ve los leads de su zona y los que tiene asignados (B-15): la cola
general le puede asignar un lead de otra zona o sin zona, y su aviso tiene que llevar a una ficha que abra."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import Session

from app.inventory import LOCAL_TZ
from db.models import (
    DEAL_KINDS,
    AdvisorNotification,
    Conversation,
    Deal,
    Lead,
    LeadScore,
    Message,
    Property,
    PropertyInterest,
    Visit,
)
from db.property_history import StatusUnchanged, UnknownProperty, set_status


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
class PropertyOption:
    """Una propiedad del selector de "Cerrar operación" (N-3)."""
    id: str
    title: str
    type: str
    status: str
    touched: bool  # el lead la vio, consultó o visitó (`property_interest`)

    @property
    def closable(self) -> bool:
        return self.status != "cerrado"

    @property
    def kind(self) -> str:
        """El tipo de operación que sugiere la propiedad: una preventa se cierra como venta."""
        return "renta" if self.type == "renta" else "venta"


@dataclass(frozen=True)
class DealRow:
    """Una operación cerrada, como la muestra la ficha."""
    closed_on: date
    property_id: str
    title: str
    kind: str
    price: Decimal
    advisor_id: str


@dataclass(frozen=True)
class LeadDetail:
    lead: Lead
    score: LeadScore | None
    conversations: list[Conversation]
    visits: list[Visit]
    messages: list[Message]
    deals: list[DealRow] = field(default_factory=list)
    close_options: list[PropertyOption] = field(default_factory=list)


# Orden de la cola (B-6): primero por calidad del lead, no por fecha de llegada.
_BAND_RANK = case((LeadScore.band == "HOT", 0), (LeadScore.band == "WARM", 1),
                  (LeadScore.band == "COLD", 2), else_=3)


def _visible_to(advisor):
    """Los leads que el asesor puede ver: los de su zona y los que tiene asignados. Nunca los de la historia
    sintética (N-9): solo existen para las vistas agregadas de la demo, no son gente a la que atender."""
    return and_(or_(Lead.zone == advisor.zone, Lead.advisor_id == advisor.id), Lead.synthetic.is_(False))


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
        deals=lead_deals(session, lead.id),
        close_options=close_options(session, lead),
    )


def as_context(detail: LeadDetail) -> dict[str, Any]:
    """La ficha como contexto plano para la plantilla."""
    return {
        "lead": detail.lead, "score": detail.score, "visits": detail.visits,
        "messages": detail.messages,
        "fsm_state": detail.conversations[-1].fsm_state if detail.conversations else None,
        "deals": detail.deals,
        "close_options": detail.close_options,
    }


# --- cerrar operación (N-3) ------------------------------------------------------------------------

MAX_PRICE = Decimal("999999999999.99")  # lo que cabe en `deals.price` (Numeric(14, 2))


class ClosingRejected(ValueError):
    """El cierre no se registró: el mensaje es para el asesor, en el formulario."""


@dataclass(frozen=True)
class Closing:
    """Lo que el asesor llenó en el formulario, tal como llegó."""
    property_id: str
    kind: str
    price: str
    closed_on: str


def close_options(session: Session, lead: Lead) -> list[PropertyOption]:
    """El selector de "Cerrar operación": primero las propiedades que el lead tocó, la más reciente arriba
    (en cualquier estado, para que el asesor vea si ya se cerró); después las `disponible` o `apartado`
    de su zona."""
    touched = (
        select(PropertyInterest.property_id, func.max(PropertyInterest.created_at).label("last_seen"))
        .where(PropertyInterest.lead_id == lead.id, PropertyInterest.synthetic.is_(False))
        .group_by(PropertyInterest.property_id)
        .subquery()
    )
    first = session.execute(
        select(Property).join(touched, touched.c.property_id == Property.id)
        .order_by(touched.c.last_seen.desc(), Property.id)
    ).scalars().all()
    rest: list[Property] = []
    if lead.zone:
        rest = session.execute(
            select(Property)
            .where(Property.zone == lead.zone, Property.status.in_(("disponible", "apartado")),
                   Property.id.not_in([prop.id for prop in first]))
            .order_by(Property.id)
        ).scalars().all()
    touched_ids = {prop.id for prop in first}
    return [PropertyOption(prop.id, prop.title, prop.type, prop.status, touched=prop.id in touched_ids)
            for prop in [*first, *rest]]


def lead_deals(session: Session, lead_id: uuid.UUID) -> list[DealRow]:
    """Las operaciones cerradas del lead, la más reciente primero. Nunca las de la historia sintética."""
    rows = session.execute(
        select(Deal, Property.title).join(Property, Property.id == Deal.property_id)
        .where(Deal.lead_id == lead_id, Deal.synthetic.is_(False))
        .order_by(Deal.closed_at.desc())
    ).all()
    return [DealRow(deal.closed_at.astimezone(LOCAL_TZ).date(), deal.property_id, title, deal.kind,
                    deal.price, deal.advisor_id)
            for deal, title in rows]


def closing_time(closed_on: date, now: datetime) -> datetime:
    """Cuándo se cerró: ahora si es hoy (hora local); si es un día pasado, a mediodía de ese día, para que
    el alta de la propiedad (`disponible`) quede antes del cierre en los días en el mercado (N-6)."""
    if closed_on == now.astimezone(LOCAL_TZ).date():
        return now
    return datetime.combine(closed_on, time(12), tzinfo=LOCAL_TZ)


def close_deal(session: Session, advisor, lead_id: uuid.UUID, closing: Closing, *, now: datetime) -> bool:
    """"Cerrar operación" (N-3): en la transacción del request, la fila en `deals`, el lead a `closed_won`
    y la propiedad a `cerrado` por `db/property_history.py`, con el asesor como actor.

    Devuelve False si el lead no existe o el asesor no lo puede ver (el mismo 404 que la ficha, DT-5).
    Lanza `ClosingRejected` sin haber escrito nada si el formulario no es válido o la propiedad ya está
    cerrada: el cambio de estado va primero y es lo único que puede fallar por la base.
    """
    lead = session.execute(
        select(Lead).where(Lead.id == lead_id, _visible_to(advisor))).scalar_one_or_none()
    if lead is None:
        return False
    if closing.kind not in DEAL_KINDS:
        raise ClosingRejected("Elige si la operación es renta o venta.")
    try:
        price = Decimal(closing.price.strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        price = None
    if price is None or not price.is_finite():
        raise ClosingRejected("El precio de cierre tiene que ser un número.")
    price = price.quantize(Decimal("0.01"))
    if price < 0:
        raise ClosingRejected("El precio de cierre no puede ser negativo.")
    if price > MAX_PRICE:
        raise ClosingRejected("El precio de cierre es demasiado alto.")
    try:
        closed_on = date.fromisoformat(closing.closed_on.strip())
    except (ValueError, AttributeError):
        raise ClosingRejected("La fecha de cierre no es válida.") from None
    if closed_on > now.astimezone(LOCAL_TZ).date():
        raise ClosingRejected("La fecha de cierre no puede ser futura.")
    if closing.property_id not in {option.id for option in close_options(session, lead)}:
        raise ClosingRejected("Esa propiedad no está entre las de este lead.")

    closed_at = closing_time(closed_on, now)
    try:
        set_status(session, closing.property_id, "cerrado", actor=advisor.id, at=closed_at)
    except StatusUnchanged:
        raise ClosingRejected("Esa propiedad ya está cerrada: no se registró nada.") from None
    except UnknownProperty:
        raise ClosingRejected("Esa propiedad no está entre las de este lead.") from None
    session.add(Deal(property_id=closing.property_id, lead_id=lead.id, advisor_id=advisor.id,
                     kind=closing.kind, price=price, closed_at=closed_at))
    session.execute(
        update(Lead).where(Lead.id == lead.id)
        .values(status="closed_won", advisor_id=func.coalesce(Lead.advisor_id, advisor.id),
                updated_at=func.now()))
    return True


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
