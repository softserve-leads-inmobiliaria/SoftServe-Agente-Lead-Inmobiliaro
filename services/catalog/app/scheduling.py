"""Agenda (C-2): `assign_advisor`, `get_slots` y `book` sobre Postgres (`advisors`, `leads`, `visits`).

No son tools MCP: `core` las llama desde código por HTTP (`app/scheduling_api.py`) y el LLM nunca las
ve (DT-32). Cada método devuelve un dict que cumple el `outputSchema` de
contracts/scheduling/<operación>.schema.json o lanza `SchedulingError` con uno de sus códigos.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import Advisor, Lead, Property, Visit

# Horario de atención de los asesores, en hora de Guadalajara. México no tiene horario de verano
# desde 2022, así que un desfase fijo basta y no depende de que la imagen traiga la base tzdata.
LOCAL_TZ = timezone(timedelta(hours=-6), "America/Mexico_City")
WORKDAYS = range(0, 6)  # lunes (0) a sábado (5)
FIRST_SLOT, LAST_SLOT = 9, 17  # el último bloque empieza a las 17:00 y termina a las 18:00
SLOT_LENGTH = timedelta(hours=1)
DEFAULT_DAYS = 7
DEFAULT_LANGUAGE = "es"
MATCH_ORDER = ("zone_and_language", "zone", "language", "any")


class SchedulingError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"error_code": self.error_code, "message": self.message}


def _is_slot_start(moment: datetime) -> bool:
    local = moment.astimezone(LOCAL_TZ)
    return (local.weekday() in WORKDAYS and FIRST_SLOT <= local.hour <= LAST_SLOT
            and (local.minute, local.second, local.microsecond) == (0, 0, 0))


def _lead(db: Session, lead_id: str) -> Lead:
    try:
        key = uuid.UUID(lead_id)
    except (TypeError, ValueError):
        raise SchedulingError("INVALID_INPUT", "lead_id debe ser un UUID.") from None
    lead = db.get(Lead, key)
    if lead is None:
        raise SchedulingError("NOT_FOUND", f"No existe el lead {lead_id}.")
    return lead


def _advisor(db: Session, advisor_id: str) -> Advisor:
    advisor = db.get(Advisor, advisor_id)
    if advisor is None:
        raise SchedulingError("NOT_FOUND", f"No existe el asesor {advisor_id}.")
    return advisor


def _visit_dict(visit: Visit, created: bool) -> dict[str, Any]:
    return {"visit_id": str(visit.id), "lead_id": str(visit.lead_id), "property_id": visit.property_id,
            "advisor_id": visit.advisor_id,
            "scheduled_at": visit.scheduled_at.astimezone(LOCAL_TZ).isoformat(), "created": created}


class SchedulingService:
    def __init__(
        self,
        db_session_factory: Callable[[], Session],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._db_session_factory = db_session_factory
        self._now = now

    def assign_advisor(self, lead_id: str, zone: str | None = None,
                       language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
        """Asesor del lead. Si ya tiene uno lo devuelve; si no, elige y lo deja escrito.

        Preferencia: zona e idioma, solo zona, solo idioma, cualquiera. Dentro del grupo gana quien
        tiene menos leads asignados (desempate por id): así los leads se reparten por turnos entre
        los asesores de una zona en lugar de ir todos al primero.
        """
        with self._db_session_factory() as db:
            lead = _lead(db, lead_id)
            if lead.advisor_id is not None:
                return self._assignment(lead, _advisor(db, lead.advisor_id), "existing")

            zone = zone or lead.zone
            load = (select(Lead.advisor_id, func.count().label("leads")).where(Lead.advisor_id.is_not(None))
                    .group_by(Lead.advisor_id).subquery())
            advisors = db.execute(
                select(Advisor).outerjoin(load, load.c.advisor_id == Advisor.id)
                .order_by(func.coalesce(load.c.leads, 0), Advisor.id)).scalars().all()
            if not advisors:
                raise SchedulingError("NOT_FOUND", "No hay asesores dados de alta.")

            def fits(advisor: Advisor, group: str) -> bool:
                same_zone = zone is not None and advisor.zone.casefold() == zone.casefold()
                same_language = advisor.language == language
                return {"zone_and_language": same_zone and same_language, "zone": same_zone,
                        "language": same_language, "any": True}[group]

            match, chosen = next((group, a) for group in MATCH_ORDER for a in advisors if fits(a, group))

            # Solo escribe si nadie lo asignó en el medio: dos llamadas simultáneas dejan un solo asesor.
            written = db.execute(update(Lead).where(Lead.id == lead.id, Lead.advisor_id.is_(None))
                                 .values(advisor_id=chosen.id, updated_at=func.now())).rowcount
            db.commit()
            if not written:
                db.refresh(lead)
                return self._assignment(lead, _advisor(db, lead.advisor_id), "existing")
            return self._assignment(lead, chosen, match)

    @staticmethod
    def _assignment(lead: Lead, advisor: Advisor, match: str) -> dict[str, Any]:
        return {"lead_id": str(lead.id), "advisor_id": advisor.id, "name": advisor.name,
                "zone": advisor.zone, "language": advisor.language, "match": match}

    def get_slots(self, advisor_id: str, date_from: str, days: int = DEFAULT_DAYS) -> dict[str, Any]:
        """Bloques libres del asesor desde `date_from`, sin los pasados ni los ya reservados."""
        try:
            first_day = date.fromisoformat(date_from)
        except (TypeError, ValueError):
            raise SchedulingError("INVALID_INPUT", "date_from debe ser una fecha AAAA-MM-DD.") from None
        if not 1 <= days <= 14:
            raise SchedulingError("INVALID_INPUT", "days debe estar entre 1 y 14.")

        now = self._now()
        first_day = max(first_day, now.astimezone(LOCAL_TZ).date())
        start = datetime.combine(first_day, time(0), LOCAL_TZ)
        end = start + timedelta(days=days)
        with self._db_session_factory() as db:
            _advisor(db, advisor_id)
            taken = set(db.execute(select(Visit.scheduled_at).where(
                Visit.advisor_id == advisor_id, Visit.scheduled_at >= start, Visit.scheduled_at < end)).scalars())

        slots = []
        for offset in range(days):
            day = first_day + timedelta(days=offset)
            if day.weekday() not in WORKDAYS:
                continue
            for hour in range(FIRST_SLOT, LAST_SLOT + 1):
                slot = datetime.combine(day, time(hour), LOCAL_TZ)
                if slot > now and slot not in taken:
                    slots.append(slot.isoformat())
        return {"advisor_id": advisor_id, "slots": slots}

    def book(self, idempotency_key: str, lead_id: str, property_id: str, advisor_id: str,
             scheduled_at: str) -> dict[str, Any]:
        """Reserva la visita. Un reintento con la misma `idempotency_key` devuelve la misma visita."""
        try:
            moment = datetime.fromisoformat(scheduled_at)
        except (TypeError, ValueError):
            raise SchedulingError("INVALID_INPUT", "scheduled_at debe ser una fecha y hora ISO 8601.") from None
        if moment.tzinfo is None:
            raise SchedulingError("INVALID_INPUT", "scheduled_at debe incluir su desfase horario.")

        with self._db_session_factory() as db:
            lead = _lead(db, lead_id)
            # El reintento se resuelve antes que cualquier otra regla: aunque el horario ya haya
            # pasado o la propiedad haya cambiado, la visita ya existe y se devuelve igual.
            existing = self._by_key(db, idempotency_key)
            if existing is not None:
                return self._retry(existing, lead.id, property_id, advisor_id, moment)

            _advisor(db, advisor_id)
            prop = db.get(Property, property_id)
            if prop is None:
                raise SchedulingError("NOT_FOUND", f"No existe la propiedad {property_id}.")
            if prop.status != "disponible":
                raise SchedulingError("INVALID_INPUT", f"La propiedad {property_id} no está disponible.")
            if not _is_slot_start(moment) or moment <= self._now():
                raise SchedulingError("INVALID_INPUT", "scheduled_at no es un horario de atención futuro.")
            if db.execute(select(Visit.id).where(Visit.advisor_id == advisor_id,
                                                 Visit.scheduled_at == moment)).first():
                raise SchedulingError("SLOT_TAKEN", "El asesor ya tiene una visita a esa hora.")

            visit = Visit(lead_id=lead.id, property_id=property_id, advisor_id=advisor_id,
                          scheduled_at=moment, idempotency_key=idempotency_key)
            db.add(visit)
            try:
                db.commit()
            except IntegrityError:
                # Otra reserva ganó la carrera: o es el mismo reintento o es otra visita a esa hora.
                db.rollback()
                existing = self._by_key(db, idempotency_key)
                if existing is not None:
                    return self._retry(existing, lead.id, property_id, advisor_id, moment)
                raise SchedulingError("SLOT_TAKEN", "El asesor ya tiene una visita a esa hora.") from None
            db.refresh(visit)
            return _visit_dict(visit, created=True)

    @staticmethod
    def _by_key(db: Session, idempotency_key: str) -> Visit | None:
        return db.execute(select(Visit).where(Visit.idempotency_key == idempotency_key)).scalar_one_or_none()

    @staticmethod
    def _retry(visit: Visit, lead_id: uuid.UUID, property_id: str, advisor_id: str,
               moment: datetime) -> dict[str, Any]:
        if (visit.lead_id, visit.property_id, visit.advisor_id, visit.scheduled_at) != (
                lead_id, property_id, advisor_id, moment):
            raise SchedulingError("INVALID_INPUT", "La idempotency_key ya se usó para otra visita.")
        return _visit_dict(visit, created=False)
