"""N-10: el reporte semanal al gerente, desde los *jobs* del scheduler (A-17) con las consultas de N-6.

Con reloj falso: cada test le pasa a `run_due` el instante que quiere simular, sin esperas reales.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app import weekly_report
from app.scheduler import MAX_ATTEMPTS, Scheduler
from app.weekly_report import (
    BUSINESS_PATH,
    PLAN_KIND,
    REPORT_KIND,
    ReportConfig,
    next_run,
    report_text,
    reported_week,
    week_label,
)
from db.metrics.inventory import (
    LOCAL_TZ,
    PlacedGroup,
    Placements,
    StagnantProperty,
    UnmetDemand,
)
from db.models import Advisor, Deal, Lead, Property, ScheduledJob, UnmetSearch
from db.property_history import record_listing, set_status
from db.session import session_scope

CONFIG = ReportConfig(chat_ids=("1001", "1002"), panel_url="https://panel.example")
MONDAY_9 = datetime(2026, 10, 5, 9, tzinfo=LOCAL_TZ)  # lunes
WEEK_START, WEEK_END = datetime(2026, 9, 28, tzinfo=LOCAL_TZ), datetime(2026, 10, 5, tzinfo=LOCAL_TZ)


class Channel:
    def __init__(self, reachable=True, error=None):
        self.sent, self._reachable, self.error = [], reachable, error

    def reachable(self, chat_id):
        return self._reachable

    def send(self, chat_id, value):
        if self.error:
            raise self.error
        self.sent.append((chat_id, value))


@pytest.fixture(autouse=True)
def _clean(engine):
    """Las consultas y la cola son globales: cada test arranca sin datos ni pendientes de los anteriores."""
    with engine.begin() as connection:
        connection.execute(text("UPDATE scheduled_jobs SET status = 'cancelled' WHERE status = 'pending'"))
        connection.execute(text("DELETE FROM scheduled_jobs WHERE kind IN ('weekly_report_plan', 'weekly_report')"))
        connection.execute(text("DELETE FROM deals"))
        connection.execute(text("DELETE FROM unmet_searches"))
        connection.execute(text("DELETE FROM property_status_events"))


def _jobs(engine, kind):
    with session_scope(engine) as session:
        return [(job.dedupe_key, job.status, job.run_at, dict(job.payload)) for job in session.execute(
            select(ScheduledJob).where(ScheduledJob.kind == kind).order_by(ScheduledJob.created_at)).scalars()]


def _schedule(engine, now, config=CONFIG):
    with session_scope(engine) as session:
        return weekly_report.schedule(session, now, config)


# --- calendario ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("after,expected", [
    (datetime(2026, 10, 3, 12, tzinfo=LOCAL_TZ), MONDAY_9),  # sábado: el lunes que viene
    (datetime(2026, 10, 5, 8, 59, tzinfo=LOCAL_TZ), MONDAY_9),  # lunes antes de la hora: ese mismo día
    (MONDAY_9, MONDAY_9 + timedelta(days=7)),  # justo a la hora: la semana siguiente
    (datetime(2026, 10, 5, 15, 30, tzinfo=UTC), MONDAY_9 + timedelta(days=7)),  # 9:30 local
])
def test_the_report_goes_out_on_mondays_at_9_local_time(after, expected):
    assert next_run(after, CONFIG) == expected


def test_day_and_hour_can_be_configured():
    friday_18 = ReportConfig(chat_ids=("1",), weekday=4, hour=18)
    assert next_run(MONDAY_9, friday_18) == datetime(2026, 10, 9, 18, tzinfo=LOCAL_TZ)


def test_it_reports_the_last_complete_week_monday_to_monday():
    assert reported_week(MONDAY_9) == (WEEK_START, WEEK_END)
    assert reported_week(datetime(2026, 10, 9, 18, tzinfo=LOCAL_TZ)) == (WEEK_START, WEEK_END)
    assert week_label(WEEK_START) == "2026-W40"


def test_the_config_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("WEEKLY_REPORT_CHAT_IDS", " 1001, ,1002 ")
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://panel.example/")
    monkeypatch.setenv("WEEKLY_REPORT_INCLUDE_SYNTHETIC", "1")
    config = ReportConfig.from_env()
    assert config.chat_ids == ("1001", "1002") and config.panel_url == "https://panel.example"
    assert (config.weekday, config.hour, config.include_synthetic) == (0, 9, True)


@pytest.mark.parametrize("name,value", [("WEEKLY_REPORT_WEEKDAY", "7"), ("WEEKLY_REPORT_HOUR", "24")])
def test_an_invalid_day_or_hour_fails_at_startup(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        ReportConfig.from_env()


# --- el mensaje ------------------------------------------------------------------------------------------

def _stuck(title, days, inquiries=0, visits=0):
    return StagnantProperty(property_id=title, title=title, zone="El Poblado", type="venta",
                            price=Decimal(1), available_since=WEEK_END - timedelta(days=days), days=days,
                            threshold=60, inquiries=inquiries, visits=visits)


def test_the_message_has_placements_stagnant_unmet_demand_and_the_link():
    placed = Placements(deals=3, amount=Decimal("3020000"), by_zone=[
        PlacedGroup("El Poblado", 2, Decimal("1020000")), PlacedGroup("Usaquen", 1, Decimal("2000000"))],
        by_type=[], by_advisor=[])
    stuck = [_stuck("Casa A", 95), _stuck("Casa B", 61, inquiries=2)]
    unmet = [UnmetDemand("El Poblado", "renta", 3, 5), UnmetDemand(None, "venta", None, 1),
             UnmetDemand("Laureles", "renta", 2, 2, kind="departamento")]

    message = report_text(WEEK_START, WEEK_END, placed, stuck, unmet, "https://panel.example")

    assert message.split("\n") == [
        "Weekly report: Sep 28 to Oct 4, 2026",
        "",
        "Placed: 3 ($3,020,000)",
        "- El Poblado: 2",
        "- Usaquen: 1",
        "",
        "On the market for more than 60 days: 2",
        "- Casa A (El Poblado): 95 days, no inquiries",
        "- Casa B (El Poblado): 61 days, inquiries but no visits",
        "",
        "Unmet demand:",
        "- for rent in El Poblado, 3 bedrooms: 5 searches",
        "- for sale in any area: 1 search",
        "- apartment for rent in Laureles, 2 bedrooms: 2 searches",
        "",
        f"Details in the panel: https://panel.example{BUSINESS_PATH}",
    ]


def test_an_empty_week_says_so_instead_of_failing():
    message = report_text(WEEK_START, WEEK_END, Placements(0, Decimal(0), [], [], []), [], [], "http://x")
    assert "Placed: 0\n" in message and "On the market for more than 60 days: 0" in message
    assert "- no searches without results" in message


def test_only_the_oldest_stagnant_properties_are_listed():
    stuck = [_stuck(f"Casa {i}", 100 - i) for i in range(7)]
    message = report_text(WEEK_START, WEEK_END, Placements(0, Decimal(0), [], [], []), stuck, [], "http://x")
    assert "Casa 4" in message and "Casa 5" not in message and "- and 2 more" in message


# --- de punta a punta por el scheduler ------------------------------------------------------------------

def _seed_week(engine):
    with session_scope(engine) as session:
        advisor = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Valeria", zone="El Poblado",
                          access_token=uuid.uuid4().hex)
        lead = Lead(telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}")
        session.add_all([advisor, lead])
        ids = {pid: f"{pid}_{uuid.uuid4().hex[:6]}" for pid in ("nr_cerrada", "nr_quieta", "nr_nueva")}
        session.add_all([Property(id=prop_id, title=f"Casa {pid}", price=1_000_000, type="venta", kind="casa",
                                  zone="El Poblado", m2=100, bedrooms=2, bathrooms=1, address_approx="a",
                                  address_exact="b") for pid, prop_id in ids.items()])
        session.flush()
        record_listing(session, ids["nr_cerrada"], "test", at=WEEK_START - timedelta(days=30))
        set_status(session, ids["nr_cerrada"], "cerrado", advisor.id, at=WEEK_START + timedelta(days=2))
        session.add(Deal(property_id=ids["nr_cerrada"], lead_id=lead.id, advisor_id=advisor.id, kind="venta",
                         price=Decimal("1000000"), closed_at=WEEK_START + timedelta(days=2)))
        record_listing(session, ids["nr_quieta"], "test", at=WEEK_END - timedelta(days=70))
        record_listing(session, ids["nr_nueva"], "test", at=WEEK_END - timedelta(days=10))
        session.add_all([UnmetSearch(zone="Usaquen", type="renta", bedrooms=3,
                                     created_at=WEEK_START + timedelta(days=1)) for _ in range(2)])


def test_startup_schedules_the_plan_once_and_only_with_recipients(engine):
    assert _schedule(engine, MONDAY_9 - timedelta(days=2), ReportConfig(chat_ids=())) is False
    assert _schedule(engine, MONDAY_9 - timedelta(days=2)) is True
    assert _schedule(engine, MONDAY_9 - timedelta(days=1)) is False  # un reinicio no duplica el plan
    ((key, status, run_at, _),) = _jobs(engine, PLAN_KIND)
    assert (key, status, run_at) == ("semanal", "pending", MONDAY_9)


def test_each_recipient_gets_the_week_report_once_and_the_plan_reschedules_itself(engine):
    _seed_week(engine)
    _schedule(engine, MONDAY_9 - timedelta(days=2))
    channel = Channel()
    runner = Scheduler(engine, channel, report_config=CONFIG)

    runner.run_due(MONDAY_9)

    assert sorted(chat for chat, _ in channel.sent) == ["1001", "1002"]
    message = channel.sent[0][1]
    assert "Placed: 1 ($1,000,000)" in message
    assert "On the market for more than 60 days: 1" in message and "Casa nr_quieta" in message
    assert "Casa nr_nueva" not in message  # 10 días: todavía no está estancada
    assert "- for rent in Usaquen, 3 bedrooms: 2 searches" in message
    reports = _jobs(engine, REPORT_KIND)
    assert sorted(key for key, *_ in reports) == ["2026-W40:1001", "2026-W40:1002"]
    assert {status for _, status, *_ in reports} == {"done"}
    plans = _jobs(engine, PLAN_KIND)
    assert [(status, run_at) for _, status, run_at, _ in plans] == [
        ("done", MONDAY_9), ("pending", MONDAY_9 + timedelta(days=7))]

    channel.sent.clear()
    runner.run_due(MONDAY_9 + timedelta(hours=1))
    assert channel.sent == []  # nada se repite


def test_the_same_week_is_never_sent_twice_even_if_planned_again(engine):
    channel = Channel()
    runner = Scheduler(engine, channel, report_config=CONFIG)
    with session_scope(engine) as session:
        weekly_report.plan(session, MONDAY_9, MONDAY_9, CONFIG)
    runner.run_due(MONDAY_9)
    with session_scope(engine) as session:
        assert weekly_report.plan(session, MONDAY_9, MONDAY_9 + timedelta(minutes=5), CONFIG) == 0
    runner.run_due(MONDAY_9 + timedelta(minutes=5))
    assert len(channel.sent) == 2


def test_if_telegram_fails_the_report_is_retried_and_sent_only_once(engine):
    with session_scope(engine) as session:
        weekly_report.plan(session, MONDAY_9, MONDAY_9, ReportConfig(chat_ids=("1001",)))
    failing = Channel(error=ConnectionError("Telegram no responde"))
    Scheduler(engine, failing, report_config=CONFIG).run_due(MONDAY_9)
    ((_, status, run_at, _),) = _jobs(engine, REPORT_KIND)
    assert (status, run_at) == ("pending", MONDAY_9 + timedelta(minutes=1))

    working = Channel()
    runner = Scheduler(engine, working, report_config=CONFIG)
    runner.run_due(MONDAY_9 + timedelta(minutes=1))
    runner.run_due(MONDAY_9 + timedelta(minutes=10))
    assert [chat for chat, _ in working.sent] == ["1001"]


def test_without_a_bot_the_report_counts_as_a_failure_and_ends_failed(engine):
    with session_scope(engine) as session:
        weekly_report.plan(session, MONDAY_9, MONDAY_9, ReportConfig(chat_ids=("1001",)))
    runner = Scheduler(engine, Channel(reachable=False), report_config=CONFIG)
    moment = MONDAY_9
    for _ in range(MAX_ATTEMPTS):
        runner.run_due(moment)
        moment += timedelta(minutes=10)
    ((_, status, _, _),) = _jobs(engine, REPORT_KIND)
    assert status == "failed"


def test_synthetic_history_is_left_out_unless_configured(engine):
    with session_scope(engine) as session:
        session.add(UnmetSearch(zone="Chapinero", type="venta", bedrooms=1, synthetic=True,
                                created_at=WEEK_START + timedelta(days=1)))
    with session_scope(engine) as session:
        plain = weekly_report.build(session, WEEK_START, WEEK_END, CONFIG)
        demo = weekly_report.build(session, WEEK_START, WEEK_END,
                                   ReportConfig(chat_ids=("1",), include_synthetic=True))
    assert "Chapinero" not in plain and "Chapinero" in demo


def test_the_report_never_carries_lead_data(engine):
    _seed_week(engine)
    with session_scope(engine) as session:
        message = weekly_report.build(session, WEEK_START, WEEK_END, CONFIG)
        telegram_ids = list(session.execute(select(Lead.telegram_user_id)).scalars())
        lead_ids = [str(i) for i in session.execute(select(Lead.id)).scalars()]
    assert not any(value in message for value in telegram_ids + lead_ids)
