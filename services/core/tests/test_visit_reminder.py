"""B-13: recordatorio de la visita el día anterior, como *job* del *scheduler* de A-17. Con reloj falso."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from catalog_stub import PROPERTY
from sqlalchemy import text
from test_visits_turn import Chat, ContractAgenda

from app import reminders, scheduling_client
from app.scheduler import Scheduler
from app.visits import LOCAL_TZ
from db.session import session_scope

APPROX, EXACT = "Col. Providencia, Guadalajara", "Av. Providencia 1234"


class Channel:
    def __init__(self, reachable=True):
        self.sent, self._reachable = [], reachable

    def reachable(self, chat_id):
        return self._reachable

    def send(self, chat_id, text):
        self.sent.append(text)


class Agenda(ContractAgenda):
    """Horarios a 3 días de hoy: los de `ContractAgenda` son fechas fijas que algún día quedan en el pasado."""

    def __init__(self, days=3):
        super().__init__()
        start = datetime.now(LOCAL_TZ).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=days)
        self.slots = [(start + timedelta(hours=hour)).isoformat() for hour in range(3)]

    async def get_slots(self, advisor_id, date_from, days=7):
        body = {"advisor_id": advisor_id, "date_from": date_from, "days": days}
        return self._call("get_slots", body, {"advisor_id": advisor_id, "slots": list(self.slots)})


@pytest.fixture(scope="module", autouse=True)
def _property(engine):
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO properties (id, title, price, type, kind, zone, m2, bedrooms, bathrooms, address_approx,"
            " address_exact) VALUES (:id, :title, 1, 'renta', 'departamento', 'Providencia', 1, 1, 1, :approx, :exact)"
            " ON CONFLICT (id) DO NOTHING"), {"id": PROPERTY["id"], "title": PROPERTY["title"],
                                             "approx": APPROX, "exact": EXACT})


@pytest.fixture(autouse=True)
def _clean_queue(engine):
    with engine.begin() as connection:
        connection.execute(text("UPDATE scheduled_jobs SET status = 'cancelled' WHERE status = 'pending'"))


@pytest.fixture
def agenda(monkeypatch):
    fake = Agenda()
    monkeypatch.setattr(scheduling_client, "get_scheduling", lambda: fake)
    return fake


def _book(engine, choice="la 2"):
    chat = Chat(engine)
    chat.to_schedule()
    chat.say(choice)
    assert chat.state == "HANDOFF"
    return chat


def _reminders(engine, chat):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT status, run_at, dedupe_key, payload FROM scheduled_jobs WHERE kind = 'visit_reminder'"
            " AND lead_id = :l ORDER BY created_at"), {"l": chat.lead_id}).all()


def _saved(engine, chat):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT content FROM messages WHERE conversation_id = :c AND content LIKE 'Te recuerdo%'"),
            {"c": chat.conversation_id}).scalars().all()


def _run(engine, now, channel):
    return Scheduler(engine, channel).run_due(now)


# --- programar ---------------------------------------------------------------------------------------

def test_booking_a_visit_schedules_one_reminder_the_evening_before(engine, agenda):
    chat = _book(engine)
    [(status, run_at, visit_id, payload)] = _reminders(engine, chat)
    visit = datetime.fromisoformat(agenda.slots[1])
    with engine.connect() as connection:
        booked_id = connection.execute(text(
            "SELECT last_tool_results -> 'visit' ->> 'visit_id' FROM fsm_context WHERE conversation_id = :c"),
            {"c": chat.conversation_id}).scalar()
    assert status == "pending" and visit_id == booked_id
    assert run_at == datetime.combine(visit.date() - timedelta(days=1), reminders.REMINDER_TIME, tzinfo=LOCAL_TZ)
    assert payload == {"conversation_id": chat.conversation_id, "property_id": PROPERTY["id"],
                       "scheduled_at": agenda.slots[1]}


def test_a_preferred_time_without_booking_has_no_reminder(engine, monkeypatch):
    class Down(Agenda):
        async def get_slots(self, advisor_id, date_from, days=7):
            raise scheduling_client.SchedulingError("UPSTREAM_UNAVAILABLE", "caída")

    monkeypatch.setattr(scheduling_client, "get_scheduling", lambda: Down())
    chat = Chat(engine)
    chat.to_schedule()
    chat.say("el martes a las 10")
    assert _reminders(engine, chat) == []


@pytest.mark.parametrize("visit,expected", [
    ("2026-10-06T10:00:00-06:00", "2026-10-05T18:00:00-06:00"),
    ("2026-10-06T00:30:00-06:00", "2026-10-05T18:00:00-06:00"),
    ("2026-10-06T23:30:00-06:00", "2026-10-05T18:00:00-06:00"),
    ("2026-10-06T03:00:00+00:00", "2026-10-04T18:00:00-06:00"),  # en hora local es el 5 a las 21:00
])
def test_the_reminder_goes_at_six_pm_local_the_day_before(visit, expected):
    assert reminders.reminder_at(datetime.fromisoformat(visit)) == datetime.fromisoformat(expected)


def test_a_visit_booked_after_the_reminder_time_gets_none(engine):
    visit = {"visit_id": str(uuid.uuid4()), "scheduled_at": "2026-10-06T10:00:00-06:00", "property": PROPERTY}
    late = datetime.fromisoformat("2026-10-05T19:00:00-06:00")
    with session_scope(engine) as session:
        assert not reminders.schedule(session, uuid.uuid4(), uuid.uuid4(), visit, late)


# --- enviar --------------------------------------------------------------------------------------------

def test_the_reminder_has_date_time_and_approximate_address(engine, agenda):
    chat, channel = _book(engine), Channel()
    run_at = _reminders(engine, chat)[0].run_at
    assert _run(engine, run_at - timedelta(seconds=1), channel) == 0 and channel.sent == []
    _run(engine, run_at, channel)
    visit = datetime.fromisoformat(agenda.slots[1])
    [sent] = channel.sent
    assert f"{visit:%d/%m}" in sent and f"{visit:%H:%M}" in sent and PROPERTY["title"] in sent
    assert APPROX in sent and EXACT not in sent
    assert _saved(engine, chat) == [sent] and _reminders(engine, chat)[0].status == "done"


def test_a_qualified_lead_gets_the_exact_address(engine, agenda):
    chat, channel = _book(engine), Channel()
    with engine.begin() as connection:
        connection.execute(text("UPDATE leads SET qualified_at = now() WHERE id = :l"), {"l": chat.lead_id})
    _run(engine, _reminders(engine, chat)[0].run_at, channel)
    assert EXACT in channel.sent[0] and APPROX not in channel.sent[0]


def test_it_is_sent_once_per_visit(engine, agenda):
    chat, channel = _book(engine), Channel()
    [(_, run_at, visit_id, payload)] = _reminders(engine, chat)
    _run(engine, run_at, channel)
    _run(engine, run_at + timedelta(hours=1), channel)
    visit = {"visit_id": visit_id, "scheduled_at": payload["scheduled_at"], "property": PROPERTY}
    with session_scope(engine) as session:  # p. ej. el mismo `book` idempotente devuelto otra vez
        assert not reminders.schedule(session, uuid.UUID(chat.lead_id), uuid.UUID(chat.conversation_id), visit,
                                      datetime.now(UTC))
    assert len(channel.sent) == 1 and len(_reminders(engine, chat)) == 1


def test_a_lead_without_a_channel_gets_nothing_saved(engine, agenda):
    chat, channel = _book(engine), Channel(reachable=False)
    _run(engine, _reminders(engine, chat)[0].run_at, channel)
    assert channel.sent == [] and _saved(engine, chat) == [] and _reminders(engine, chat)[0].status == "done"


# --- baja ----------------------------------------------------------------------------------------------

def test_stop_cancels_the_reminder(engine, agenda):
    chat, channel = _book(engine), Channel()
    run_at = _reminders(engine, chat)[0].run_at
    chat.say("/stop")
    assert _reminders(engine, chat)[0].status == "cancelled"
    _run(engine, run_at, channel)
    assert channel.sent == []


def test_a_withdrawn_lead_is_not_reminded_even_if_the_job_was_left(engine, agenda):
    chat, channel = _book(engine), Channel()
    with engine.begin() as connection:
        connection.execute(text("UPDATE leads SET consent_status = 'withdrawn' WHERE id = :l"), {"l": chat.lead_id})
    _run(engine, _reminders(engine, chat)[0].run_at, channel)
    assert channel.sent == [] and _reminders(engine, chat)[0].status == "cancelled"


def test_a_visit_already_past_is_not_reminded(engine, agenda):
    chat, channel = _book(engine), Channel()
    visit = datetime.fromisoformat(agenda.slots[1])
    with engine.begin() as connection:  # p. ej. `core` estuvo caído desde la víspera hasta después de la visita
        connection.execute(text("UPDATE scheduled_jobs SET run_at = :t WHERE lead_id = :l"),
                           {"t": visit - timedelta(hours=1), "l": chat.lead_id})
    _run(engine, visit + timedelta(minutes=1), channel)
    assert channel.sent == [] and _reminders(engine, chat)[0].status == "cancelled"
