"""A-17: seguimientos y aristas `no_response` (`DISCOVERY -> NURTURE -> LOST`) desde `scheduled_jobs`.

Con reloj falso: cada test le pasa a `run_due` el instante que quiere simular, sin esperas reales.
"""
import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from catalog_stub import stub_call
from sqlalchemy import text

from app import jobs, scheduler
from app.channels import consent
from app.scheduler import (
    FOLLOW_UPS,
    STOP_HINT,
    NurturePolicy,
    Scheduler,
    TelegramChannel,
    follow_up_text,
)
from app.turn import process_turn
from db.repo_root import repo_root
from db.session import session_scope

INBOUND = {key: value for key, value in json.loads(
    (repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8")).items()
    if key not in ("property_id", "utm_campaign")}
POLICY = NurturePolicy(intervals={"DISCOVERY": timedelta(hours=24), "NURTURE": timedelta(days=7)}, follow_ups=2)
HOUR, DAY = timedelta(hours=1), timedelta(days=1)


@pytest.fixture(autouse=True)
def _clean_queue(engine, monkeypatch):
    """`run_due` toma los jobs de toda la base: cada test arranca sin pendientes de los anteriores."""
    monkeypatch.setattr(scheduler, "default_policy", lambda: POLICY)
    with engine.begin() as connection:
        connection.execute(text("UPDATE scheduled_jobs SET status = 'cancelled' WHERE status = 'pending'"))


class Catalog:
    async def call(self, tool, arguments=None):
        return stub_call(tool, arguments)


class Channel:
    def __init__(self, reachable=True, error=None):
        self.sent, self._reachable, self.error = [], reachable, error

    def reachable(self, chat_id):
        return self._reachable

    def send(self, chat_id, text):
        if self.error:
            raise self.error
        self.sent.append((chat_id, text))


class Lead:
    def __init__(self, engine):
        self.engine = engine
        self.conversation_id, self.lead_id = str(uuid.uuid4()), str(uuid.uuid4())

    def say(self, message="Hola"):
        event = INBOUND | {"conversation_id": self.conversation_id, "lead_id": self.lead_id,
                           "event_id": str(uuid.uuid4()), "text": message}
        return asyncio.run(process_turn(self.engine, event, Catalog()))

    def _query(self, sql):
        with self.engine.connect() as connection:
            return connection.execute(text(sql), {"c": self.conversation_id, "l": self.lead_id}).all()

    def state(self):
        return self._query("SELECT fsm_state FROM conversations WHERE id = :c")[0][0]

    def pending(self):
        """(estado, intento, run_at) del seguimiento pendiente, o None."""
        rows = self._query("SELECT payload ->> 'state', (payload ->> 'attempt')::int, run_at FROM scheduled_jobs"
                           " WHERE dedupe_key = CAST(:c AS text) AND status = 'pending'")
        assert len(rows) <= 1
        return tuple(rows[0]) if rows else None

    def jobs(self):
        return self._query("SELECT status, attempts, last_error FROM scheduled_jobs WHERE lead_id = :l"
                           " ORDER BY created_at")

    def follow_ups_saved(self):
        return [row[0] for row in self._query(
            "SELECT content FROM messages WHERE conversation_id = :c AND role = 'assistant'"
            " AND content LIKE '%' || 'STOP.' ORDER BY created_at")]

    def funnel(self):
        return [row[0] for row in self._query("SELECT event_type FROM funnel_events WHERE lead_id = :l ORDER BY id")]


def _run(engine, now, channel=None):
    return Scheduler(engine, channel, POLICY).run_due(now)


# --- programar ---------------------------------------------------------------------------------------

def test_a_lead_message_in_discovery_schedules_the_first_follow_up(engine):
    lead = Lead(engine)
    before = datetime.now(UTC)
    lead.say()
    state, attempt, run_at = lead.pending()
    assert (lead.state(), state, attempt) == ("DISCOVERY", "DISCOVERY", 1)
    assert before + 24 * HOUR - timedelta(minutes=1) < run_at < datetime.now(UTC) + 24 * HOUR


def test_nothing_runs_before_it_is_due(engine):
    lead, channel = Lead(engine), Channel()
    lead.say()
    assert _run(engine, datetime.now(UTC) + 23 * HOUR, channel) == 0
    assert channel.sent == [] and lead.pending()[1] == 1


def test_a_state_without_no_response_edge_schedules_nothing(engine):
    lead = Lead(engine)
    lead.say()
    lead.say("Busco una casa")  # DISCOVERY -> QUALIFY: ahí no hay arista no_response
    assert lead.state() == "QUALIFY" and lead.pending() is None
    assert lead.jobs()[-1][0] == "cancelled"


# --- el ciclo completo -------------------------------------------------------------------------------

def test_two_follow_ups_then_nurture_then_two_more_then_lost(engine):
    lead, channel = Lead(engine), Channel()
    lead.say()
    t0 = datetime.now(UTC)
    for step in (1, 2):
        _run(engine, t0 + step * 24 * HOUR + HOUR, channel)
        assert lead.state() == "DISCOVERY" and lead.pending()[:2] == ("DISCOVERY", step + 1)
    _run(engine, t0 + 73 * HOUR, channel)
    assert lead.state() == "NURTURE" and lead.pending()[:2] == ("NURTURE", 1)
    for step in (1, 2):
        _run(engine, t0 + 73 * HOUR + step * 7 * DAY, channel)
        assert lead.state() == "NURTURE" and lead.pending()[:2] == ("NURTURE", step + 1)
    _run(engine, t0 + 73 * HOUR + 21 * DAY + HOUR, channel)
    assert lead.state() == "LOST" and lead.pending() is None
    expected = [follow_up_text("DISCOVERY", 1), follow_up_text("DISCOVERY", 2),
                follow_up_text("NURTURE", 1), follow_up_text("NURTURE", 2)]
    assert [sent for _, sent in channel.sent] == expected
    assert lead.follow_ups_saved() == expected
    assert lead.funnel()[-2:] == ["state.NURTURE", "state.LOST"]
    assert {status for status, _, _ in lead.jobs()} == {"done"}


def test_a_late_scheduler_does_not_skip_steps(engine):
    """Si `core` estuvo caído una semana, se envía un seguimiento por pasada, no todos de golpe."""
    lead, channel = Lead(engine), Channel()
    lead.say()
    _run(engine, datetime.now(UTC) + 7 * DAY, channel)
    assert len(channel.sent) == 1 and lead.pending()[:2] == ("DISCOVERY", 2)


def test_the_lead_replying_restarts_the_count(engine):
    lead, channel = Lead(engine), Channel()
    lead.say()
    _run(engine, datetime.now(UTC) + 25 * HOUR, channel)
    assert lead.pending()[1] == 2
    lead.say("Hola")  # sigue en DISCOVERY
    assert lead.state() == "DISCOVERY" and lead.pending()[:2] == ("DISCOVERY", 1)


def test_a_lead_returning_from_nurture_goes_back_to_discovery_with_a_fresh_count(engine):
    lead = Lead(engine)
    lead.say()
    t0 = datetime.now(UTC)
    for hours in (25, 49, 73):
        _run(engine, t0 + hours * HOUR, Channel())
    assert lead.state() == "NURTURE"
    lead.say("Hola, sigo buscando")
    assert lead.state() == "DISCOVERY" and lead.pending()[:2] == ("DISCOVERY", 1)


def test_a_lead_without_a_channel_still_counts_the_attempt_but_nothing_is_saved(engine):
    lead, channel = Lead(engine), Channel(reachable=False)
    lead.say()
    _run(engine, datetime.now(UTC) + 25 * HOUR, channel)
    assert channel.sent == [] and lead.follow_ups_saved() == []
    assert lead.pending()[1] == 2


# --- /stop ---------------------------------------------------------------------------------------------

def test_stop_in_the_turn_cancels_the_pending_jobs(engine):
    lead, channel = Lead(engine), Channel()
    lead.say()
    lead.say("/stop")
    assert lead.pending() is None and lead.jobs()[-1][0] == "cancelled"
    assert _run(engine, datetime.now(UTC) + 30 * DAY, channel) == 0 and channel.sent == []


def test_stop_in_the_channel_cancels_the_pending_jobs(engine):
    lead = Lead(engine)
    lead.say()
    consent.withdraw_consent(engine, uuid.UUID(lead.lead_id))
    assert lead.pending() is None


def test_a_withdrawn_lead_is_never_contacted_even_if_a_job_was_left(engine):
    lead, channel = Lead(engine), Channel()
    lead.say()
    with engine.begin() as connection:  # baja sin pasar por `withdraw_consent`
        connection.execute(text("UPDATE leads SET consent_status = 'withdrawn' WHERE id = :l"), {"l": lead.lead_id})
    _run(engine, datetime.now(UTC) + 25 * HOUR, channel)
    assert channel.sent == [] and lead.jobs()[-1][0] == "cancelled" and lead.state() == "DISCOVERY"


# --- reintentos y una sola ejecución -----------------------------------------------------------------

def test_a_failing_job_is_retried_and_then_left_as_failed(engine, caplog):
    lead, broken = Lead(engine), Channel(error=TimeoutError("sin red"))
    lead.say()
    t = datetime.now(UTC) + 25 * HOUR
    with caplog.at_level("WARNING", logger="core.scheduler"):
        _run(engine, t, broken)
        assert lead.jobs()[-1] == ("pending", 1, "TimeoutError")
        assert lead.pending()[1] == 1 and lead.follow_ups_saved() == []  # nada quedó a medias
        assert _run(engine, t + timedelta(seconds=59), broken) == 0  # espera 1 minuto
        _run(engine, t + timedelta(minutes=1), broken)
        _run(engine, t + timedelta(minutes=6), broken)
    assert lead.jobs()[-1] == ("failed", 3, "TimeoutError") and lead.pending() is None
    assert [record.levelname for record in caplog.records if record.name == "core.scheduler"] == [
        "WARNING", "WARNING", "ERROR"]
    assert all("sin red" not in record.getMessage() for record in caplog.records)


def test_a_job_that_fails_once_then_works_is_sent_once(engine):
    lead, channel = Lead(engine), Channel(error=ConnectionError())
    lead.say()
    t = datetime.now(UTC) + 25 * HOUR
    _run(engine, t, channel)
    channel.error = None
    _run(engine, t + timedelta(minutes=1), channel)
    _run(engine, t + timedelta(minutes=2), channel)
    assert len(channel.sent) == 1 and lead.pending()[1] == 2


def test_running_twice_does_not_run_a_job_twice(engine):
    lead, channel = Lead(engine), Channel()
    lead.say()
    t = datetime.now(UTC) + 25 * HOUR
    assert _run(engine, t, channel) >= 1
    _run(engine, t, channel)
    assert len(channel.sent) == 1


def test_a_job_another_scheduler_holds_is_skipped(engine):
    lead, channel = Lead(engine), Channel()
    lead.say()
    with engine.connect() as other:
        with other.begin():
            other.execute(text("SELECT id FROM scheduled_jobs WHERE dedupe_key = :c AND status = 'pending'"
                               " FOR UPDATE"), {"c": lead.conversation_id})
            assert _run(engine, datetime.now(UTC) + 25 * HOUR, channel) == 0
    assert channel.sent == []


def test_concurrent_schedulers_send_each_follow_up_once(engine):
    leads, channel = [Lead(engine) for _ in range(4)], Channel()
    for lead in leads:
        lead.say()
    t = datetime.now(UTC) + 25 * HOUR
    threads = [threading.Thread(target=_run, args=(engine, t, channel)) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(channel.sent) == 4


def test_a_conversation_in_the_middle_of_a_turn_is_left_for_the_next_pass(engine):
    """Sin NOWAIT, el turno (que tiene la conversación y quiere cancelar el job) y el scheduler (que tiene
    el job y quiere la conversación) se bloquearían entre sí."""
    lead, channel = Lead(engine), Channel()
    lead.say()
    t = datetime.now(UTC) + 25 * HOUR
    with engine.connect() as turn:
        with turn.begin():
            turn.execute(text("SELECT id FROM conversations WHERE id = :c FOR UPDATE"), {"c": lead.conversation_id})
            assert _run(engine, t, channel) == 1
    assert channel.sent == [] and lead.jobs()[-1][:2] == ("pending", 0)  # no cuenta como fallo
    _run(engine, t, channel)
    assert len(channel.sent) == 1


# --- piezas ------------------------------------------------------------------------------------------

def test_enqueue_ignores_a_second_pending_job_with_the_same_key(engine):
    lead = Lead(engine)
    lead.say()
    with session_scope(engine) as session:
        assert not jobs.enqueue(session, "no_response", lead.conversation_id, datetime.now(UTC),
                                lead_id=uuid.UUID(lead.lead_id))
    assert lead.pending()[1] == 1


def test_every_follow_up_reminds_how_to_stop():
    for state, texts in FOLLOW_UPS.items():
        for attempt in range(1, len(texts) + 2):
            assert follow_up_text(state, attempt).endswith(STOP_HINT)


def test_the_policy_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("SCHEDULER_DISCOVERY_HOURS", "4")
    monkeypatch.setenv("SCHEDULER_NURTURE_DAYS", "2")
    monkeypatch.setenv("SCHEDULER_FOLLOW_UPS", "1")
    assert NurturePolicy.from_env() == NurturePolicy(
        intervals={"DISCOVERY": 4 * HOUR, "NURTURE": 2 * DAY}, follow_ups=1)


class _Bot:
    pass


@pytest.mark.parametrize("bot,chat_id,reachable", [
    (_Bot(), "123456789", True), (_Bot(), "-1001", True), (_Bot(), str(uuid.uuid4()), False), (None, "123", False)])
def test_telegram_only_reaches_real_chats(bot, chat_id, reachable):
    assert TelegramChannel(asyncio.new_event_loop(), lambda: bot).reachable(chat_id) is reachable


def test_the_scheduler_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("SCHEDULER_POLL_SECONDS", "0")
    assert scheduler.poll_seconds() == 0
