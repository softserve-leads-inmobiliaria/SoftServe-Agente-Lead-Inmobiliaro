"""Tarjeta del lead calificado al asesor, por Telegram (B-7, DT-24).

Se dispara desde `process_turn` vía `on_qualified`, después del commit del turno y solo en el turno
exacto en que la banda del score cruza a WARM/HOT. Reusa el `Bot` del canal (`get_bot()`), no crea un
segundo cliente. Nada de lo que pase acá puede romper el turno del lead: todo falla en silencio.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from sqlalchemy import Engine, func, select, update

from app.interest import property_of_interest
from db.models import Advisor, FsmContext, Lead, Visit
from db.session import session_scope

logger = logging.getLogger("core.channels.notifications")


class Sender(Protocol):
    async def send_message(self, chat_id: int | str, text: str) -> Any: ...


@dataclass(frozen=True)
class LeadCard:
    lead_id: uuid.UUID
    zone: str | None
    score_total: int
    score_band: str
    property_title: str | None
    property_id: str | None
    visit_at: str | None
    reasons: tuple[str, ...]


def pick_advisor(session, zone: str | None) -> Advisor | None:
    """El asesor de la zona del lead que ya abrió el bot; si no hay, cualquiera registrado.

    Cola general de respaldo (patrón de v1): un lead calificado sin asesor en su zona no puede
    quedarse sin aviso.
    """
    registered = Advisor.telegram_chat_id.is_not(None)
    if zone:
        found = session.execute(
            select(Advisor).where(Advisor.zone == zone, registered).order_by(Advisor.id)
        ).scalars().first()
        if found is not None:
            return found
    return session.execute(select(Advisor).where(registered).order_by(Advisor.id)).scalars().first()


def assign(session, lead_id: uuid.UUID, advisor: Advisor) -> None:
    session.execute(update(Lead).where(Lead.id == lead_id)
                    .values(advisor_id=advisor.id, updated_at=func.now()))


def build_card(session, lead_id: uuid.UUID, conversation_id: uuid.UUID,
               score: Mapping[str, Any]) -> LeadCard:
    lead = session.get(Lead, lead_id)
    context = session.get(FsmContext, conversation_id)
    prop = property_of_interest(context.last_tool_results if context else {})
    visit = session.execute(
        select(Visit).where(Visit.lead_id == lead_id).order_by(Visit.scheduled_at)).scalars().first()
    return LeadCard(
        lead_id=lead_id,
        zone=lead.zone if lead else None,
        score_total=int(score["total"]),
        score_band=str(score["band"]),
        property_title=prop.get("title") if prop else None,
        property_id=prop.get("id") if prop else None,
        visit_at=visit.scheduled_at.strftime("%d/%m/%Y %H:%M") if visit else None,
        reasons=tuple(score.get("reasons") or ()),
    )


def render(card: LeadCard) -> str:
    """Texto plano de la tarjeta. Sin datos de contacto: esos los ve el asesor en el panel."""
    lines = [
        f"Lead calificado ({card.score_band} · {card.score_total}/100)",
        f"Zona: {card.zone or 'sin definir'}",
    ]
    if card.property_title:
        lines.append(f"Propiedad de interés: {card.property_title} ({card.property_id})")
    if card.visit_at:
        lines.append(f"Visita agendada: {card.visit_at}")
    if card.reasons:
        lines.append("Por qué: " + "; ".join(card.reasons))
    lines.append(f"Ficha: /panel/<tu token>/leads/{card.lead_id}")
    return "\n".join(lines)


async def notify_advisor(engine: Engine, event: Mapping[str, Any], score: Mapping[str, Any],
                         bot: Sender | None) -> Advisor | None:
    """Elige asesor, lo deja asignado en `Lead.advisor_id` y le manda la tarjeta.

    Devuelve el asesor notificado, o None si no había bot ni asesor registrado. Nunca lanza.
    """
    lead_id = uuid.UUID(str(event["lead_id"]))
    conversation_id = uuid.UUID(str(event["conversation_id"]))
    try:
        with session_scope(engine) as session:
            lead = session.get(Lead, lead_id)
            advisor = pick_advisor(session, lead.zone if lead else None)
            if advisor is None:
                logger.warning("lead calificado sin ningún asesor registrado en Telegram",
                               extra={"lead_id": str(lead_id)})
                return None
            assign(session, lead_id, advisor)
            card = build_card(session, lead_id, conversation_id, score)
            chat_id = advisor.telegram_chat_id
            name = advisor.name
        if bot is None:
            logger.warning("lead calificado pero el bot de Telegram no está arrancado",
                           extra={"lead_id": str(lead_id), "advisor_id": advisor.id})
            return advisor
        await bot.send_message(chat_id=chat_id, text=render(card))
        logger.info("tarjeta enviada al asesor", extra={"lead_id": str(lead_id), "advisor": name})
        return advisor
    except Exception:
        logger.exception("no se pudo notificar al asesor", extra={"lead_id": str(lead_id)})
        return None
