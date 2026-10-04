"""B-12: la toma del lead queda guardada y la cola marca los HOT sin tomar que pasaron el umbral."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import main
from app.main import app, get_session
from app.repo import claim_lead
from app.sla import DEFAULT_MINUTES, hot_wait_from_env, overdue
from db.models import Advisor, Lead, LeadScore

TOTALS = {"HOT": 90, "WARM": 50, "COLD": 10}  # el total tiene que caer en la banda
NOW = datetime(2026, 10, 3, 18, tzinfo=UTC)


@pytest.fixture
def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def zona():
    """Zona única por test: la base es compartida en el módulo y la cola se filtra por zona."""
    return f"zona-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def client(session_factory):
    def override():
        session = session_factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _advisor(session, zone) -> Advisor:
    advisor = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Valeria", zone=zone, access_token=uuid.uuid4().hex)
    session.add(advisor)
    session.flush()
    return advisor


def _lead(session, zone, *, band="HOT", status="mql", qualified_ago=timedelta(hours=1)) -> Lead:
    qualified = None if qualified_ago is None else datetime.now(UTC) - qualified_ago
    lead = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}", zone=zone, status=status,
                qualified_at=qualified)
    session.add(lead)
    session.flush()
    if band:
        session.add(LeadScore(lead_id=lead.id, need_fit=0, budget=0, authority=0, timing=0, eligibility=0,
                              total=TOTALS[band], band=band, reasons=[]))
        session.flush()
    return lead


# --- la toma -------------------------------------------------------------------------------------------

def test_taking_the_lead_saves_when(session_factory, zona):
    with session_factory() as session:
        valeria = _advisor(session, zona)
        lead = _lead(session, zona)
        assert lead.claimed_at is None

        claim_lead(session, valeria, lead.id)
        session.commit()
        first = session.execute(select(Lead.claimed_at).where(Lead.id == lead.id)).scalar_one()

        claim_lead(session, valeria, lead.id)  # ya está en gestión: no se pisa
        session.commit()
        again = session.execute(select(Lead.claimed_at).where(Lead.id == lead.id)).scalar_one()

    assert first is not None
    assert again == first


# --- el umbral -----------------------------------------------------------------------------------------

def test_the_threshold_defaults_to_thirty_minutes(monkeypatch):
    monkeypatch.delenv("SLA_HOT_MINUTES", raising=False)
    assert hot_wait_from_env() == timedelta(minutes=DEFAULT_MINUTES) == timedelta(minutes=30)


def test_the_threshold_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("SLA_HOT_MINUTES", "45")
    assert hot_wait_from_env() == timedelta(minutes=45)


@pytest.mark.parametrize("value", ["media hora", "0", "-5", "1.5"])
def test_an_invalid_threshold_fails(monkeypatch, value):
    monkeypatch.setenv("SLA_HOT_MINUTES", value)
    with pytest.raises(ValueError, match="SLA_HOT_MINUTES"):
        hot_wait_from_env()


class _Row:
    def __init__(self, band="HOT", status="mql", qualified_at=NOW - timedelta(minutes=40)):
        self.score_band, self.status, self.qualified_at = band, status, qualified_at


@pytest.mark.parametrize("row, marked", [
    (_Row(), True),
    (_Row(status="nuevo"), True),
    (_Row(qualified_at=NOW - timedelta(minutes=20)), False),  # todavía dentro del umbral
    (_Row(band="WARM"), False),
    (_Row(status="sql"), False),  # ya tomado
    (_Row(status="visit_scheduled"), False),
    (_Row(qualified_at=None), False),
])
def test_only_unclaimed_hot_leads_past_the_threshold_are_marked(row, marked):
    assert (overdue(row, NOW, timedelta(minutes=30)) is not None) is marked


# --- la cola -------------------------------------------------------------------------------------------

def test_the_queue_marks_the_hot_leads_waiting_too_long(client, session_factory, zona, monkeypatch):
    monkeypatch.setattr(main, "SLA_HOT_WAIT", timedelta(minutes=30))
    with session_factory() as session:
        valeria = _advisor(session, zona)
        _lead(session, zona, qualified_ago=timedelta(minutes=45))
        _lead(session, zona, qualified_ago=timedelta(hours=3))
        _lead(session, zona, qualified_ago=timedelta(minutes=5))
        _lead(session, zona, band="WARM", qualified_ago=timedelta(hours=5))
        _lead(session, zona, status="sql", qualified_ago=timedelta(hours=5))
        session.commit()

    page = client.get(f"/panel/{valeria.access_token}").text

    assert page.count("HOT sin tomar hace") == 2
    assert "HOT sin tomar hace 45 min" in page
    assert "HOT sin tomar hace 3 h" in page
