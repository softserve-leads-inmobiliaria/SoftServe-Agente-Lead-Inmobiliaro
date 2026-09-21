"""Un turno completo (A-1): responder, mover la FSM y persistir el nuevo estado en Postgres."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from app.fakes.fake_llm import FakeLLM
from app.fsm import next_state, trigger_for
from app.lead_scoring import TurnEvidence, score_turn
from app.scoring import Score
from app.skeleton import Catalog, respond
from app.state_store import StateStore
from db.session import session_scope


@dataclass(frozen=True)
class Transition:
    before: str
    after: str
    trigger: str | None
    score: Score


def advance(engine: Engine, event: Mapping[str, Any], intent: str,
            evidence: TurnEvidence | None = None) -> Transition:
    """Carga y bloquea el estado, aplica el trigger, puntúa y guarda, todo en una transacción."""
    conversation_id = uuid.UUID(str(event["conversation_id"]))
    lead_id = uuid.UUID(str(event["lead_id"]))
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
        store.touch(conversation_id)
        return Transition(before=state.fsm_state, after=after, trigger=trigger, score=score)


async def process_turn(engine: Engine, event: Mapping[str, Any], catalog: Catalog) -> dict[str, Any]:
    # La respuesta se arma primero, sin transacción abierta: no se sostiene un bloqueo de base de
    # datos mientras se espera al catálogo por red.
    reply = await respond(event, catalog)
    evidence = TurnEvidence(raw_signal=FakeLLM().extract_signal(event["text"]), tool_results=reply.tool_results)
    await run_in_threadpool(advance, engine, event, reply.intent, evidence)
    return reply.outbound
