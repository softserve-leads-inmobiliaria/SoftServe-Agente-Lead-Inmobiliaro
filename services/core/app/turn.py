"""Un turno completo: guardrail de entrada, respuesta, FSM y score, persistidos en Postgres.

El orden es: validar el evento -> guardrail de entrada (A-8) -> responder -> mover la FSM, puntuar y
auditar en una sola transacción (A-1, A-9).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from app.fakes.fake_core import FakeCore
from app.fakes.fake_llm import FakeLLM
from app.fsm import HUMAN_INTENTS, HUMAN_REQUESTED, TERMINAL_STATES, next_state, trigger_for
from app.handoff import FRUSTRATION_REPLY, is_frustrated, next_streak
from app.input_guard import (
    HANDOFF_OFFER,
    OPT_OUT_REPLY,
    OUT_OF_DOMAIN_OFFER_AFTER,
    OUT_OF_DOMAIN_REPLY,
    PII_REMINDER,
    REDACTED,
    WITHDRAWN_REPLY,
    InputVerdict,
    inspect_input,
)
from app.lead_scoring import TurnEvidence, score_turn
from app.scoring import Score
from app.skeleton import Catalog, respond
from app.state_store import GuardContext, StateStore
from app.tracing import SPAN_KIND, get_tracer
from app.transcript import record_turn
from db.session import session_scope

Audit = tuple[str, Mapping[str, Any]]


@dataclass(frozen=True)
class Transition:
    before: str
    after: str
    trigger: str | None
    score: Score
    unresolved_streak: int = 0


def _describe(span: Any, transition: "Transition") -> None:
    """Estado de la FSM y score de un turno, como atributos del span (sin ningún dato del lead)."""
    span.set_attributes({
        "fsm.state_before": transition.before, "fsm.state_after": transition.after,
        "fsm.transitioned": transition.before != transition.after, "fsm.trigger": transition.trigger or "",
        "fsm.unresolved_streak": transition.unresolved_streak,
        "score.total": transition.score.total, "score.band": transition.score.band,
    })


def _ids(event: Mapping[str, Any]) -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.UUID(str(event["conversation_id"])), uuid.UUID(str(event["lead_id"]))


def _write_audits(store: StateStore, event: Mapping[str, Any], audits: Sequence[Audit]) -> None:
    for action, detail in audits:
        store.audit(action, {"conversation_id": str(event["conversation_id"]),
                             "lead_id": str(event["lead_id"]), "event_id": str(event["event_id"]), **detail})


def read_guard_context(engine: Engine, event: Mapping[str, Any]) -> GuardContext:
    conversation_id, lead_id = _ids(event)
    with session_scope(engine) as session:
        return StateStore(session).guard_context(conversation_id, lead_id)


def advance(engine: Engine, event: Mapping[str, Any], intent: str,
            evidence: TurnEvidence | None = None, audits: Sequence[Audit] = (),
            counts_as_unresolved: bool = True, inbound_text: str = "",
            outbound_text: str = "") -> Transition:
    """Carga y bloquea el estado, aplica el trigger, puntúa, audita y guarda, en una transacción.

    `counts_as_unresolved=False` para los turnos que fuerza el guardrail de entrada: no suman a la
    racha de frustración. `inbound_text`/`outbound_text` son el texto ya saneado por el guardrail
    (B-5): se guardan como transcripción en la misma transacción que el estado.
    """
    conversation_id, lead_id = _ids(event)
    with get_tracer().start_as_current_span("fsm.advance") as span, session_scope(engine) as session:
        span.set_attribute(SPAN_KIND, "CHAIN")
        store = StateStore(session)
        state = store.lock(conversation_id, lead_id)
        streak = next_streak(state.unresolved_streak, intent, counts_as_unresolved)
        trigger = trigger_for(state.fsm_state, intent, frustrated=is_frustrated(streak))
        after = next_state(state.fsm_state, trigger) if trigger else state.fsm_state
        if after != state.fsm_state:
            store.save_state(conversation_id, after)
        if trigger == HUMAN_REQUESTED:
            streak = 0  # la racha ya cumplió su función
        store.save_streak(conversation_id, streak)
        evidence = evidence or TurnEvidence()
        signal, score = score_turn(state.score_signal, evidence)
        store.save_scoring(conversation_id, lead_id, signal, dict(evidence.tool_results), score)
        _write_audits(store, event, audits)
        record_turn(session, conversation_id, inbound_text=inbound_text, outbound_text=outbound_text)
        store.touch(conversation_id)
        transition = Transition(before=state.fsm_state, after=after, trigger=trigger, score=score,
                                unresolved_streak=streak)
        _describe(span, transition)
        if trigger == HUMAN_REQUESTED:
            span.set_attribute("fsm.handoff_reason", intent if intent in HUMAN_INTENTS else "frustration")
        span.set_attribute("guardrail.audits", [action for action, _ in audits])
        return transition


def withdraw(engine: Engine, event: Mapping[str, Any], audits: Sequence[Audit],
             inbound_text: str = "", outbound_text: str = "") -> None:
    """Baja del lead (`/stop`): marca `consent_status=withdrawn` y lo audita. Sin FSM ni score."""
    conversation_id, lead_id = _ids(event)
    with session_scope(engine) as session:
        store = StateStore(session)
        store.lock(conversation_id, lead_id)
        store.withdraw_consent(lead_id)
        _write_audits(store, event, audits)
        record_turn(session, conversation_id, inbound_text=inbound_text, outbound_text=outbound_text)
        store.touch(conversation_id)


def _out_of_domain_reply(context: GuardContext) -> str:
    """Respuesta acotada; si el lead insiste, ofrece un asesor (la oferta no mueve la FSM)."""
    insisting = context.out_of_domain_count + 1 >= OUT_OF_DOMAIN_OFFER_AFTER
    return OUT_OF_DOMAIN_REPLY + (HANDOFF_OFFER if insisting else "")


def _raw_signal(verdict: InputVerdict) -> dict[str, Any]:
    if verdict.jailbreak:
        return {}  # un mensaje adversarial no aporta señal al score
    raw = FakeLLM().extract_signal(verdict.signal_text)
    if REDACTED in (raw["budget_hint"] or ""):
        raw["budget_hint"] = None  # un dato sensible omitido no es un presupuesto
    return raw


async def _run_turn(engine: Engine, event: Mapping[str, Any], catalog: Catalog, span: Any) -> dict[str, Any]:
    FakeCore().validate(event)  # evento incompleto o no inbound -> ValueError (422)
    with get_tracer().start_as_current_span("guardrail.input") as guard:
        verdict = inspect_input(event["text"])
        guard.set_attributes({
            SPAN_KIND: "GUARDRAIL", "guardrail.opt_out": verdict.opt_out, "guardrail.jailbreak": verdict.jailbreak,
            "guardrail.pii_types": list(verdict.pii), "guardrail.out_of_domain": verdict.out_of_domain,
            "guardrail.protected_category": verdict.protected_category,
        })
    context = await run_in_threadpool(read_guard_context, engine, event)
    audits = verdict.audit_kinds()

    # /stop: determinista, sin LLM. Un lead ya dado de baja tampoco recibe más flujo.
    if verdict.opt_out:
        span.set_attribute("turn.outcome", "opt_out")
        await run_in_threadpool(withdraw, engine, event, audits, verdict.clean_text, OPT_OUT_REPLY)
        return (await respond(event, catalog, intent="otro", text=OPT_OUT_REPLY)).outbound
    if context.consent_status == "withdrawn":
        span.set_attribute("turn.outcome", "withdrawn")
        return (await respond(event, catalog, intent="otro", text=WITHDRAWN_REPLY)).outbound

    span.set_attribute("turn.outcome", "out_of_domain" if verdict.out_of_domain
                       else "jailbreak" if verdict.jailbreak else "normal")
    clean_event = {**event, "text": verdict.clean_text}
    if verdict.out_of_domain:
        reply = await respond(clean_event, catalog, intent="otro", text=_out_of_domain_reply(context))
    elif verdict.jailbreak:
        # Se contesta como una pregunta de dominio cualquiera: no se refleja la instrucción ni
        # dispara efectos (traspaso a humano, búsquedas).
        reply = await respond(clean_event, catalog, intent="otro")
    else:
        reply = await respond(clean_event, catalog)
    outbound = dict(reply.outbound)
    if verdict.pii:
        outbound["text"] += " " + PII_REMINDER
    counts = not (verdict.jailbreak or verdict.out_of_domain)
    if (counts and context.fsm_state not in TERMINAL_STATES and reply.intent not in HUMAN_INTENTS
            and is_frustrated(next_streak(context.unresolved_streak, reply.intent))):
        outbound["text"] = FRUSTRATION_REPLY  # esta racha dispara el traspaso: se le avisa al lead

    evidence = TurnEvidence(raw_signal=_raw_signal(verdict), tool_results=reply.tool_results)
    span.set_attribute("turn.intent", reply.intent)
    span.set_attribute("turn.tools", sorted(reply.tool_results))
    transition = await run_in_threadpool(advance, engine, event, reply.intent, evidence, audits,
                                         counts, verdict.clean_text, outbound["text"])
    _describe(span, transition)
    return outbound


async def process_turn(engine: Engine, event: Mapping[str, Any], catalog: Catalog) -> dict[str, Any]:
    """Un turno completo dentro de un span `turn`. El span no lleva el texto del mensaje."""
    with get_tracer().start_as_current_span("turn") as span:
        span.set_attributes({
            SPAN_KIND: "CHAIN", "conversation.id": str(event.get("conversation_id", "")),
            "lead.id": str(event.get("lead_id", "")), "event.id": str(event.get("event_id", "")),
            "channel": str(event.get("channel", "")),
        })
        started = time.perf_counter()
        try:
            return await _run_turn(engine, event, catalog, span)
        finally:
            span.set_attribute("turn.latency_ms", round((time.perf_counter() - started) * 1000, 1))
