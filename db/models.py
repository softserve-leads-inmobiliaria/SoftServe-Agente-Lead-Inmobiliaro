"""Modelos SQLAlchemy — fuente de verdad única del esquema (C-3, docs/DESIGN/design.md §1).

Cada servicio importa estos modelos; ninguno mantiene su propia copia. Los cambios de esquema se
hacen aquí y con una migración nueva en db/migrations/ (Alembic): nunca a mano en la base.

Estado de la FSM (DT-1, DT-11): `Conversation.fsm_state` es el único lugar donde vive el estado
actual; `FsmContext` guarda el resto del contexto (lead_qualified, last_tool_results,
score_signal) y `LeadScore` el último score. Juntos son el contrato `FsmState` de CT-3.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Valores permitidos (design.md §1-§3). Los tests los comparan con contracts/fsm_state.schema.json.
FSM_STATES = ("GREETING", "DISCOVERY", "QUALIFY", "RECOMMEND", "SCHEDULE", "HANDOFF",
              "HUMAN_HANDOFF", "NURTURE", "LOST")
LEAD_STATUSES = ("nuevo", "mql", "sql", "visit_scheduled", "closed_won", "closed_lost")
CONSENT_STATUSES = ("pending", "granted", "withdrawn")
PROPERTY_TYPES = ("venta", "renta", "preventa")
PROPERTY_STATUSES = ("disponible", "apartado", "cerrado")
SCORE_BANDS = ("COLD", "WARM", "HOT")
MESSAGE_ROLES = ("user", "assistant", "system", "tool")
NOTIFICATION_KINDS = ("calificado", "traspaso")
# Inteligencia comercial (N-1). Una propiedad colocada queda en `cerrado`; si fue renta o venta lo dice `deals`.
DEAL_KINDS = ("renta", "venta")
INTEREST_KINDS = ("anuncio", "consulta", "busqueda", "visita")
# Tareas diferidas (A-17, DT-44). Cada historia que agregue una clase de *job* la suma aquí y en una migración.
JOB_KINDS = ("no_response",)
JOB_STATUSES = ("pending", "done", "failed", "cancelled")

# Dimensión del vector de la FAQ. Valor de partida (modelos de sentence embeddings habituales usan
# 384); C-5 fija el modelo real (DT-7) y, si cambia, lo ajusta con una migración nueva.
EMBEDDING_DIM = 384


def _one_of(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    listed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({listed})", name=name)


def _null_or_one_of(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    listed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IS NULL OR {column} IN ({listed})", name=name)


class Base(DeclarativeBase):
    # Nombres estables para índices y constraints: Alembic los usa al comparar contra la base.
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


_UUID_PK = dict(primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _synthetic() -> Mapped[bool]:
    """Fila sembrada por la historia sintética de N-9: se distingue y se borra sin tocar datos reales."""
    return mapped_column(server_default=text("false"), nullable=False)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        _one_of("status", LEAD_STATUSES, "ck_leads_status"),
        _one_of("consent_status", CONSENT_STATUSES, "ck_leads_consent_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    # Identidad única por canal (hoy solo Telegram).
    telegram_user_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # phone/email solo se guardan con consentimiento (B-2).
    phone: Mapped[str | None] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String)
    consent_status: Mapped[str] = mapped_column(String, server_default="pending", nullable=False)
    status: Mapped[str] = mapped_column(String, server_default="nuevo", nullable=False)
    zone: Mapped[str | None] = mapped_column(String)
    # Quién recibió la tarjeta al calificar (B-7). Null hasta entonces.
    advisor_id: Mapped[str | None] = mapped_column(ForeignKey("advisors.id"), index=True)
    # Cuándo cruzó a calificado (#89): guarda de idempotencia de StateStore.qualify(), independiente
    # de `status` — B-6 puede mover `status` a `sql` antes de que el score llegue a calificar.
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    budget: Mapped[str | None] = mapped_column(Text)  # texto libre, como budget_hint (design.md §3)
    timing: Mapped[str | None] = mapped_column(Text)
    # Campaña del primer contacto (`?start=camp_…`, N-5). Una campaña posterior no la pisa.
    utm_campaign: Mapped[str | None] = mapped_column(String)
    synthetic: Mapped[bool] = _synthetic()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (_one_of("fsm_state", FSM_STATES, "ck_conversations_fsm_state"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"),
                                               index=True, nullable=False)
    fsm_state: Mapped[str] = mapped_column(String, server_default="GREETING", nullable=False)
    started_at: Mapped[datetime] = _now()
    last_message_at: Mapped[datetime] = _now()


class FsmContext(Base):
    """Contexto de la FSM de una conversación (1:1). El estado en sí vive en Conversation."""

    __tablename__ = "fsm_context"
    __table_args__ = (CheckConstraint("unresolved_streak >= 0", name="ck_fsm_context_unresolved_streak_non_negative"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True)
    # Lo determina la FSM a partir de lead.status, nunca el LLM.
    lead_qualified: Mapped[bool] = mapped_column(server_default=text("false"), nullable=False)
    # Payloads de las tools catalog.* del último turno; se sobrescribe cada turno.
    last_tool_results: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"),
                                                              nullable=False)
    score_signal: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        server_default=text(
            """'{"budget_hint": null, "decision_maker": "no_declarado", "urgency": "no_declarada", """
            """"matching_properties": 0, "meets_requirements": null}'::jsonb"""),
        nullable=False)
    # Turnos seguidos sin resolver (intención `otro`): proxy de frustración (A-13). Lo lleva el código.
    unresolved_streak: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    updated_at: Mapped[datetime] = _now()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        _one_of("role", MESSAGE_ROLES, "ck_messages_role"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


class LeadScore(Base):
    """Último score BANT-E del lead (una fila por lead). Lo calcula una función pura (C-4)."""

    __tablename__ = "lead_scores"
    __table_args__ = (
        *(CheckConstraint(f"{col} BETWEEN 0 AND 20", name=f"ck_lead_scores_{col}")
          for col in ("need_fit", "budget", "authority", "timing", "eligibility")),
        CheckConstraint("total BETWEEN 0 AND 100", name="ck_lead_scores_total"),
        _one_of("band", SCORE_BANDS, "ck_lead_scores_band"),
        CheckConstraint(
            "(band = 'COLD' AND total <= 40) OR (band = 'WARM' AND total BETWEEN 41 AND 70)"
            " OR (band = 'HOT' AND total >= 71)",
            name="ck_lead_scores_band_matches_total"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"),
                                               primary_key=True)
    need_fit: Mapped[int] = mapped_column(Integer, nullable=False)
    budget: Mapped[int] = mapped_column(Integer, nullable=False)
    authority: Mapped[int] = mapped_column(Integer, nullable=False)
    timing: Mapped[int] = mapped_column(Integer, nullable=False)
    eligibility: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    updated_at: Mapped[datetime] = _now()


class Property(Base):
    __tablename__ = "properties"
    __table_args__ = (
        _one_of("type", PROPERTY_TYPES, "ck_properties_type"),
        _one_of("status", PROPERTY_STATUSES, "ck_properties_status"),
        CheckConstraint("price >= 0", name="ck_properties_price"),
        CheckConstraint("bedrooms >= 0", name="ck_properties_bedrooms"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)  # p. ej. "prop_00231"
    title: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    zone: Mapped[str] = mapped_column(String, index=True, nullable=False)
    m2: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    bedrooms: Mapped[int] = mapped_column(Integer, nullable=False)
    bathrooms: Mapped[Decimal] = mapped_column(Numeric(3, 1), nullable=False)
    amenities: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"), nullable=False)
    # Se cambia solo con db/property_history.py, que deja el evento en property_status_events (N-1).
    status: Mapped[str] = mapped_column(String, server_default="disponible", nullable=False)
    address_approx: Mapped[str] = mapped_column(String, nullable=False)
    # Reservada hasta lead_qualified=true (guardrails.md).
    address_exact: Mapped[str] = mapped_column(String, nullable=False)
    photo_url: Mapped[str | None] = mapped_column(String)  # foto en Telegram (B-10)


class Promotion(Base):
    __tablename__ = "promotions"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # p. ej. "promo_001"
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"),
                                             index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)


class Advisor(Base):
    __tablename__ = "advisors"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # p. ej. "adv_001"
    name: Mapped[str] = mapped_column(String, nullable=False)
    zone: Mapped[str] = mapped_column(String, index=True, nullable=False)
    language: Mapped[str] = mapped_column(String, server_default="es", nullable=False)
    # Auth del panel (DT-5): token estático por asesor.
    access_token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # A dónde llega la tarjeta del lead calificado (B-7). Lo completa el asesor al abrir el bot.
    telegram_chat_id: Mapped[str | None] = mapped_column(String, unique=True)


class Visit(Base):
    __tablename__ = "visits"
    __table_args__ = (
        # Idempotencia: un reintento de webhook con la misma clave no crea una visita duplicada.
        UniqueConstraint("idempotency_key", name="uq_visits_idempotency_key"),
        # Un asesor no puede tener dos visitas a la misma hora, ni con dos reservas simultáneas (C-2).
        UniqueConstraint("advisor_id", "scheduled_at", name="uq_visits_advisor_id_scheduled_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"),
                                               index=True, nullable=False)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    advisor_id: Mapped[str] = mapped_column(ForeignKey("advisors.id"), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)


class AdvisorNotification(Base):
    """Aviso al asesor en el panel (B-15): la tarjeta del lead calificado (B-7) o la ficha de traspaso
    (A-12). Se guarda siempre; Telegram es un canal más, solo para el asesor que registró su chat."""

    __tablename__ = "advisor_notifications"
    __table_args__ = (
        _one_of("kind", NOTIFICATION_KINDS, "ck_advisor_notifications_kind"),
        Index("ix_advisor_notifications_advisor_created", "advisor_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    advisor_id: Mapped[str] = mapped_column(ForeignKey("advisors.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)  # el resumen, sin la transcripción
    created_at: Mapped[datetime] = _now()
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PropertyStatusEvent(Base):
    """Historial de estados de una propiedad (N-1): de aquí salen los días en el mercado (N-6).
    Lo escribe solo db/property_history.py, en la misma sesión que cambia `Property.status`."""

    __tablename__ = "property_status_events"
    __table_args__ = (
        _null_or_one_of("from_status", PROPERTY_STATUSES, "ck_property_status_events_from_status"),
        _one_of("to_status", PROPERTY_STATUSES, "ck_property_status_events_to_status"),
        Index("ix_property_status_events_property_changed", "property_id", "changed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String)  # None: el alta de la propiedad
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 index=True, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)  # p. ej. el id del asesor, o "seed"
    synthetic: Mapped[bool] = _synthetic()


class Deal(Base):
    """Operación cerrada (N-3): qué propiedad, con qué lead, qué asesor, renta o venta y a qué precio."""

    __tablename__ = "deals"
    __table_args__ = (
        _one_of("kind", DEAL_KINDS, "ck_deals_kind"),
        CheckConstraint("price >= 0", name="ck_deals_price"),
        Index("ix_deals_property_closed", "property_id", "closed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"),
                                               index=True, nullable=False)
    advisor_id: Mapped[str] = mapped_column(ForeignKey("advisors.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                index=True, nullable=False)
    synthetic: Mapped[bool] = _synthetic()


class PropertyInterest(Base):
    """Qué propiedades vio, consultó o visitó cada lead (N-4). Base de la atribución al bot (DT-41).
    Sin texto del lead: solo el tipo de interacción y la fecha."""

    __tablename__ = "property_interest"
    __table_args__ = (
        _one_of("kind", INTEREST_KINDS, "ck_property_interest_kind"),
        # DT-41: EXISTS con el mismo lead y propiedad dentro de la ventana previa al cierre.
        Index("ix_property_interest_lead_property_created", "lead_id", "property_id", "created_at"),
        Index("ix_property_interest_property_created", "property_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 index=True, nullable=False)
    synthetic: Mapped[bool] = _synthetic()


class UnmetSearch(Base):
    """Búsqueda sin resultados (N-5): los filtros de `search_properties`, sin lead ni texto del lead."""

    __tablename__ = "unmet_searches"
    __table_args__ = (
        _null_or_one_of("type", PROPERTY_TYPES, "ck_unmet_searches_type"),
        CheckConstraint("bedrooms IS NULL OR bedrooms >= 0", name="ck_unmet_searches_bedrooms"),
        CheckConstraint("budget_min IS NULL OR budget_min >= 0", name="ck_unmet_searches_budget_min"),
        CheckConstraint("budget_max IS NULL OR budget_max >= 0", name="ck_unmet_searches_budget_max"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Cada filtro es opcional, como en search_properties.
    zone: Mapped[str | None] = mapped_column(String)
    type: Mapped[str | None] = mapped_column(String)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    budget_min: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    budget_max: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 index=True, nullable=False)
    synthetic: Mapped[bool] = _synthetic()


class FaqEntry(Base):
    __tablename__ = "faq_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)


class ScheduledJob(Base):
    """Tarea diferida (A-17, DT-44): la ejecuta el *scheduler* de `core` cuando vence `run_at`.

    `dedupe_key` identifica al *job* dentro de su clase (para `no_response`, la conversación): no puede
    haber dos pendientes con la misma clave. Los ejecutados, cancelados o fallidos quedan como registro.
    """

    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        _one_of("kind", JOB_KINDS, "ck_scheduled_jobs_kind"),
        _one_of("status", JOB_STATUSES, "ck_scheduled_jobs_status"),
        CheckConstraint("attempts >= 0", name="ck_scheduled_jobs_attempts"),
        Index("ix_scheduled_jobs_due", "run_at", postgresql_where=text("status = 'pending'")),
        Index("uq_scheduled_jobs_pending_key", "kind", "dedupe_key", unique=True,
              postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **_UUID_PK)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String, nullable=False)
    # Null para los *jobs* que no son de un lead (p. ej. el reporte semanal de N-10).
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String, server_default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)  # ejecuciones fallidas
    last_error: Mapped[str | None] = mapped_column(Text)  # el tipo de la excepción, sin datos del lead
    created_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FunnelEvent(Base):
    """Append-only: un trigger (migración 0001) impide UPDATE y DELETE.

    Sin ON DELETE CASCADE a propósito: un lead con eventos no se puede borrar.
    """

    __tablename__ = "funnel_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"),
                                               index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    created_at: Mapped[datetime] = _now()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, index=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"),
                                                    nullable=False)
    created_at: Mapped[datetime] = _now()
