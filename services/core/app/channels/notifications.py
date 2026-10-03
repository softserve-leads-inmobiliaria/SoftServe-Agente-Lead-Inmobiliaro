"""Avisos al asesor: la tarjeta del lead calificado (B-7, DT-24) y la ficha de traspaso (A-12).

Se disparan desde `process_turn`, después del commit del turno. Cada aviso se guarda en
`advisor_notifications` y el asesor lo ve en el panel (B-15, DT-42); si además registró su chat de
Telegram, se le manda por ahí con el `Bot` del canal (`get_bot()`). Nada de lo que pase acá puede
romper el turno del lead: todo falla en silencio.

El asesor lo elige la agenda del catálogo (`assign_advisor`, A-10, DT-33): zona, idioma y reparto por
turnos entre todos los asesores, tengan o no Telegram (DT-42). Si la agenda no responde, se usa la
regla local `pick_advisor` para que el aviso no se pierda.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from sqlalchemy import Engine, func, select, update

from app import scheduling_client
from app.interest import property_of_interest
from app.scheduling_client import SchedulingClient, SchedulingError
from app.visits import LOCAL_TZ, VISIT_KEY
from db.models import Advisor, AdvisorNotification, FsmContext, Lead, Message, Visit
from db.session import session_scope

logger = logging.getLogger("core.channels.notifications")

TELEGRAM_MAX_CHARS = 4096  # tope de un mensaje de Telegram; la transcripción se parte en varios

HANDOFF_REASONS = {
    "solicitud_humano": "el lead pidió hablar con una persona",
    "fuera_de_alcance": "consulta fuera del alcance del agente",
    "frustration": "varios turnos seguidos sin resolver (frustración)",
    "visit_scheduled": "visita agendada, el lead pasa al asesor",
    "visit_requested": "el lead pidió una visita y la agenda no la pudo reservar: coordinar su horario",
}
_ROLE_LABELS = {"user": "Lead", "assistant": "Agente"}


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
    preferred_time: str | None = None  # lo que pidió el lead cuando la agenda no pudo reservar (B-8)


# `_agenda_advisor` devuelve esto cuando la agenda no respondió: se decide con `pick_advisor`.
LOCAL_RULE = object()


async def _agenda_advisor(scheduling: SchedulingClient, lead_id: uuid.UUID) -> str | None | object:
    """El id del asesor que la agenda asignó al lead; None si no hay ningún asesor dado de alta."""
    try:
        return (await scheduling.assign_advisor(str(lead_id)))["advisor_id"]
    except SchedulingError as exc:
        if exc.code == "NOT_FOUND":
            return None
        logger.warning("la agenda no asignó asesor; se usa la regla local",
                       extra={"lead_id": str(lead_id), "error_code": exc.code})
        return LOCAL_RULE


def pick_advisor(session, zone: str | None) -> Advisor | None:
    """Regla local, respaldo de la agenda: el asesor de la zona, o cualquiera. Un lead calificado sin
    asesor en su zona no puede quedarse sin aviso.
    """
    if zone:
        found = session.execute(
            select(Advisor).where(Advisor.zone == zone).order_by(Advisor.id)).scalars().first()
        if found is not None:
            return found
    return session.execute(select(Advisor).order_by(Advisor.id)).scalars().first()


def assign(session, lead_id: uuid.UUID, advisor: Advisor) -> None:
    session.execute(update(Lead).where(Lead.id == lead_id)
                    .values(advisor_id=advisor.id, updated_at=func.now()))


def build_card(session, lead_id: uuid.UUID, conversation_id: uuid.UUID,
               score: Mapping[str, Any]) -> LeadCard:
    lead = session.get(Lead, lead_id)
    context = session.get(FsmContext, conversation_id)
    last = context.last_tool_results if context else {}
    prop = property_of_interest(last)
    requested = last.get(VISIT_KEY) if isinstance(last.get(VISIT_KEY), Mapping) else {}
    visit = session.execute(
        select(Visit).where(Visit.lead_id == lead_id).order_by(Visit.scheduled_at)).scalars().first()
    return LeadCard(
        lead_id=lead_id,
        zone=lead.zone if lead else None,
        score_total=int(score["total"]),
        score_band=str(score["band"]),
        property_title=prop.get("title") if prop else None,
        property_id=prop.get("id") if prop else None,
        visit_at=visit.scheduled_at.astimezone(LOCAL_TZ).strftime("%d/%m/%Y %H:%M") if visit else None,
        reasons=tuple(score.get("reasons") or ()),
        preferred_time=requested.get("preferred_time") or None,
    )


def _card_lines(card: LeadCard) -> list[str]:
    """Propiedad, visita y horario preferido: lo que comparten la tarjeta y la ficha de traspaso."""
    lines = []
    if card.property_title:
        lines.append(f"Propiedad de interés: {card.property_title} ({card.property_id})")
    if card.visit_at:
        lines.append(f"Visita agendada: {card.visit_at}")
    if card.preferred_time:
        lines.append(f"Horario preferido para la visita: {card.preferred_time}")
    return lines


def _link(card: LeadCard) -> str:
    return f"Ficha: /panel/<tu token>/leads/{card.lead_id}"


def card_summary(card: LeadCard) -> str:
    """La tarjeta sin el enlace a la ficha: lo que se guarda como aviso en el panel."""
    lines = [
        f"Lead calificado ({card.score_band} · {card.score_total}/100)",
        f"Zona: {card.zone or 'sin definir'}",
        *_card_lines(card),
    ]
    if card.reasons:
        lines.append("Por qué: " + "; ".join(card.reasons))
    return "\n".join(lines)


def render(card: LeadCard) -> str:
    """Texto plano de la tarjeta. Sin datos de contacto: esos los ve el asesor en el panel."""
    return f"{card_summary(card)}\n{_link(card)}"


@dataclass(frozen=True)
class HandoffFile:
    """Ficha de traspaso (A-12): la tarjeta del lead más el motivo y la transcripción completa."""

    card: LeadCard
    fsm_state: str
    reason: str
    transcript: tuple[tuple[str, str], ...]  # (rol, contenido) en orden cronológico


def transcript_of(session, conversation_id: uuid.UUID) -> tuple[tuple[str, str], ...]:
    """Todos los mensajes del lead y del agente de la conversación, sin tope (el historial del LLM sí lo tiene)."""
    rows = session.execute(
        select(Message.role, Message.content)
        .where(Message.conversation_id == conversation_id, Message.role.in_(tuple(_ROLE_LABELS)))
        .order_by(Message.created_at, Message.id)).all()
    return tuple((row.role, row.content) for row in rows)


def build_handoff_file(session, lead_id: uuid.UUID, conversation_id: uuid.UUID, score: Mapping[str, Any],
                       fsm_state: str, reason: str) -> HandoffFile:
    return HandoffFile(card=build_card(session, lead_id, conversation_id, score), fsm_state=fsm_state,
                       reason=reason, transcript=transcript_of(session, conversation_id))


def handoff_summary(handoff: HandoffFile) -> str:
    """El resumen de la ficha, sin la transcripción ni el enlace: lo que se guarda como aviso en el panel,
    que ya muestra la conversación en la ficha del lead."""
    card = handoff.card
    lines = [
        f"Traspaso de lead ({handoff.fsm_state}): {HANDOFF_REASONS.get(handoff.reason, handoff.reason)}",
        f"Score: {card.score_band} · {card.score_total}/100",
        f"Zona: {card.zone or 'sin definir'}",
        *_card_lines(card),
    ]
    if card.reasons:
        lines.append("Por qué el score: " + "; ".join(card.reasons))
    return "\n".join(lines)


def render_handoff(handoff: HandoffFile) -> list[str]:
    """Los mensajes de Telegram de la ficha: el resumen primero y la transcripción completa después.

    La transcripción se parte por líneas en trozos de a lo sumo `TELEGRAM_MAX_CHARS`; una línea
    más larga que el tope (un mensaje enorme) se corta a la fuerza. Ninguna parte se pierde.
    """
    messages = [f"{handoff_summary(handoff)}\n{_link(handoff.card)}"]

    if not handoff.transcript:
        return messages + ["Transcripción: sin mensajes."]
    transcript = ["Transcripción:", *(f"{_ROLE_LABELS.get(role, role)}: {content}"
                                       for role, content in handoff.transcript)]
    return messages + split_lines(transcript, TELEGRAM_MAX_CHARS)


def split_lines(lines: Sequence[str], limit: int) -> list[str]:
    """Junta líneas en textos de a lo sumo `limit` caracteres, sin perder ninguna. Una línea más larga
    que el tope se corta a la fuerza."""
    parts: list[str] = []
    current = ""
    for line in lines:
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if current and len(current) + 1 + len(line) > limit:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts


def _local_advisor_for_handoff(session, lead_id: uuid.UUID) -> Advisor | None:
    """Regla local: el asesor que ya recibió la tarjeta; si no hay, uno nuevo."""
    lead = session.get(Lead, lead_id)
    if lead is not None and lead.advisor_id:
        current = session.get(Advisor, lead.advisor_id)
        if current is not None:
            return current
    advisor = pick_advisor(session, lead.zone if lead else None)
    if advisor is not None:
        assign(session, lead_id, advisor)
    return advisor


def _local_advisor_on_qualify(session, lead_id: uuid.UUID) -> Advisor | None:
    lead = session.get(Lead, lead_id)
    advisor = pick_advisor(session, lead.zone if lead else None)
    if advisor is not None:
        assign(session, lead_id, advisor)
    return advisor


def _chosen(session, lead_id: uuid.UUID, chosen: str | None | object, local_rule) -> Advisor | None:
    """El asesor elegido por la agenda. `local_rule` decide si la agenda no respondió."""
    if chosen is LOCAL_RULE:
        return local_rule(session, lead_id)
    return session.get(Advisor, chosen) if chosen else None


def save_notice(session, advisor: Advisor, lead_id: uuid.UUID, kind: str, body: str) -> None:
    """El aviso que ve el asesor en el panel (B-15). Se guarda tenga o no Telegram."""
    session.add(AdvisorNotification(advisor_id=advisor.id, lead_id=lead_id, kind=kind, body=body))


async def _send(bot: Sender | None, chat_id: str | None, parts: Sequence[str], lead_id: uuid.UUID,
                advisor: Advisor) -> None:
    """Telegram es un canal adicional: si el asesor no lo registró, el aviso queda solo en el panel.
    Si el envío falla, el aviso ya está guardado."""
    extra = {"lead_id": str(lead_id), "advisor_id": advisor.id}
    if chat_id is None:
        logger.info("aviso solo en el panel: el asesor no registró Telegram", extra=extra)
        return
    if bot is None:
        logger.warning("aviso solo en el panel: el bot de Telegram no está arrancado", extra=extra)
        return
    try:
        for part in parts:
            await bot.send_message(chat_id=chat_id, text=part)
    except Exception:
        logger.exception("no se pudo mandar el aviso por Telegram; queda en el panel", extra=extra)
        return
    logger.info("aviso enviado por Telegram", extra={**extra, "parts": len(parts)})


async def notify_handoff(engine: Engine, event: Mapping[str, Any], score: Mapping[str, Any],
                         fsm_state: str, reason: str, bot: Sender | None,
                         scheduling: SchedulingClient | None = None) -> Advisor | None:
    """Deja la ficha del traspaso en el panel del asesor del lead y, si tiene Telegram, se la manda
    (resumen + transcripción completa).

    Es el mismo asesor que ya recibió la tarjeta de calificación si la hubo; si no, el de su zona
    (o la cola general) queda asignado en `Lead.advisor_id`. Devuelve el asesor, o None si no hay
    ningún asesor dado de alta. Nunca lanza.
    """
    lead_id = uuid.UUID(str(event["lead_id"]))
    conversation_id = uuid.UUID(str(event["conversation_id"]))
    try:
        chosen = await _agenda_advisor(scheduling or scheduling_client.get_scheduling(), lead_id)
        with session_scope(engine) as session:
            advisor = _chosen(session, lead_id, chosen, _local_advisor_for_handoff)
            if advisor is None:
                logger.warning("traspaso sin ningún asesor dado de alta",
                               extra={"lead_id": str(lead_id), "fsm_state": fsm_state})
                return None
            handoff = build_handoff_file(session, lead_id, conversation_id, score, fsm_state, reason)
            save_notice(session, advisor, lead_id, "traspaso", handoff_summary(handoff))
            chat_id = advisor.telegram_chat_id
        await _send(bot, chat_id, render_handoff(handoff), lead_id, advisor)
        return advisor
    except Exception:
        logger.exception("no se pudo enviar la ficha de traspaso", extra={"lead_id": str(lead_id)})
        return None


async def notify_advisor(engine: Engine, event: Mapping[str, Any], score: Mapping[str, Any],
                         bot: Sender | None, scheduling: SchedulingClient | None = None) -> Advisor | None:
    """Elige asesor, lo deja asignado en `Lead.advisor_id`, le deja la tarjeta en el panel y, si tiene
    Telegram, se la manda.

    Devuelve el asesor notificado, o None si no hay ningún asesor dado de alta. Nunca lanza.
    """
    lead_id = uuid.UUID(str(event["lead_id"]))
    conversation_id = uuid.UUID(str(event["conversation_id"]))
    try:
        chosen = await _agenda_advisor(scheduling or scheduling_client.get_scheduling(), lead_id)
        with session_scope(engine) as session:
            advisor = _chosen(session, lead_id, chosen, _local_advisor_on_qualify)
            if advisor is None:
                logger.warning("lead calificado sin ningún asesor dado de alta",
                               extra={"lead_id": str(lead_id)})
                return None
            card = build_card(session, lead_id, conversation_id, score)
            save_notice(session, advisor, lead_id, "calificado", card_summary(card))
            chat_id = advisor.telegram_chat_id
        await _send(bot, chat_id, [render(card)], lead_id, advisor)
        return advisor
    except Exception:
        logger.exception("no se pudo notificar al asesor", extra={"lead_id": str(lead_id)})
        return None
