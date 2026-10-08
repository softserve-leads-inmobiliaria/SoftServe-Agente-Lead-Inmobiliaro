"""Agendar la visita desde la conversación (B-8, DT-39), con la agenda del catálogo (`get_slots`, `book`).

Lo decide el código, no el LLM: la agenda no es una tool MCP (DT-32), así que el modelo nunca ve ni
inventa horarios. Son dos pasos:

1. Cuando el lead pide la visita (`RECOMMEND -> SCHEDULE`), `start` pide el asesor del lead y le ofrece
   los primeros horarios libres, numerados. La oferta queda en `last_tool_results` (`OFFER_KEY`).
2. En `SCHEDULE`, `follow_up` lee la respuesta del lead. Si elige un horario de la oferta, lo reserva con
   `book` y la visita queda resuelta (`SCHEDULE -> HANDOFF`). Si no se entiende cuál, vuelve a ofrecerlos.

Cuándo prefiere el lead ("this weekend", "next Tuesday afternoon", "another day") lo extrae el clasificador
en `visit_when` (#247), en la misma llamada de cada turno: los horarios se filtran con eso y, si ninguno encaja,
se dice con texto fijo y se ofrecen los más cercanos. En la prueba de jurado, "this weekend" ofrecía el jueves y
"another day" contestaba "I didn't quite get which time you prefer".

Respaldo: si la agenda no responde, no hay ningún asesor, no quedan horarios o no se sabe qué
propiedad visitar, se le pide al lead su día y hora preferidos. Su siguiente mensaje se le pasa al
asesor en la ficha de traspaso, que coordina la visita a mano.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from app.markers import STATUS
from app.scheduling_client import SchedulingClient, SchedulingError

LOCAL_TZ = timezone(timedelta(hours=-5))  # hora de Colombia, la misma de la agenda (DT-32, DT-55)
OFFERED_SLOTS = 3
SEARCH_DAYS = 7
PREFERENCE_DAYS = 14  # con un día pedido ("next Tuesday") se mira más adelante; la agenda admite hasta 14

OFFER_KEY = "visit_offer"  # oferta en curso: horarios ofrecidos, o la espera de un horario preferido
VISIT_KEY = "visit"  # visita resuelta: reservada (`visit_id`, `scheduled_at`) o con `preferred_time`
# La hora de la visita reservada, que sigue en los turnos siguientes (`VISIT_KEY` dura solo el turno siguiente).
BOOKED_KEY = "booked_visit"

# Los horarios se muestran en inglés (#202, DT-55); la respuesta del lead se entiende en inglés y en español.
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_ORDINALS = (("primer", 1), ("segund", 2), ("tercer", 3), ("first", 1), ("second", 2), ("third", 3),
             ("1st", 1), ("2nd", 2), ("3rd", 3))
# "pm" suma 12: "at 4 pm" y "4:00 pm" son las 16:00, que es como vienen los horarios de la agenda.
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s?([ap])?\.?m?\b")
_AT_HOUR = re.compile(r"\b(?:a las|at) (\d{1,2})(?:\s?([ap])\.?m\b)?")
_NUMBER = re.compile(r"(?<![\d:])\b(\d{1,2})\b(?![\d:])")
# Algo que parezca un día o una hora: sin esto, un "ok" quedaría como horario preferido.
_WHEN = re.compile(r"\d|\b(hoy|manana|pasado|lunes|martes|miercoles|jueves|viernes|sabado|domingo|semana"
                   r"|tarde|noche|mediodia|temprano"
                   r"|today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|weekend"
                   r"|morning|afternoon|evening|night|noon|early|late)\b")

PREFERRED_ASK = ("I can't book the visit from here right now. What day and time work for you? "
                 "I'll pass it on to your agent so they can confirm it.")
PREFERRED_AGAIN = "What day and time would you like for the visit? I'll pass it on to your agent."
PREFERRED_THANKS = ("Thanks. I'll pass your preferred time on to your agent, who will contact you to "
                    "confirm the visit.")


# `visit_when` del clasificador: los días como los nombra, y las franjas en horas locales [desde, hasta).
_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
PARTS_OF_DAY = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}


def visit_when(raw: Any) -> dict[str, Any] | None:
    """La preferencia de `visit_when` que contestó el clasificador, validada; None si no dice nada usable.

    `{"days": [5, 6], "part": "morning", "other_day": False}`, con los días como `date.weekday()`.
    """
    if not isinstance(raw, Mapping):
        return None
    days = raw.get("days")
    found = sorted({_DAY_KEYS.index(d[:3].lower()) for d in days
                    if isinstance(d, str) and d[:3].lower() in _DAY_KEYS}) if isinstance(days, list) else []
    part = raw.get("part_of_day") if raw.get("part_of_day") in PARTS_OF_DAY else None
    other_day = raw.get("other_day") is True
    return {"days": found, "part": part, "other_day": other_day} if found or part or other_day else None


def _fits(slot: str, when: Mapping[str, Any] | None) -> bool:
    if not when:
        return True
    local = datetime.fromisoformat(slot).astimezone(LOCAL_TZ)
    if when.get("days") and local.weekday() not in when["days"]:
        return False
    since, until = PARTS_OF_DAY.get(when.get("part") or "", (0, 24))
    return since <= local.hour < until


def describe(when: Mapping[str, Any]) -> str:
    """`{"days": [5, 6]}` -> "on the weekend"; `{"days": [1], "part": "afternoon"}` -> "on Tuesday afternoon"."""
    days = list(when.get("days") or [])
    part = when.get("part")
    if days == [5, 6]:
        text = "on the weekend"
    elif days:
        names = [_DAY_NAMES[d] for d in days]
        text = "on " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1])
    else:
        text = "on another day" if when.get("other_day") else ""
    if part:
        text = f"{text} {part}" if days else f"{text} in the {part}"
    return text.strip()


def _hour(hour: str, meridiem: str | None) -> int:
    value = int(hour)
    return value + 12 if meridiem == "p" and value < 12 else value


@dataclass(frozen=True)
class VisitStep:
    """Lo que el código responde en un turno de agenda y lo que deja en `last_tool_results`."""

    reply: str
    tool_results: dict[str, Any] = field(default_factory=dict)
    settled: bool = False  # la visita quedó resuelta: reservada o con un horario preferido para el asesor
    booked: bool = False  # hay una visita real en `visits`
    # Qué contestó la agenda, para el registro del turno (`turn.decision`): "offer", "reoffer", "unclear_choice",
    # "booked", "slot_taken", "ask_preferred", "preferred_again", "preferred_saved", "not_visitable"...
    kind: str = ""


def today_local(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ).date()


def slot_label(slot: str) -> str:
    """`2026-10-06T10:00:00-05:00` -> `Tue Oct 6, 10:00`, en hora local."""
    local = datetime.fromisoformat(slot).astimezone(LOCAL_TZ)
    return f"{_WEEKDAYS[local.weekday()]} {_MONTHS[local.month - 1]} {local.day}, {local:%H:%M}"


def _listing(slots: list[str]) -> str:
    return "\n".join(f"{n}) {slot_label(slot)}" for n, slot in enumerate(slots, start=1))


def offer_text(prop: Mapping[str, Any], slots: list[str], intro: str | None = None) -> str:
    intro = intro or f"Here are the available times to visit {prop['title']} with your agent:"
    return f"{intro}\n{_listing(slots)}\nReply with the number of the one that works for you."


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def choose_slot(text: str, slots: list[str]) -> str | None:
    """El horario de la oferta que eligió el lead, o None si no se entiende cuál (o hay más de uno).

    Acepta la hora ("10:00", "a las 11", "at 4 pm"), el número de la opción ("2", "la 2", "number 2") o el
    ordinal ("la segunda", "the second one").
    """
    normalized = _normalize(text)
    local = [datetime.fromisoformat(slot).astimezone(LOCAL_TZ) for slot in slots]

    def unique(matches: list[str]) -> str | None:
        return matches[0] if len(set(matches)) == 1 else None

    clocks = _CLOCK.findall(normalized)
    if clocks:
        return unique([slot for slot, at in zip(slots, local, strict=True)
                       for hour, minute, meridiem in clocks
                       if (at.hour, at.minute) == (_hour(hour, meridiem), int(minute))])
    hours = _AT_HOUR.findall(normalized)
    if hours:
        return unique([slot for slot, at in zip(slots, local, strict=True)
                       for hour, meridiem in hours if at.hour == _hour(hour, meridiem)])
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


# Lo que se agrega a la respuesta del agente mientras la oferta sigue abierta (#247).
OFFER_REMINDER = "Whenever you're ready, just reply with the number of the time that works for you."
PREFERRED_REMINDER = "Whenever you're ready, tell me what day and time work for you for the visit."


def answers(offer: Mapping[str, Any], text: str, when: Mapping[str, Any] | None = None) -> bool:
    """El mensaje contesta la oferta: elige uno de los horarios, dice cuándo prefiere la visita (`when`), o da un
    día u hora si se pidió el preferido.

    Si no, es otra pregunta ("When will it be available again?") y la contesta el agente (#247): en la prueba de
    jurado, la agenda contestaba a todo "I didn't quite get which time you prefer" y el lead no podía salir."""
    if offer.get("preferred_time"):
        return bool(_WHEN.search(_normalize(text)))
    return bool(when) or choose_slot(text, list(offer.get("slots") or [])) is not None


def reminder(offer: Mapping[str, Any]) -> str:
    return PREFERRED_REMINDER if offer.get("preferred_time") else OFFER_REMINDER


def not_visitable(prop: Mapping[str, Any], status: str) -> str:
    """La propiedad que pidió visitar está apartada o cerrada (#247): lo dice el código, con el estado del catálogo."""
    label = STATUS.get(status, "not available")
    return (f"{prop['title']} is {label} right now, so I can't book a visit to it. "
            "Would you like me to show you similar options that are available?")


def carry_booked(tool_results: Mapping[str, Any], previous: Mapping[str, Any]) -> dict[str, Any]:
    """`tool_results` con la hora de la visita reservada: la de este turno o la que ya se recordaba."""
    visit = tool_results.get(VISIT_KEY)
    if isinstance(visit, Mapping) and isinstance(visit.get("scheduled_at"), str):
        return {**tool_results, BOOKED_KEY: {"scheduled_at": visit["scheduled_at"]}}
    if isinstance(previous.get(BOOKED_KEY), Mapping):
        return {**tool_results, BOOKED_KEY: dict(previous[BOOKED_KEY])}
    return dict(tool_results)


def booked_at(previous: Mapping[str, Any]) -> str | None:
    """La hora de la visita reservada en la conversación, si la hay."""
    for key in (VISIT_KEY, BOOKED_KEY):
        found = previous.get(key)
        if isinstance(found, Mapping) and isinstance(found.get("scheduled_at"), str):
            return found["scheduled_at"]
    return None


def ask_preferred(prop: Mapping[str, Any] | None, kind: str = "ask_preferred") -> VisitStep:
    return VisitStep(PREFERRED_ASK, {OFFER_KEY: {"property": prop, "preferred_time": True}}, kind=kind)


async def _offer(scheduling: SchedulingClient, lead_id: str, prop: dict[str, Any], today: date,
                 intro: str | None = None, when: Mapping[str, Any] | None = None,
                 skip_days: frozenset[date] = frozenset()) -> VisitStep:
    """Ofrece los primeros horarios libres que encajan con `when`. Si ninguno encaja, lo dice y ofrece los más
    cercanos: el texto lo pone el código, no el modelo. `skip_days`: los días ya ofrecidos, si pidió otro."""
    try:
        advisor_id = (await scheduling.assign_advisor(lead_id))["advisor_id"]
        found = await scheduling.get_slots(advisor_id, today.isoformat(), PREFERENCE_DAYS if when else SEARCH_DAYS)
        free = [str(slot) for slot in found["slots"]
                if datetime.fromisoformat(str(slot)).astimezone(LOCAL_TZ).date() not in skip_days]
    except (SchedulingError, KeyError, TypeError, ValueError):
        return ask_preferred(prop, "scheduling_error")
    fitting = [slot for slot in free if _fits(slot, when)]
    slots = (fitting or free)[:OFFERED_SLOTS]
    if not slots:
        return ask_preferred(prop, "no_slots")
    wanted = describe(when) if when else ""
    if wanted and fitting:
        intro = intro or f"Here are the available times {wanted} to visit {prop['title']} with your agent:"
    elif wanted:
        intro = f"I don't have available times {wanted}. These are the closest ones to visit {prop['title']}:"
    offer = {"property": prop, "advisor_id": advisor_id, "slots": slots}
    kind = "closest" if wanted and not fitting else "offer"
    return VisitStep(offer_text(prop, slots, intro), {OFFER_KEY: offer}, kind=kind)


async def start(scheduling: SchedulingClient, lead_id: str, prop: Mapping[str, Any] | None,
                today: date, when: Mapping[str, Any] | None = None) -> VisitStep:
    """El lead pidió la visita: le ofrece horarios del asesor (los que encajan con `when`), o le pide su horario
    preferido."""
    summary = _property(prop)
    if summary is None:
        return ask_preferred(None, "no_property")
    return await _offer(scheduling, lead_id, summary, today, when=when)


async def follow_up(scheduling: SchedulingClient, conversation_id: str, lead_id: str,
                    offer: Mapping[str, Any], text: str, today: date,
                    when: Mapping[str, Any] | None = None) -> VisitStep:
    """La respuesta del lead a la oferta: reserva el horario que eligió, ofrece otros si dijo cuándo prefiere
    (`when`), o vuelve a preguntar. Un horario que no cae en el día que nombró no cuenta como elegido: "Tuesday at
    4 pm" no reserva el jueves a las 16:00."""
    prop = offer.get("property")
    if offer.get("preferred_time"):
        if not _WHEN.search(_normalize(text)):
            return VisitStep(PREFERRED_AGAIN, {OFFER_KEY: dict(offer)}, kind="preferred_again")
        return VisitStep(PREFERRED_THANKS, {VISIT_KEY: {"property": prop, "preferred_time": text.strip()}},
                         settled=True, kind="preferred_saved")
    slots = list(offer.get("slots") or [])
    chosen = choose_slot(text, slots)
    if chosen is not None and when and not _fits(chosen, {"days": when.get("days")}):
        chosen = None
    if chosen is None and when:
        offered = frozenset(datetime.fromisoformat(slot).astimezone(LOCAL_TZ).date() for slot in slots)
        # Los días ofrecidos se saltan solo con "another day" a secas. Con un día o una franja, el mismo día puede
        # servir: "anything in the afternoon?" después de la mañana del lunes saltaba al lunes siguiente (O6).
        just_another_day = when.get("other_day") and not when.get("days") and not when.get("part")
        return await _offer(scheduling, lead_id, prop, today, when=when,
                            skip_days=offered if just_another_day else frozenset())
    if chosen is None:
        intro = "I didn't quite get which time you prefer. These are the ones I have:"
        return VisitStep(offer_text(prop, slots, intro), {OFFER_KEY: dict(offer)}, kind="unclear_choice")
    try:
        visit = await scheduling.book(f"{conversation_id}:{chosen}", lead_id, prop["id"],
                                      offer["advisor_id"], chosen)
    except SchedulingError as exc:
        if exc.code == "SLOT_TAKEN":
            return replace(await _offer(scheduling, lead_id, prop, today,
                                        "That time was just taken. These are still free:"), kind="slot_taken")
        return ask_preferred(prop, "scheduling_error")
    booked = {"property": prop, "visit_id": visit.get("visit_id"), "scheduled_at": visit.get("scheduled_at", chosen)}
    reply = (f"Done, your visit to {prop['title']} is booked for {slot_label(chosen)}. "
             "Your agent will contact you to confirm the details.")
    return VisitStep(reply, {VISIT_KEY: booked}, settled=True, booked=True, kind="booked")
