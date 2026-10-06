"""Resumen de la conversación para el asesor (A-16): qué busca el lead, sus objeciones y el siguiente paso.

Se genera una vez, en el turno en que la FSM llega a `HANDOFF` o `HUMAN_HANDOFF`, después del commit y
antes de la ficha de traspaso (A-12), que lo lleva. Se guarda en `leads.handoff_summary` y el panel
(B-5) lo muestra antes de la transcripción.

- Con el agente de Bedrock lo redacta el LLM; con el *fake* (o cualquier agente sin `summarize`) sale
  de una plantilla determinista con los datos del lead.
- Pasa por los guardrails de salida (A-5, A-6, A-7) y no puede mencionar categorías protegidas
  (`guardrails.md` §1). Lo que vale como fuente es la transcripción guardada (ya sin PII ni categorías
  protegidas) y los datos del lead. Si algo no pasa, se descarta: no se regenera.
- Si el LLM falla o tarda más de `SUMMARY_TIMEOUT_SECONDS`, la ficha sale sin resumen, como antes.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import Engine, func, select, update
from starlette.concurrency import run_in_threadpool

from app import address_guard, factuality, promise_guard
from app.channels.notifications import HANDOFF_REASONS, transcript_of
from app.input_guard import describes_protected, normalize
from app.interest import property_of_interest
from app.scoring import URGENCY_TEXT
from app.visits import VISIT_KEY, slot_label
from db.models import FsmContext, Lead, Visit
from db.session import session_scope

logger = logging.getLogger("core.summary")

# Mensajes más recientes que ve el LLM: una conversación larga no puede disparar el costo del resumen.
SUMMARY_MAX_MESSAGES = 60
# El turno del traspaso espera el resumen antes de contestarle al lead: no puede colgarlo.
SUMMARY_TIMEOUT_SECONDS = 15.0

# Las claves son las del JSON que devuelve el LLM; las etiquetas, lo que lee el asesor (#202).
SECTIONS: tuple[tuple[str, str], ...] = (
    ("busca", "Looking for"),
    ("objeciones", "Objections"),
    ("siguiente_paso", "Next step"),
)
# "Agent" es el asesor: el que conversa con el lead es el asistente.
_ROLE_LABELS = {"user": "Lead", "assistant": "Assistant"}

NO_OBJECTIONS_TEMPLATE = "Not detected: the assistant can't identify them without the LLM. Check the transcript."
_NEXT_STEPS = {
    "solicitud_humano": "Contact the lead: they asked to talk to a person.",
    "fuera_de_alcance": "Review their request: it's outside what the assistant handles.",
    "frustration": "Pick up the conversation: the assistant didn't resolve several turns in a row.",
    "visit_scheduled": "Confirm the scheduled visit.",
    "visit_requested": "Coordinate the visit time: the calendar couldn't book it.",
}

# La transcripción ya viene sin categorías protegidas, pero el resumen habla del lead en tercera
# persona: "his wife" no lo cubre el guardrail de entrada, que mira "my wife". "their partner" y "their
# family" son las formas neutras que deja el guardrail (DT-40), así que no cuentan. Sigue el español de
# antes ("su esposa"), por las conversaciones guardadas en español.
_THIRD_PERSON_FAMILY = re.compile(
    r"\b(?:(?:his|her|their) (?:wives|wife|husbands?|kids|children|sons?|daughters?|girlfriends?|boyfriends?"
    r"|fiancee?s?)|sus? (?:espos[oa]s?|marido|mujer|novi[oa]s?|hij[oa]s?))\b")


@dataclass(frozen=True)
class SummaryFacts:
    """Lo que el código ya sabe del lead: base de la plantilla y fuente válida para los guardrails."""

    reason: str
    lead_qualified: bool = False
    zone: str | None = None
    budget: str | None = None
    timing: str | None = None
    property_title: str | None = None
    visit_at: str | None = None
    preferred_time: str | None = None

    def lines(self) -> list[str]:
        values = [
            ("Handoff reason", HANDOFF_REASONS.get(self.reason, self.reason)),
            ("Area", self.zone), ("Budget", self.budget), ("Timeline", _timing(self.timing)),
            ("Property of interest", self.property_title), ("Scheduled visit", self.visit_at),
            ("Preferred visit time", self.preferred_time),
        ]
        return [f"{label}: {value}" for label, value in values if value]


def _timing(value: str | None) -> str | None:
    """El plazo en inglés si es uno de los de `urgency` ("3_meses" -> "within 3 months")."""
    return URGENCY_TEXT.get(value, value.replace("_", " ")) if value else None


def template(facts: SummaryFacts) -> dict[str, str]:
    """El resumen sin LLM: determinista, solo con los datos del lead."""
    wants = [f"interested in {facts.property_title}" if facts.property_title else None,
             f"area {facts.zone}" if facts.zone else None,
             f"budget {facts.budget}" if facts.budget else None,
             f"timeline {_timing(facts.timing)}" if facts.timing else None]
    wants = [w for w in wants if w]
    busca = "; ".join(wants)
    next_step = _NEXT_STEPS.get(facts.reason, "Contact the lead.")
    if facts.reason == "visit_scheduled" and facts.visit_at:
        next_step = f"Confirm the visit on {facts.visit_at}."
    elif facts.reason == "visit_requested" and facts.preferred_time:
        next_step = f"Coordinate the visit time: the lead prefers {facts.preferred_time}."
    return {
        "busca": f"{busca[:1].upper()}{busca[1:]}." if busca else "They haven't said yet.",
        "objeciones": NO_OBJECTIONS_TEMPLATE,
        "siguiente_paso": next_step,
    }


def render(sections: Mapping[str, str]) -> str:
    return "\n".join(f"{label}: {sections[key]}" for key, label in SECTIONS)


def transcript_text(transcript: Sequence[tuple[str, str]]) -> str:
    recent = transcript[-SUMMARY_MAX_MESSAGES:]
    return "\n".join(f"{_ROLE_LABELS.get(role, role)}: {content}" for role, content in recent)


def violations(text: str, transcript: Sequence[tuple[str, str]], facts: SummaryFacts) -> list[str]:
    """Las clases de lo que el resumen no puede decir. Nunca devuelve el texto detectado."""
    sources = "\n".join([*(content for _, content in transcript), *facts.lines()])
    found = (factuality.check(text, {}, sources)
             + address_guard.check(text, facts.lead_qualified, sources)
             + promise_guard.check(text, {}, sources))
    kinds = [violation.kind for violation in found]
    if describes_protected(text) or _THIRD_PERSON_FAMILY.search(normalize(text)):
        kinds.append("protected_category")
    return kinds


async def summarize(agent: Any, transcript: Sequence[tuple[str, str]], facts: SummaryFacts) -> str | None:
    """El resumen ya validado, o None si no se pudo generar o no pasó los guardrails. Nunca lanza."""
    generate = getattr(agent, "summarize", None)
    try:
        if generate is None:
            sections = template(facts)
        else:
            sections = await asyncio.wait_for(generate(transcript_text(transcript), facts),
                                              SUMMARY_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("no se pudo generar el resumen de la conversación",
                       extra={"error_type": type(exc).__name__})
        return None
    if not sections or any(not str(sections.get(key) or "").strip() for key, _ in SECTIONS):
        logger.warning("resumen incompleto: la ficha sale sin resumen")
        return None
    text = render({key: str(sections[key]).strip() for key, _ in SECTIONS})
    kinds = violations(text, transcript, facts)
    if kinds:
        logger.warning("resumen descartado por los guardrails de salida", extra={"violations": sorted(set(kinds))})
        return None
    return text


def read_facts(session, lead_id: uuid.UUID, conversation_id: uuid.UUID, reason: str) -> SummaryFacts:
    lead = session.get(Lead, lead_id)
    context = session.get(FsmContext, conversation_id)
    last = context.last_tool_results if context else {}
    prop = property_of_interest(last)
    requested = last.get(VISIT_KEY) if isinstance(last.get(VISIT_KEY), Mapping) else {}
    visit = session.execute(
        select(Visit).where(Visit.lead_id == lead_id).order_by(Visit.scheduled_at)).scalars().first()
    return SummaryFacts(
        reason=reason,
        lead_qualified=bool(lead and lead.qualified_at),
        zone=lead.zone if lead else None,
        budget=lead.budget if lead else None,
        timing=lead.timing if lead else None,
        property_title=prop.get("title") if prop else None,
        visit_at=slot_label(visit.scheduled_at.isoformat()) if visit else None,
        preferred_time=requested.get("preferred_time") or None,
    )


def _read(engine: Engine, lead_id: uuid.UUID, conversation_id: uuid.UUID,
          reason: str) -> tuple[tuple[tuple[str, str], ...], SummaryFacts]:
    with session_scope(engine) as session:
        return transcript_of(session, conversation_id), read_facts(session, lead_id, conversation_id, reason)


def _save(engine: Engine, lead_id: uuid.UUID, text: str) -> None:
    with session_scope(engine) as session:
        session.execute(update(Lead).where(Lead.id == lead_id)
                        .values(handoff_summary=text, handoff_summary_at=func.now()))


async def generate(engine: Engine, lead_id: uuid.UUID, conversation_id: uuid.UUID, reason: str,
                   agent: Any) -> str | None:
    """Genera y guarda el resumen del traspaso. Si no sale, el lead queda como estaba."""
    transcript, facts = await run_in_threadpool(_read, engine, lead_id, conversation_id, reason)
    text = await summarize(agent, transcript, facts)
    if text is not None:
        await run_in_threadpool(_save, engine, lead_id, text)
    return text
