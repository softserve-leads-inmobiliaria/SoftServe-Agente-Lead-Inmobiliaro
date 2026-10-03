"""La cola de tareas diferidas en Postgres (`scheduled_jobs`, A-17, DT-44).

Solo encolar y cancelar, dentro de la transacción de quien llama: un turno reprograma su seguimiento en
la misma transacción que guarda el estado. Quién ejecuta los *jobs* lo decide `app/scheduler.py`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Mapping

from sqlalchemy import ColumnElement, func, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.models import ScheduledJob

_PENDING = text("status = 'pending'")


def enqueue(session: Session, kind: str, dedupe_key: str, run_at: datetime | ColumnElement[Any], *,
            lead_id: uuid.UUID | None = None, payload: Mapping[str, Any] | None = None) -> bool:
    """Programa un *job*. Si ya hay uno pendiente con la misma clase y clave, no hace nada y devuelve False."""
    inserted = session.execute(
        insert(ScheduledJob)
        .values(kind=kind, dedupe_key=dedupe_key, run_at=run_at, lead_id=lead_id, payload=dict(payload or {}))
        .on_conflict_do_nothing(index_elements=["kind", "dedupe_key"], index_where=_PENDING)
        .returning(ScheduledJob.id)).scalar()
    return inserted is not None


def cancel(session: Session, kind: str, dedupe_key: str) -> int:
    """Cancela el pendiente de esa clase y clave, si lo hay."""
    return session.execute(
        update(ScheduledJob)
        .where(ScheduledJob.kind == kind, ScheduledJob.dedupe_key == dedupe_key, ScheduledJob.status == "pending")
        .values(status="cancelled", finished_at=func.now())).rowcount


def cancel_for_lead(session: Session, lead_id: uuid.UUID) -> int:
    """Cancela todo lo pendiente del lead (`/stop`, `guardrails.md` §1)."""
    return session.execute(
        update(ScheduledJob)
        .where(ScheduledJob.lead_id == lead_id, ScheduledJob.status == "pending")
        .values(status="cancelled", finished_at=func.now())).rowcount
