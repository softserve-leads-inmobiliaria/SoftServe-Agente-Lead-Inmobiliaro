"""*Scheduler* de tareas diferidas (A-17, DT-3, DT-44): lo que el sistema hace sin que el lead escriba.

Corre dentro de `core` (DT-3): un bucle en el `lifespan` que cada `SCHEDULER_POLL_SECONDS` llama a
`Scheduler.run_due`. Los *jobs* viven en Postgres (`scheduled_jobs`, `app/jobs.py`).

Un *job* se ejecuta una sola vez: se toma con `FOR UPDATE SKIP LOCKED`, y su efecto en la base y su
`done` van en la misma transacción. Si falla, se revierte todo y se reintenta más tarde; al tercer fallo
queda en `failed`. El mensaje al lead se envía al final, justo antes del commit: si el commit fallara
después de un envío, el reintento lo repetiría (el único caso de duplicado, ver DT-44).

Clases de *job*:
- `no_response`: los seguimientos y las aristas `no_response` de la FSM (`DISCOVERY -> NURTURE -> LOST`,
  design.md §2). Cada mensaje del lead reinicia la cuenta.
- `visit_reminder`: el recordatorio de la visita el día anterior (B-13, `app/reminders.py`).
- `alert_scan` y `property_alert`: las alertas de propiedades nuevas (A-18, `app/alerts.py`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Callable, Mapping, Protocol

from psycopg.errors import LockNotAvailable
from sqlalchemy import ColumnElement, Engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import address_guard, alerts, factuality, jobs, promise_guard, reminders
from app.fsm import NO_RESPONSE, next_state
from app.state_store import StateStore
from db.models import Conversation, Lead, Property, PropertyAlert, ScheduledJob
from db.session import session_scope

logger = logging.getLogger("core.scheduler")

NO_RESPONSE_KIND = "no_response"
MAX_ATTEMPTS = 3  # ejecuciones fallidas antes de dejar el job en `failed`
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5))  # espera tras el 1.er y el 2.º fallo

STOP_HINT = "Si prefieres que no te escriba más, responde STOP."
# Seguimientos por estado, sin LLM: el intento N usa el texto N (el último se repite si N es mayor).
FOLLOW_UPS: Mapping[str, tuple[str, ...]] = {
    "DISCOVERY": (
        "¡Hola! ¿Sigues buscando propiedad? Si me cuentas la zona y tu presupuesto, te muestro opciones.",
        "Por si te sirve: puedo buscarte casas o departamentos en venta o renta. Escríbeme cuando quieras retomar.",
    ),
    "NURTURE": (
        "Hola de nuevo. Si todavía buscas propiedad, escríbeme y retomamos tu búsqueda.",
        "Este es mi último mensaje por ahora: cuando quieras retomar la búsqueda, escríbeme por aquí.",
    ),
}


@dataclass(frozen=True)
class NurturePolicy:
    """Cuánto silencio antes de cada intento, por estado, y cuántos seguimientos antes de `no_response`."""

    intervals: Mapping[str, timedelta]
    follow_ups: int

    @classmethod
    def from_env(cls) -> NurturePolicy:
        return cls(intervals={
            "DISCOVERY": timedelta(hours=float(os.environ.get("SCHEDULER_DISCOVERY_HOURS", "24"))),
            "NURTURE": timedelta(days=float(os.environ.get("SCHEDULER_NURTURE_DAYS", "7"))),
        }, follow_ups=int(os.environ.get("SCHEDULER_FOLLOW_UPS", "2")))


@lru_cache
def default_policy() -> NurturePolicy:
    return NurturePolicy.from_env()


def follow_up_text(state: str, attempt: int) -> str:
    texts = FOLLOW_UPS[state]
    return f"{texts[min(attempt, len(texts)) - 1]}\n\n{STOP_HINT}"


def restart_silence(session: Session, conversation_id: uuid.UUID, lead_id: uuid.UUID, fsm_state: str,
                    policy: NurturePolicy | None = None, now: datetime | None = None) -> None:
    """El lead escribió: cancela el seguimiento pendiente y, si `fsm_state` tiene arista `no_response`,
    programa el primer intento. Va en la transacción del turno."""
    policy = policy or default_policy()
    jobs.cancel(session, NO_RESPONSE_KIND, str(conversation_id))
    if fsm_state in policy.intervals:
        _schedule_attempt(session, conversation_id, lead_id, fsm_state, 1, policy,
                          now if now is not None else func.now())


def _schedule_attempt(session: Session, conversation_id: uuid.UUID, lead_id: uuid.UUID, state: str,
                      attempt: int, policy: NurturePolicy, now: datetime | ColumnElement[Any]) -> None:
    jobs.enqueue(session, NO_RESPONSE_KIND, str(conversation_id), now + policy.intervals[state],
                 lead_id=lead_id, payload={"conversation_id": str(conversation_id), "state": state,
                                           "attempt": attempt})


class Channel(Protocol):
    """Por dónde le llega un seguimiento al lead. `reachable` False: no se envía ni se guarda nada."""

    def reachable(self, chat_id: str) -> bool: ...

    def send(self, chat_id: str, text: str) -> None: ...


class _Busy(Exception):
    """La conversación está en medio de un turno: el job queda para la próxima pasada, sin contar como fallo."""


class Scheduler:
    def __init__(self, engine: Engine, channel: Channel | None = None, policy: NurturePolicy | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC), batch_size: int = 50) -> None:
        self._engine = engine
        self._channel = channel
        self._policy = policy or default_policy()
        self._clock = clock
        self._batch_size = batch_size
        self._handlers = {NO_RESPONSE_KIND: self._no_response, reminders.VISIT_REMINDER_KIND: self._visit_reminder,
                          alerts.ALERT_SCAN_KIND: self._alert_scan, alerts.PROPERTY_ALERT_KIND: self._property_alert}

    def run_due(self, now: datetime | None = None) -> int:
        """Ejecuta los jobs vencidos a `now` (el reloj del scheduler si no se pasa). Devuelve cuántos tomó."""
        now = now or self._clock()
        busy: list[uuid.UUID] = []
        taken = 0
        while taken < self._batch_size and self._run_one(now, busy):
            taken += 1
        return taken

    def _run_one(self, now: datetime, busy: list[uuid.UUID]) -> bool:
        session = Session(self._engine, expire_on_commit=False)
        job_id = None
        try:
            job = session.execute(
                select(ScheduledJob)
                .where(ScheduledJob.status == "pending", ScheduledJob.run_at <= now, ScheduledJob.id.not_in(busy))
                .order_by(ScheduledJob.run_at).limit(1).with_for_update(skip_locked=True)).scalar_one_or_none()
            if job is None:
                session.rollback()
                return False
            job_id = job.id
            # Primero se cierra este job: así el siguiente intento puede ocupar su misma clave.
            job.status, job.finished_at = "done", now
            session.flush()
            job.status = self._handlers[job.kind](session, job, now)
            session.commit()
            return True
        except _Busy:
            session.rollback()
            busy.append(job_id)
            return True
        except Exception as exc:
            session.rollback()
            if job_id is None:
                raise
            self._record_failure(job_id, exc, now)
            return True
        finally:
            session.close()

    def _record_failure(self, job_id: uuid.UUID, exc: Exception, now: datetime) -> None:
        with session_scope(self._engine) as session:
            job = session.get(ScheduledJob, job_id, with_for_update=True)
            if job is None or job.status != "pending":
                return
            job.attempts += 1
            job.last_error = type(exc).__name__  # el texto de la excepción podría traer datos del lead
            extra = {"job_id": str(job_id), "job_kind": job.kind, "job_attempts": job.attempts,
                     "job_error": job.last_error}
            if job.attempts >= MAX_ATTEMPTS:
                job.status, job.finished_at = "failed", now
                logger.error("job fallido sin más reintentos", extra=extra)
            else:
                job.run_at = now + RETRY_DELAYS[job.attempts - 1]
                logger.warning("job fallido: se reintenta", extra=extra)

    # --- no_response ---------------------------------------------------------------------------------

    @staticmethod
    def _lock_conversation(session: Session, conversation_id: uuid.UUID) -> Any:
        """Bloquea la conversación y devuelve su estado y el lead. `_Busy` si un turno la tiene.

        NOWAIT: un turno en curso tiene la conversación y espera a este job para cancelarlo. Esperar aquí
        sería un deadlock; se suelta el job y el turno lo cancela.
        """
        try:
            return session.execute(
                select(Conversation.fsm_state, Lead.consent_status, Lead.telegram_user_id, Lead.qualified_at)
                .join(Lead, Lead.id == Conversation.lead_id).where(Conversation.id == conversation_id)
                .with_for_update(of=Conversation, nowait=True)).one_or_none()
        except OperationalError as exc:
            if isinstance(exc.orig, LockNotAvailable):
                raise _Busy from exc
            raise

    def _no_response(self, session: Session, job: ScheduledJob, now: datetime) -> str:
        state, attempt = job.payload["state"], int(job.payload["attempt"])
        conversation_id = uuid.UUID(job.payload["conversation_id"])
        row = self._lock_conversation(session, conversation_id)
        if row is None or row.consent_status == "withdrawn" or row.fsm_state != state:
            return "cancelled"  # el lead se dio de baja, o la conversación ya no está donde estaba
        store = StateStore(session)
        extra = {"job_id": str(job.id), "conversation_id": str(conversation_id), "fsm_state": state,
                 "attempt": attempt}
        if attempt <= self._policy.follow_ups:
            _schedule_attempt(session, conversation_id, job.lead_id, state, attempt + 1, self._policy, now)
            if self._channel is not None and self._channel.reachable(row.telegram_user_id):
                text = follow_up_text(state, attempt)
                store.add_messages(conversation_id, [("assistant", text, None)])
                session.flush()
                self._channel.send(row.telegram_user_id, text)  # lo último antes del commit
                logger.info("seguimiento enviado", extra=extra)
            else:
                logger.info("seguimiento sin canal: cuenta como intento", extra=extra)
            return "done"
        after = next_state(state, NO_RESPONSE)
        store.save_state(conversation_id, after)
        if after in self._policy.intervals:
            _schedule_attempt(session, conversation_id, job.lead_id, after, 1, self._policy, now)
        logger.info("sin respuesta: cambia de estado", extra=extra | {"fsm_state_after": after})
        return "done"

    # --- visit_reminder --------------------------------------------------------------------------------

    def _visit_reminder(self, session: Session, job: ScheduledJob, now: datetime) -> str:
        """No cambia la FSM, así que no bloquea la conversación: solo lee al lead y la propiedad al enviar."""
        scheduled_at = datetime.fromisoformat(job.payload["scheduled_at"])
        row = session.execute(
            select(Lead.consent_status, Lead.telegram_user_id, Lead.qualified_at, Property.title,
                   Property.address_approx, Property.address_exact)
            .join(Property, Property.id == job.payload["property_id"]).where(Lead.id == job.lead_id)).one_or_none()
        if row is None or row.consent_status == "withdrawn" or scheduled_at <= now:
            return "cancelled"
        extra = {"job_id": str(job.id), "visit_id": job.dedupe_key}
        if self._channel is None or not self._channel.reachable(row.telegram_user_id):
            logger.info("recordatorio sin canal", extra=extra)
            return "done"
        # A-6: la dirección exacta solo para un lead calificado; si no, la aproximada.
        address = row.address_exact if row.qualified_at is not None else row.address_approx
        text = reminders.reminder_text(row.title, scheduled_at, address)
        StateStore(session).add_messages(uuid.UUID(job.payload["conversation_id"]), [("assistant", text, None)])
        session.flush()
        self._channel.send(row.telegram_user_id, text)  # lo último antes del commit
        logger.info("recordatorio de visita enviado", extra=extra)
        return "done"

    # --- alertas de propiedades nuevas (A-18) ------------------------------------------------------------

    def _alert_scan(self, session: Session, job: ScheduledJob, now: datetime) -> str:
        """Encola un `property_alert` por cada propiedad nueva que cumpla una alerta activa, y se vuelve a
        programar mientras quede alguna alerta activa."""
        alerts.expire(session, now)
        for match in alerts.new_matches(session, now):
            jobs.enqueue(session, alerts.PROPERTY_ALERT_KIND, f"{match.alert_id}:{match.property_id}", now,
                         lead_id=match.lead_id, once=True,
                         payload={"alert_id": str(match.alert_id), "property_id": match.property_id,
                                  "conversation_id": str(match.conversation_id)})
        if alerts.any_active(session, now):
            jobs.enqueue(session, alerts.ALERT_SCAN_KIND, alerts.SCAN_KEY, now + alerts.scan_every())
        return "done"

    def _property_alert(self, session: Session, job: ScheduledJob, now: datetime) -> str:
        conversation_id = uuid.UUID(job.payload["conversation_id"])
        lead = self._lock_conversation(session, conversation_id)  # escribe la propiedad recordada
        alert = session.get(PropertyAlert, uuid.UUID(job.payload["alert_id"]))
        prop = session.get(Property, job.payload["property_id"])
        if (lead is None or lead.consent_status == "withdrawn" or alert is None or not alert.active
                or alert.expires_at <= now or prop is None or prop.status != "disponible"):
            return "cancelled"
        found = {"id": prop.id, "title": prop.title, "price": float(prop.price), "type": prop.type,
                 "zone": prop.zone, "bedrooms": prop.bedrooms, "status": prop.status}
        text = f"{alerts.alert_text(found)}\n\n{STOP_HINT}"
        # A-18: el aviso pasa por los guardrails de salida, como una respuesta del LLM (A-5, A-6, A-7).
        tool_results = {"get_property": found}
        violations = [*factuality.check(text, tool_results), *address_guard.check(text, lead.qualified_at is not None),
                      *promise_guard.check(text, tool_results)]
        extra = {"job_id": str(job.id), "alert_id": str(alert.id), "property_id": prop.id}
        if violations:
            logger.warning("alerta bloqueada por el guardrail de salida",
                           extra=extra | {"violations": [violation.kind for violation in violations]})
            return "cancelled"
        if self._channel is None or not self._channel.reachable(lead.telegram_user_id):
            logger.info("alerta sin canal", extra=extra)
            return "done"
        store = StateStore(session)
        store.add_messages(conversation_id, [("assistant", text, None)])
        # Si el lead contesta "me interesa", el turno siguiente sabe de qué propiedad habla.
        store.remember(conversation_id, {key: found[key] for key in ("id", "title", "zone")})
        session.flush()
        self._channel.send(lead.telegram_user_id, text)  # lo último antes del commit
        logger.info("alerta de propiedad nueva enviada", extra=extra)
        return "done"


# --- en producción -----------------------------------------------------------------------------------

class TelegramChannel:
    """Envía por el bot de `core` (B-1) desde el hilo del scheduler, en el event loop del servidor."""

    def __init__(self, loop: asyncio.AbstractEventLoop, get_bot: Callable[[], Any], timeout: float = 30.0):
        self._loop = loop
        self._get_bot = get_bot
        self._timeout = timeout

    def reachable(self, chat_id: str) -> bool:
        # Un lead del arnés no tiene chat de Telegram: su id provisional es un UUID (DT-12).
        return self._get_bot() is not None and chat_id.lstrip("-").isdigit()

    def send(self, chat_id: str, text: str) -> None:
        coroutine = self._get_bot().send_message(chat_id=int(chat_id), text=text)
        asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(timeout=self._timeout)


def poll_seconds() -> float:
    """`SCHEDULER_POLL_SECONDS=0` apaga el scheduler."""
    return float(os.environ.get("SCHEDULER_POLL_SECONDS", "30"))


async def run_forever(scheduler: Scheduler, every: float) -> None:
    """El bucle del `lifespan`. Espera antes de la primera pasada: arrancar `core` no dispara nada."""
    while True:
        await asyncio.sleep(every)
        try:
            await run_in_threadpool(scheduler.run_due)
        except Exception:
            logger.exception("falló una pasada del scheduler")
