"""B-12: tiempo de atención por asesor, de que el lead califica a que lo toman en el panel."""
from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.metrics.sla import ClaimTime, claim_times
from db.models import Advisor, Lead, LeadScore
from db.testing import temporary_database

START = datetime(2026, 9, 14, 6, tzinfo=UTC)
END = START + timedelta(days=7)
QUALIFIED = START + timedelta(days=2)
TOTALS = {"HOT": 90, "WARM": 50, "COLD": 10}


@pytest.fixture(scope="module")
def engine():
    with temporary_database() as url:
        engine = create_engine(url)
        yield engine
        engine.dispose()


@pytest.fixture
def session(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection) as session:
            session.add_all([
                Advisor(id="adv_v", name="Valeria", zone="Providencia", access_token=f"t-{uuid.uuid4().hex}"),
                Advisor(id="adv_a", name="Andrés", zone="Zapopan", access_token=f"t-{uuid.uuid4().hex}"),
            ])
            session.flush()
            yield session
        transaction.rollback()


def _taken(session, advisor, wait, *, band="HOT", qualified=QUALIFIED, synthetic=False) -> Lead:
    lead = Lead(telegram_user_id=f"tg-{uuid.uuid4().hex}", advisor_id=advisor, status="sql",
                qualified_at=qualified, claimed_at=None if wait is None else qualified + wait, synthetic=synthetic)
    session.add(lead)
    session.flush()
    session.add(LeadScore(lead_id=lead.id, need_fit=0, budget=0, authority=0, timing=0, eligibility=0,
                          total=TOTALS[band], band=band, reasons=[]))
    session.flush()
    return lead


def test_the_median_per_advisor_slowest_first(session):
    for minutes in (10, 20, 90):
        _taken(session, "adv_v", timedelta(minutes=minutes))
    _taken(session, "adv_a", timedelta(hours=4), band="WARM")

    rows = claim_times(session, START, END)

    assert [r.advisor for r in rows] == ["Andrés", "Valeria"]
    andres, valeria = rows
    assert valeria.median == timedelta(minutes=20) and valeria.leads == 3 and valeria.hot == 3
    assert valeria.median_hot == timedelta(minutes=20)
    assert andres.median == timedelta(hours=4) and andres.hot == 0
    assert andres.median_hot is None


def test_the_hot_median_only_counts_hot_leads(session):
    _taken(session, "adv_v", timedelta(minutes=10))
    _taken(session, "adv_v", timedelta(hours=10), band="WARM")

    (valeria,) = claim_times(session, START, END)

    assert valeria.median_hot == timedelta(minutes=10)
    assert valeria.median == timedelta(hours=5, minutes=5)


def test_what_does_not_count(session):
    _taken(session, "adv_v", None)  # nunca se tomó
    _taken(session, "adv_v", -timedelta(hours=1))  # se tomó antes de calificar
    _taken(session, "adv_v", timedelta(minutes=5), qualified=END + timedelta(days=1))  # fuera del periodo
    _taken(session, "adv_v", timedelta(minutes=5), synthetic=True)
    session.add(Lead(telegram_user_id=f"tg-{uuid.uuid4().hex}", advisor_id="adv_v", status="sql",
                     claimed_at=QUALIFIED))  # tomado sin haber calificado nunca
    session.flush()

    assert claim_times(session, START, END) == []


def test_the_synthetic_history_counts_when_asked(session):
    _taken(session, "adv_v", timedelta(minutes=5), synthetic=True)

    (valeria,) = claim_times(session, START, END, include_synthetic=True)
    assert valeria.leads == 1


def test_a_period_that_ends_before_it_starts_is_an_error(session):
    with pytest.raises(ValueError):
        claim_times(session, END, START)


def test_no_row_carries_a_lead():
    assert {f.name for f in fields(ClaimTime)} == {"advisor", "median", "median_hot", "leads", "hot"}
