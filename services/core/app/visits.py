"""Agendar la visita desde la conversación (B-8, DT-39), con la agenda del catálogo (`get_slots`, `book`).

Lo decide el código, no el LLM: la agenda no es una tool MCP (DT-32), así que el modelo nunca ve ni
inventa horarios. Son dos pasos:

1. Cuando el lead pide la visita (`RECOMMEND -> SCHEDULE`), `start` pide el asesor del lead y le ofrece
   los primeros horarios libres, numerados. La oferta queda en `last_tool_results` (`OFFER_KEY`).
2. En `SCHEDULE`, `follow_up` lee la respuesta del lead. Si elige un horario de la oferta, lo reserva con
   `book` y la visita queda resuelta (`SCHEDULE -> HANDOFF`). Si no se entiende cuál, vuelve a ofrecerlos.

Respaldo: si la agenda no responde, no hay ningún asesor, no quedan horarios o no se sabe qué
propiedad visitar, se le pide al lead su día y hora preferidos. Su siguiente mensaje se le pasa al
asesor en la ficha de traspaso, que coordina la visita a mano.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from app.scheduling_client import SchedulingClient, SchedulingError

LOCAL_TZ = timezone(timedelta(hours=-5))  # hora de Colombia, la misma de la agenda (DT-32, DT-55)
OFFERED_SLOTS = 3
SEARCH_DAYS = 7

OFFER_KEY = "visit_offer"  # oferta en curso: horarios ofrecidos, o la espera de un horario preferido
VISIT_KEY = "visit"  # visita resuelta: reservada (`visit_id`, `scheduled_at`) o con `preferred_time`

_WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_ORDINALS = (("primer", 1), ("segund", 2), ("tercer", 3))
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_AT_HOUR = re.compile(r"\ba las (\d{1,2})\b")
_NUMBER = re.compile(r"(?<![\d:])\b(\d{1,2})\b(?![\d:])")
# Algo que parezca un día o una hora: sin esto, un "ok" quedaría como horario preferido.
_WHEN = re.compile(r"\d|\b(hoy|manana|pasado|lunes|martes|miercoles|jueves|viernes|sabado|domingo|semana"
                   r"|tarde|noche|mediodia|temprano)\b")

PREFERRED_ASK = ("Ahora no puedo reservar la visita desde aquí. ¿Qué día y hora te acomodan? "
                 "Se lo paso a tu asesor para que la confirme.")
PREFERRED_AGAIN = "¿Qué día y a qué hora te gustaría hacer la visita? Se lo paso a tu asesor."
PREFERRED_THANKS = ("Gracias. Le paso tu horario preferido a tu asesor, que te contactará para "
                    "confirmar la visita.")


@dataclass(frozen=True)
class VisitStep:
    """Lo que el código responde en un turno de agenda y lo que deja en `last_tool_results`."""

    reply: str
    tool_results: dict[str, Any] = field(default_factory=dict)
    settled: bool = False  # la visita quedó resuelta: reservada o con un horario preferido para el asesor
    booked: bool = False  # hay una visita real en `visits`


def today_local(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ).date()


def slot_label(slot: str) -> str:
    """`2026-10-06T10:00:00-05:00` -> `lun 06/10 10:00`, en hora local."""
    local = datetime.fromisoformat(slot).astimezone(LOCAL_TZ)
    return f"{_WEEKDAYS[local.weekday()]} {local:%d/%m %H:%M}"


def _listing(slots: list[str]) -> str:
    return "\n".join(f"{n}) {slot_label(slot)}" for n, slot in enumerate(slots, start=1))


def offer_text(prop: Mapping[str, Any], slots: list[str], intro: str | None = None) -> str:
    intro = intro or f"Tengo estos horarios para visitar {prop['title']} con tu asesor:"
    return f"{intro}\n{_listing(slots)}\nRespóndeme con el número del que te acomode."


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def choose_slot(text: str, slots: list[str]) -> str | None:
    """El horario de la oferta que eligió el lead, o None si no se entiende cuál (o hay más de uno).

    Acepta la hora ("10:00", "a las 11"), el número de la opción ("2", "la 2") o el ordinal ("la segunda").
    """
    normalized = _normalize(text)
    local = [datetime.fromisoformat(slot).astimezone(LOCAL_TZ) for slot in slots]

    def unique(matches: list[str]) -> str | None:
        return matches[0] if len(set(matches)) == 1 else None

    clocks = _CLOCK.findall(normalized)
    if clocks:
        return unique([slot for slot, at in zip(slots, local, strict=True)
                       for hour, minute in clocks if (at.hour, at.minute) == (int(hour), int(minute))])
    hours = _AT_HOUR.findall(normalized)
    if hours:
        return unique([slot for slot, at in zip(slots, local, strict=True) for hour in hours if at.hour == int(hour)])
    numbers = {int(n) for n in _NUMBER.findall(normalized)}
    if numbers:
        return slots[numbers.pop() - 1] if len(numbers) == 1 and 1 <= min(numbers) <= len(slots) else None
    ordinals = {n for word, n in _ORDINALS if word in normalized and n <= len(slots)}
    return slots[ordinals.pop() - 1] if len(ordinals) == 1 else None


def _property(prop: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Lo que se guarda de la propiedad: sin `status`, que en el turno siguiente ya puede estar viejo."""
    if not prop or not prop.get("id") or not prop.get("title"):
        return None
    return {key: prop[key] for key in ("id", "title", "zone") if prop.get(key)}


def ask_preferred(prop: Mapping[str, Any] | None) -> VisitStep:
    return VisitStep(PREFERRED_ASK, {OFFER_KEY: {"property": prop, "preferred_time": True}})


async def _offer(scheduling: SchedulingClient, lead_id: str, prop: dict[str, Any], today: date,
                 intro: str | None = None) -> VisitStep:
    try:
        advisor_id = (await scheduling.assign_advisor(lead_id))["advisor_id"]
        found = await scheduling.get_slots(advisor_id, today.isoformat(), SEARCH_DAYS)
        slots = [str(slot) for slot in found["slots"]][:OFFERED_SLOTS]
    except (SchedulingError, KeyError, TypeError, ValueError):
        return ask_preferred(prop)
    if not slots:
        return ask_preferred(prop)
    offer = {"property": prop, "advisor_id": advisor_id, "slots": slots}
    return VisitStep(offer_text(prop, slots, intro), {OFFER_KEY: offer})


async def start(scheduling: SchedulingClient, lead_id: str, prop: Mapping[str, Any] | None,
                today: date) -> VisitStep:
    """El lead pidió la visita: le ofrece horarios del asesor, o le pide su horario preferido."""
    summary = _property(prop)
    if summary is None:
        return ask_preferred(None)
    return await _offer(scheduling, lead_id, summary, today)


async def follow_up(scheduling: SchedulingClient, conversation_id: str, lead_id: str,
                    offer: Mapping[str, Any], text: str, today: date) -> VisitStep:
    """La respuesta del lead a la oferta: reserva el horario que eligió, o vuelve a preguntar."""
    prop = offer.get("property")
    if offer.get("preferred_time"):
        if not _WHEN.search(_normalize(text)):
            return VisitStep(PREFERRED_AGAIN, {OFFER_KEY: dict(offer)})
        return VisitStep(PREFERRED_THANKS, {VISIT_KEY: {"property": prop, "preferred_time": text.strip()}},
                         settled=True)
    slots = list(offer.get("slots") or [])
    chosen = choose_slot(text, slots)
    if chosen is None:
        intro = "No me quedó claro cuál horario prefieres. Estos son los que tengo:"
        return VisitStep(offer_text(prop, slots, intro), {OFFER_KEY: dict(offer)})
    try:
        visit = await scheduling.book(f"{conversation_id}:{chosen}", lead_id, prop["id"],
                                      offer["advisor_id"], chosen)
    except SchedulingError as exc:
        if exc.code == "SLOT_TAKEN":
            return await _offer(scheduling, lead_id, prop, today,
                                "Ese horario se acaba de ocupar. Estos siguen libres:")
        return ask_preferred(prop)
    booked = {"property": prop, "visit_id": visit.get("visit_id"), "scheduled_at": visit.get("scheduled_at", chosen)}
    reply = (f"Listo, tu visita a {prop['title']} quedó agendada para el {slot_label(chosen)}. "
             "Tu asesor te contactará para confirmar los detalles.")
    return VisitStep(reply, {VISIT_KEY: booked}, settled=True, booked=True)
