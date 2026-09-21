"""Un turno completo: guardrail de entrada, respuesta, FSM y score, persistidos en Postgres.

El orden es: validar el evento -> guardrail de entrada (A-8) -> responder -> mover la FSM, puntuar y
auditar en una sola transacción (A-1, A-9).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from app.fakes.fake_core import FakeCore
from app.fakes.fake_llm import FakeLLM
from app.fsm import next_state, trigger_for
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
from db.session import session_scope

Audit = tuple[str, Mapping[str, Any]]


@dataclass(frozen=True)
class Transition:
    before: str
    after: str
    trigger: str | None
    score: Score


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
            evidence: TurnEvidence | None = None, audits: Sequence[Audit] = ()) -> Transition:
    """Carga y bloquea el estado, aplica el trigger, puntúa, audita y guarda, en una transacción."""
    conversation_id, lead_id = _ids(event)
    with session_scope(engine) as session:
        store = StateStore(session)
        state = store.lock(conversation_id, lead_id)
        trigger = trigger_for(state.fsm_state, intent)
        after = next_state(state.fsm_state, trigger) if trigger else state.fsm_state
        if after != state.fsm_state:
            store.save_state(conversation_id, after)
        evidence = evidence or TurnEvidence()
        signal, score = score_turn(state.score_signal, evidence)
        store.save_scoring(conversation_id, lead_id, signal, dict(evidence.tool_results), score)
        _write_audits(store, event, audits)
        store.touch(conversation_id)
        return Transition(before=state.fsm_state, after=after, trigger=trigger, score=score)


def withdraw(engine: Engine, event: Mapping[str, Any], audits: Sequence[Audit]) -> None:
    """Baja del lead (`/stop`): marca `consent_status=withdrawn` y lo audita. Sin FSM ni score."""
    conversation_id, lead_id = _ids(event)
    with session_scope(engine) as session:
        store = StateStore(session)
        store.lock(conversation_id, lead_id)
        store.withdraw_consent(lead_id)
        _write_audits(store, event, audits)
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


async def process_turn(engine: Engine, event: Mapping[str, Any], catalog: Catalog) -> dict[str, Any]:
    FakeCore().validate(event)  # evento incompleto o no inbound -> ValueError (422)
    verdict = inspect_input(event["text"])
    context = await run_in_threadpool(read_guard_context, engine, event)
    audits = verdict.audit_kinds()

    # /stop: determinista, sin LLM. Un lead ya dado de baja tampoco recibe más flujo.
    if verdict.opt_out:
        await run_in_threadpool(withdraw, engine, event, audits)
        return (await respond(event, catalog, intent="otro", text=OPT_OUT_REPLY)).outbound
    if context.consent_status == "withdrawn":
        return (await respond(event, catalog, intent="otro", text=WITHDRAWN_REPLY)).outbound

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

    evidence = TurnEvidence(raw_signal=_raw_signal(verdict), tool_results=reply.tool_results)
    await run_in_threadpool(advance, engine, event, reply.intent, evidence, audits)
    return outbound
