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
from app.input_guard import mentions_protected, normalize
from app.interest import property_of_interest
from app.visits import LOCAL_TZ, VISIT_KEY
from db.models import FsmContext, Lead, Visit
from db.session import session_scope

logger = logging.getLogger("core.summary")

# Mensajes más recientes que ve el LLM: una conversación larga no puede disparar el costo del resumen.
SUMMARY_MAX_MESSAGES = 60
# El turno del traspaso espera el resumen antes de contestarle al lead: no puede colgarlo.
SUMMARY_TIMEOUT_SECONDS = 15.0

SECTIONS: tuple[tuple[str, str], ...] = (
    ("busca", "Qué busca"),
    ("objeciones", "Objeciones"),
    ("siguiente_paso", "Siguiente paso"),
)
_ROLE_LABELS = {"user": "Lead", "assistant": "Agente"}

NO_OBJECTIONS_TEMPLATE = "Sin detectar: el agente sin LLM no las identifica. Revisa la transcripción."
_NEXT_STEPS = {
    "solicitud_humano": "Contactar al lead: pidió hablar con una persona.",
    "fuera_de_alcance": "Revisar su consulta: está fuera del alcance del agente.",
    "frustration": "Retomar la conversación: el agente no resolvió varios turnos seguidos.",
    "visit_scheduled": "Confirmar la visita agendada.",
    "visit_requested": "Coordinar el horario de la visita: la agenda no la pudo reservar.",
}

# La transcripción ya viene sin categorías protegidas, pero el resumen habla del lead en tercera
# persona: "su esposa" no lo cubre el guardrail de entrada, que mira "mi esposa". "su pareja" y "su
# familia" son las formas neutras que deja el guardrail (DT-40), así que no cuentan.
_THIRD_PERSON_FAMILY = re.compile(r"\bsus? (?:espos[oa]s?|marido|mujer|novi[oa]s?|hij[oa]s?)\b")


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
            ("Motivo del traspaso", HANDOFF_REASONS.get(self.reason, self.reason)),
            ("Zona", self.zone), ("Presupuesto", self.budget), ("Plazo", _timing(self.timing)),
            ("Propiedad de interés", self.property_title), ("Visita agendada", self.visit_at),
            ("Horario preferido para la visita", self.preferred_time),
        ]
        return [f"{label}: {value}" for label, value in values if value]


def _timing(value: str | None) -> str | None:
    return value.replace("_", " ") if value else None


def template(facts: SummaryFacts) -> dict[str, str]:
    """El resumen sin LLM: determinista, solo con los datos del lead."""
    wants = [f"le interesa {facts.property_title}" if facts.property_title else None,
             f"zona {facts.zone}" if facts.zone else None,
             f"presupuesto {facts.budget}" if facts.budget else None,
             f"plazo {_timing(facts.timing)}" if facts.timing else None]
    wants = [w for w in wants if w]
    busca = "; ".join(wants)
    next_step = _NEXT_STEPS.get(facts.reason, "Contactar al lead.")
    if facts.reason == "visit_scheduled" and facts.visit_at:
        next_step = f"Confirmar la visita del {facts.visit_at}."
    elif facts.reason == "visit_requested" and facts.preferred_time:
        next_step = f"Coordinar el horario de la visita: el lead prefiere {facts.preferred_time}."
    return {
        "busca": f"{busca[:1].upper()}{busca[1:]}." if busca else "Todavía no lo dijo.",
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
    if mentions_protected(text) or _THIRD_PERSON_FAMILY.search(normalize(text)):
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
        visit_at=visit.scheduled_at.astimezone(LOCAL_TZ).strftime("%d/%m/%Y %H:%M") if visit else None,
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
