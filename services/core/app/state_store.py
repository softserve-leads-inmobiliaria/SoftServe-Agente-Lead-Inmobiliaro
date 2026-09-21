"""Estado de la conversación en Postgres (A-1, DT-1, DT-11): nunca en memoria.

`Conversation.fsm_state` guarda el estado; `fsm_context` el resto del contexto y `lead_scores` el
último score. Juntos son el contrato `FsmState` (contracts/fsm_state.schema.json).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.scoring import Score
from db.models import AuditLog, Conversation, FsmContext, Lead, LeadScore


class ConversationMismatch(ValueError):
    """La conversación ya existe pero pertenece a otro lead."""


@dataclass(frozen=True)
class ConversationState:
    conversation_id: uuid.UUID
    lead_id: uuid.UUID
    fsm_state: str
    lead_qualified: bool
    last_tool_results: dict[str, Any]
    score_signal: dict[str, Any]
    score: dict[str, Any] | None

    def to_contract(self) -> dict[str, Any]:
        """El estado tal como lo describe el contrato FsmState (CT-3)."""
        return {
            "conversation_id": str(self.conversation_id),
            "lead_id": str(self.lead_id),
            "fsm_state": self.fsm_state,
            "lead_qualified": self.lead_qualified,
            "last_tool_results": self.last_tool_results,
            "score_signal": self.score_signal,
            "score": self.score,
        }


@dataclass(frozen=True)
class GuardContext:
    """Lo que el guardrail de entrada necesita saber antes de responder."""

    consent_status: str
    out_of_domain_count: int


class StateStore:
    """Lee y escribe el estado dentro de la transacción de la `Session` recibida."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock(self, conversation_id: uuid.UUID, lead_id: uuid.UUID) -> ConversationState:
        """Devuelve el estado de la conversación (creándola si es nueva) y bloquea su fila.

        El bloqueo (`FOR UPDATE`) dura hasta el commit: dos mensajes simultáneos de la misma
        conversación se procesan uno detrás de otro, no pisándose.
        """
        self._ensure_rows(conversation_id, lead_id)
        row = self._session.execute(
            select(Conversation, FsmContext)
            .join(FsmContext, FsmContext.conversation_id == Conversation.id)
            .where(Conversation.id == conversation_id)
            .with_for_update(of=Conversation)
        ).one()
        conversation, context = row
        if conversation.lead_id != lead_id:
            raise ConversationMismatch(f"la conversación {conversation_id} pertenece a otro lead")
        score = self._session.get(LeadScore, lead_id)
        return ConversationState(
            conversation_id=conversation.id,
            lead_id=conversation.lead_id,
            fsm_state=conversation.fsm_state,
            lead_qualified=context.lead_qualified,
            last_tool_results=context.last_tool_results,
            score_signal=context.score_signal,
            score=None if score is None else {
                "lead_id": str(score.lead_id), "need_fit": score.need_fit, "budget": score.budget,
                "authority": score.authority, "timing": score.timing,
                "eligibility": score.eligibility, "total": score.total, "band": score.band,
                "reasons": list(score.reasons),
            },
        )

    def save_state(self, conversation_id: uuid.UUID, fsm_state: str) -> None:
        self._session.execute(update(Conversation).where(Conversation.id == conversation_id)
                              .values(fsm_state=fsm_state))

    def save_scoring(self, conversation_id: uuid.UUID, lead_id: uuid.UUID, signal: Mapping[str, Any],
                     tool_results: Mapping[str, Any], score: Score) -> None:
        """Guarda la señal acumulada, los resultados de tools del turno y el score (una fila por lead)."""
        self._session.execute(update(FsmContext).where(FsmContext.conversation_id == conversation_id)
                              .values(score_signal=dict(signal), last_tool_results=dict(tool_results)))
        values = {"need_fit": score.need_fit, "budget": score.budget, "authority": score.authority,
                  "timing": score.timing, "eligibility": score.eligibility, "total": score.total,
                  "band": score.band, "reasons": list(score.reasons)}
        self._session.execute(insert(LeadScore).values(lead_id=lead_id, **values)
                              .on_conflict_do_update(index_elements=[LeadScore.lead_id],
                                                     set_=values | {"updated_at": func.now()}))

    def guard_context(self, conversation_id: uuid.UUID, lead_id: uuid.UUID) -> GuardContext:
        """Consentimiento del lead y cuántos mensajes fuera de dominio lleva la conversación."""
        consent = self._session.execute(select(Lead.consent_status).where(Lead.id == lead_id)).scalar()
        out_of_domain = self._session.execute(
            select(func.count()).select_from(AuditLog)
            .where(AuditLog.action == "input.out_of_domain",
                   AuditLog.payload["conversation_id"].astext == str(conversation_id))).scalar()
        return GuardContext(consent_status=consent or "pending", out_of_domain_count=out_of_domain or 0)

    def withdraw_consent(self, lead_id: uuid.UUID) -> None:
        self._session.execute(update(Lead).where(Lead.id == lead_id)
                              .values(consent_status="withdrawn", updated_at=func.now()))

    def audit(self, action: str, payload: Mapping[str, Any]) -> None:
        """Registra una detección del guardrail. El `payload` no debe llevar el contenido del mensaje."""
        self._session.add(AuditLog(actor="input_guard", action=action, payload=dict(payload)))

    def touch(self, conversation_id: uuid.UUID) -> None:
        """Marca actividad: `last_message_at` en la conversación y `updated_at` en su contexto."""
        self._session.execute(update(Conversation).where(Conversation.id == conversation_id)
                              .values(last_message_at=func.now()))
        self._session.execute(update(FsmContext).where(FsmContext.conversation_id == conversation_id)
                              .values(updated_at=func.now()))

    def _ensure_rows(self, conversation_id: uuid.UUID, lead_id: uuid.UUID) -> None:
        # ON CONFLICT DO NOTHING: si dos primeros mensajes llegan a la vez, ninguno falla.
        # `telegram_user_id` es provisional: el canal (B-3) crea al lead con su id real antes de
        # que el evento llegue al core; esto solo cubre eventos de lead desconocido (p. ej. el arnés).
        self._session.execute(insert(Lead).values(id=lead_id, telegram_user_id=str(lead_id))
                              .on_conflict_do_nothing())
        self._session.execute(insert(Conversation).values(id=conversation_id, lead_id=lead_id)
                              .on_conflict_do_nothing())
        self._session.execute(insert(FsmContext).values(conversation_id=conversation_id)
                              .on_conflict_do_nothing())
