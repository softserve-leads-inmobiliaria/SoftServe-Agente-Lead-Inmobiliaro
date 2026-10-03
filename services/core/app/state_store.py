"""Estado de la conversación en Postgres (A-1, DT-1, DT-11): nunca en memoria.

`Conversation.fsm_state` guarda el estado; `fsm_context` el resto del contexto y `lead_scores` el
último score. Juntos son el contrato `FsmState` (contracts/fsm_state.schema.json).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sqlalchemy import case, func, literal, select, update
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.orm import Session

from app import jobs
from app.scoring import Score
from db.models import (
    AuditLog,
    Conversation,
    FsmContext,
    FunnelEvent,
    Lead,
    LeadScore,
    Message,
    Property,
    PropertyInterest,
    UnmetSearch,
)

logger = logging.getLogger("core.state_store")


def funnel_event_type(fsm_state: str) -> str:
    """Nombre del evento del funnel cuando una conversación entra a `fsm_state`."""
    return f"state.{fsm_state}"


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
    unresolved_streak: int = 0  # interno (A-13): no forma parte del contrato FsmState

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
    fsm_state: str
    unresolved_streak: int
    lead_qualified: bool = False
    history: tuple[tuple[str, str], ...] = ()  # (rol, contenido) de los últimos mensajes, del más viejo al más nuevo
    last_tool_results: Mapping[str, Any] = field(default_factory=dict)  # los del turno anterior (B-8)


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
            unresolved_streak=context.unresolved_streak,
            score_signal=context.score_signal,
            score=None if score is None else {
                "lead_id": str(score.lead_id), "need_fit": score.need_fit, "budget": score.budget,
                "authority": score.authority, "timing": score.timing,
                "eligibility": score.eligibility, "total": score.total, "band": score.band,
                "reasons": list(score.reasons),
            },
        )

    def save_streak(self, conversation_id: uuid.UUID, streak: int) -> None:
        self._session.execute(update(FsmContext).where(FsmContext.conversation_id == conversation_id)
                              .values(unresolved_streak=streak))

    def save_state(self, conversation_id: uuid.UUID, fsm_state: str) -> bool:
        """Guarda el estado y, si de verdad cambió, registra la transición en `funnel_events` (OBS-2).

        Una fila por transición real, no por turno: guardar el mismo estado no escribe nada. Todo
        pasa por aquí, así que ninguna transición (mensaje, *scheduler*) se queda sin registrar, y el
        evento va en la misma transacción que el cambio: o se guardan los dos o ninguno.
        """
        lead_id = self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.fsm_state != fsm_state)
            .values(fsm_state=fsm_state).returning(Conversation.lead_id)).scalar()
        if lead_id is None:
            return False
        self._session.add(FunnelEvent(lead_id=lead_id, event_type=funnel_event_type(fsm_state)))
        return True

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

    def guard_context(self, conversation_id: uuid.UUID, lead_id: uuid.UUID,
                      history_limit: int = 20) -> GuardContext:
        """Consentimiento del lead y cuántos mensajes fuera de dominio lleva la conversación."""
        consent = self._session.execute(select(Lead.consent_status).where(Lead.id == lead_id)).scalar()
        out_of_domain = self._session.execute(
            select(func.count()).select_from(AuditLog)
            .where(AuditLog.action == "input.out_of_domain",
                   AuditLog.payload["conversation_id"].astext == str(conversation_id))).scalar()
        state = self._session.execute(
            select(Conversation.fsm_state, FsmContext.unresolved_streak, FsmContext.lead_qualified,
                   FsmContext.last_tool_results)
            .join(FsmContext, FsmContext.conversation_id == Conversation.id)
            .where(Conversation.id == conversation_id)).one_or_none()
        return GuardContext(consent_status=consent or "pending", out_of_domain_count=out_of_domain or 0,
                            fsm_state=state.fsm_state if state else "GREETING",
                            unresolved_streak=state.unresolved_streak if state else 0,
                            lead_qualified=state.lead_qualified if state else False,
                            history=self.history(conversation_id, history_limit),
                            last_tool_results=(state.last_tool_results or {}) if state else {})

    def history(self, conversation_id: uuid.UUID, limit: int) -> tuple[tuple[str, str], ...]:
        """Los últimos `limit` mensajes de la conversación (solo lead y asistente), en orden cronológico."""
        rows = self._session.execute(
            select(Message.role, Message.content)
            .where(Message.conversation_id == conversation_id, Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)).all()
        return tuple((row.role, row.content) for row in reversed(rows))

    def add_messages(self, conversation_id: uuid.UUID,
                     messages: Sequence[tuple[str, str, list[dict[str, Any]] | None]]) -> None:
        """Guarda los mensajes del turno. `clock_timestamp()` da una marca distinta a cada fila aunque
        vayan en la misma transacción, así el orden se conserva."""
        for role, content, tool_calls in messages:
            self._session.add(Message(conversation_id=conversation_id, role=role, content=content,
                                      tool_calls=tool_calls or None, created_at=func.clock_timestamp()))

    def qualify(self, conversation_id: uuid.UUID, lead_id: uuid.UUID) -> bool:
        """Marca al lead como calificado (`qualified_at`, `lead_qualified=true`) una sola vez (B-7).

        La guarda de idempotencia es `qualified_at IS NULL`, no `status` (#89): B-6 puede mover
        `status` a `sql` (lead reclamado a mano) antes de que el score cruce a WARM/HOT, y una
        guarda sobre `status='nuevo'` dejaría de disparar para siempre en ese caso. `status` solo
        avanza a `mql` si seguía en `nuevo`; si ya está en `sql` (o más adelante), se respeta.
        """
        claimed = self._session.execute(
            update(Lead).where(Lead.id == lead_id, Lead.qualified_at.is_(None))
            .values(status=case((Lead.status == "nuevo", "mql"), else_=Lead.status),
                   qualified_at=func.now(), updated_at=func.now())
            .returning(Lead.id)).scalar()
        if claimed is None:
            return False
        self._session.execute(update(FsmContext).where(FsmContext.conversation_id == conversation_id)
                              .values(lead_qualified=True))
        return True

    def mark_visit_scheduled(self, lead_id: uuid.UUID) -> None:
        """`Lead.status` pasa a `visit_scheduled` al reservar la visita (B-8, DT-32); un lead ya cerrado no
        retrocede."""
        self._session.execute(update(Lead).where(Lead.id == lead_id, Lead.status.in_(("nuevo", "mql", "sql")))
                              .values(status="visit_scheduled", updated_at=func.now()))

    def set_zone_if_unknown(self, lead_id: uuid.UUID, zone: str) -> None:
        """Completa `Lead.zone` la primera vez que se sabe, y no la pisa después (#81).

        Es lo que hace que el lead aparezca en la cola de un asesor: el panel filtra por esta
        columna. `WHERE zone IS NULL` deja la decisión en la base, sin leer antes de escribir.
        """
        self._session.execute(update(Lead).where(Lead.id == lead_id, Lead.zone.is_(None))
                              .values(zone=zone, updated_at=func.now()))

    def record_interest(self, lead_id: uuid.UUID, interactions: Sequence[tuple[str, str]]) -> None:
        """Una fila de `property_interest` por cada `(propiedad, tipo)` del turno (N-4). Sin texto del lead.

        `interest.interactions` ya quitó los repetidos del turno. Una propiedad que no está en `properties`
        no se registra y queda un `warning`: el catálogo lee la misma base, así que solo pasa con un catálogo
        de prueba, y no tiene por qué tumbar el turno. Cualquier otra falla sí lo tumba, como el resto de
        `advance`.
        """
        for property_id, kind in interactions:
            written = self._session.execute(insert(PropertyInterest).from_select(
                ["lead_id", "property_id", "kind"],
                select(literal(lead_id, UUID(as_uuid=True)), Property.id, literal(kind))
                .where(Property.id == property_id)).returning(PropertyInterest.id)).scalar()
            if written is None:
                logger.warning("interés no registrado: la propiedad no está en el inventario",
                               extra={"property_id": property_id, "kind": kind})

    def set_campaign_if_unknown(self, lead_id: uuid.UUID, campaign: str) -> None:
        """Guarda la campaña del primer contacto (`?start=camp_…`, N-5). Una campaña posterior no la pisa:
        cuenta la que trajo al lead. `WHERE utm_campaign IS NULL` deja la decisión en la base."""
        self._session.execute(update(Lead).where(Lead.id == lead_id, Lead.utm_campaign.is_(None))
                              .values(utm_campaign=campaign, updated_at=func.now()))

    def record_unmet_searches(self, searches: Sequence[Mapping[str, Any]]) -> None:
        """Una fila de `unmet_searches` por búsqueda sin resultados del turno (N-5): solo los filtros, sin
        lead ni texto. `demand.SearchLog` ya quitó las repetidas del turno."""
        for filters in searches:
            self._session.add(UnmetSearch(**filters))

    def withdraw_consent(self, lead_id: uuid.UUID) -> None:
        """La baja también cancela todo lo que el *scheduler* tenía pendiente para el lead (A-17)."""
        self._session.execute(update(Lead).where(Lead.id == lead_id)
                              .values(consent_status="withdrawn", updated_at=func.now()))
        jobs.cancel_for_lead(self._session, lead_id)

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
