"""Recordatorio de la visita el día anterior (B-13), como *job* del *scheduler* (A-17, DT-44).

El turno que reserva la visita (B-8) lo programa para las 18:00 del día anterior, en hora de Guadalajara.
Si esa hora ya pasó al reservar (p. ej. una visita para mañana reservada esta noche), no hay recordatorio:
el lead acaba de recibir la confirmación. Uno por visita: su clave es el `visit_id`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app import jobs
from app.visits import LOCAL_TZ

VISIT_REMINDER_KIND = "visit_reminder"
REMINDER_TIME = time(18, 0)  # hora local del día anterior


def reminder_at(scheduled_at: datetime) -> datetime:
    """Las 18:00 del día anterior a la visita, en hora local."""
    day_before = scheduled_at.astimezone(LOCAL_TZ).date() - timedelta(days=1)
    return datetime.combine(day_before, REMINDER_TIME, tzinfo=LOCAL_TZ)


def schedule(session: Session, lead_id: uuid.UUID, conversation_id: uuid.UUID, visit: Mapping[str, Any],
             now: datetime) -> bool:
    """Programa el recordatorio de la visita recién reservada. False si no corresponde o ya existía."""
    visit_id, scheduled = visit.get("visit_id"), visit.get("scheduled_at")
    prop = visit.get("property") or {}
    if not visit_id or not scheduled or not prop.get("id"):
        return False
    run_at = reminder_at(datetime.fromisoformat(str(scheduled)))
    if run_at <= now:
        return False
    return jobs.enqueue(session, VISIT_REMINDER_KIND, str(visit_id), run_at, lead_id=lead_id, once=True,
                        payload={"conversation_id": str(conversation_id), "property_id": prop["id"],
                                 "scheduled_at": str(scheduled)})


def reminder_text(title: str, scheduled_at: datetime, address: str | None) -> str:
    local = scheduled_at.astimezone(LOCAL_TZ)
    where = f", en {address}" if address else ""
    return (f"Te recuerdo tu visita de mañana a {title}: el {local:%d/%m} a las {local:%H:%M}{where}. "
            "Si no puedes ir, avísame por aquí.")
