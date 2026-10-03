"""N-7: atribución al bot (DT-41), tiempo al cierre, conversión por banda y retorno por campaña.

Cada test arma sus datos dentro de una transacción que se revierte, sobre una base propia del módulo: las
consultas son globales y cualquier resto de otro test cambiaría los conteos.
"""
from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.metrics.attribution import (
    ATTRIBUTION_WINDOW_DAYS,
    AttributionReport,
    BandConversion,
    attribution_report,
    bot_attribution,
    campaign_return,
    conversion_by_band,
    time_to_close,
)
from db.models import Advisor, Deal, Lead, LeadScore, Property, PropertyInterest
from db.testing import temporary_database

START, END = datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)
CLOSED = datetime(2026, 9, 20, 12, tzinfo=UTC)


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
                Property(id="prop_a", title="A", price=1, type="renta", zone="Providencia", m2=1, bedrooms=1,
                         bathrooms=1, address_approx="a", address_exact="b"),
                Property(id="prop_b", title="B", price=1, type="venta", zone="Zapopan", m2=1, bedrooms=1,
                         bathrooms=1, address_approx="a", address_exact="b"),
                Advisor(id="adv_a", name="Valeria", zone="Providencia", access_token=f"t-{uuid.uuid4().hex}"),
            ])
            session.flush()
            yield session
        transaction.rollback()


def _lead(session, *, created=START + timedelta(days=1), campaign=None, band=None, status="nuevo",
          qualified=False, synthetic=False) -> Lead:
    lead = Lead(telegram_user_id=f"tg-{uuid.uuid4().hex}", created_at=created, utm_campaign=campaign,
                status=status, synthetic=synthetic, qualified_at=created if qualified else None)
    session.add(lead)
    session.flush()
    if band:
        session.add(LeadScore(lead_id=lead.id, need_fit=0, budget=0, authority=0, timing=0, eligibility=0,
                              total={"HOT": 80, "WARM": 50, "COLD": 10}[band], band=band, reasons=[]))
        session.flush()
    return lead


def _deal(session, lead, *, prop="prop_a", closed=CLOSED, synthetic=False) -> Deal:
    deal = Deal(property_id=prop, lead_id=lead.id, advisor_id="adv_a", kind="renta", price=Decimal("1000"),
                closed_at=closed, synthetic=synthetic)
    session.add(deal)
    session.flush()
    return deal


def _touched(session, lead, kind, *, prop="prop_a", days_before=1.0, closed=CLOSED):
    session.add(PropertyInterest(lead_id=lead.id, property_id=prop, kind=kind,
                                 created_at=closed - timedelta(days=days_before)))
    session.flush()


def _attributed(session, **options) -> tuple[int, int]:
    result = bot_attribution(session, START, END, **options)
    return result.attributed, result.deals


# --- atribución al bot (DT-41) ---------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["anuncio", "consulta", "visita"])
def test_a_deal_on_a_property_the_lead_touched_with_the_bot_is_attributed(session, kind):
    lead = _lead(session)
    _touched(session, lead, kind)
    _deal(session, lead)
    assert _attributed(session) == (1, 1)


def test_a_search_result_alone_does_not_attribute(session):
    lead = _lead(session)
    _touched(session, lead, "busqueda")
    _deal(session, lead)
    assert _attributed(session) == (0, 1)


def test_a_search_plus_a_lookup_does(session):
    lead = _lead(session)
    _touched(session, lead, "busqueda", days_before=5)
    _touched(session, lead, "consulta", days_before=3)
    _deal(session, lead)
    assert _attributed(session) == (1, 1)


@pytest.mark.parametrize("days_before,attributed", [
    (ATTRIBUTION_WINDOW_DAYS, 1), (ATTRIBUTION_WINDOW_DAYS + 1, 0), (ATTRIBUTION_WINDOW_DAYS - 0.5, 1)])
def test_the_window_is_60_days_before_the_deal(session, days_before, attributed):
    lead = _lead(session, created=START - timedelta(days=90))
    _touched(session, lead, "consulta", days_before=days_before)
    _deal(session, lead)
    assert _attributed(session) == (attributed, 1)


def test_the_window_counts_from_the_last_interaction(session):
    lead = _lead(session, created=START - timedelta(days=120))
    _touched(session, lead, "consulta", days_before=90)
    _touched(session, lead, "visita", days_before=10)
    _deal(session, lead)
    assert _attributed(session) == (1, 1)


def test_an_interaction_after_the_deal_does_not_count(session):
    lead = _lead(session)
    _touched(session, lead, "consulta", days_before=-1)
    _deal(session, lead)
    assert _attributed(session) == (0, 1)


def test_another_property_of_the_same_lead_does_not_count(session):
    lead = _lead(session)
    _touched(session, lead, "consulta", prop="prop_b")
    _deal(session, lead, prop="prop_a")
    assert _attributed(session) == (0, 1)


def test_another_lead_on_the_same_property_does_not_count(session):
    lead, other = _lead(session), _lead(session)
    _touched(session, other, "consulta")
    _deal(session, lead)
    assert _attributed(session) == (0, 1)


def test_only_deals_closed_inside_the_period_count(session):
    lead = _lead(session)
    _deal(session, lead, closed=START - timedelta(seconds=1))
    _deal(session, lead, closed=END)  # el periodo es [start, end)
    _deal(session, lead, closed=START)
    assert _attributed(session) == (0, 1)


def test_synthetic_deals_count_only_when_asked(session):
    lead = _lead(session, synthetic=True)
    _touched(session, lead, "anuncio")
    _deal(session, lead, synthetic=True)
    assert _attributed(session) == (0, 0)
    assert _attributed(session, include_synthetic=True) == (1, 1)


def test_no_deals_gives_no_rate(session):
    assert bot_attribution(session, START, END).rate is None


# --- tiempo al cierre ------------------------------------------------------------------------------

def test_time_to_close_goes_from_the_first_contact_to_each_deal(session):
    for days in (10, 20, 30, 40):
        _deal(session, _lead(session, created=CLOSED - timedelta(days=days)))
    result = time_to_close(session, START, END)
    assert result.deals == 4
    assert result.p50 == timedelta(days=25)
    assert result.p90 == timedelta(days=37)


def test_time_to_close_without_deals_is_empty(session):
    result = time_to_close(session, START, END)
    assert (result.p50, result.p90, result.deals) == (None, None, 0)


# --- conversión por banda ---------------------------------------------------------------------------

def test_conversion_by_band_counts_the_leads_created_in_the_period(session):
    _lead(session, band="HOT", status="closed_won")
    _lead(session, band="HOT")
    _lead(session, band="WARM", status="closed_lost")
    _lead(session, band="HOT", status="closed_won", created=START - timedelta(days=1))  # fuera del periodo
    _lead(session)  # sin score: no tiene banda
    assert conversion_by_band(session, START, END) == [
        BandConversion("HOT", leads=2, won=1), BandConversion("WARM", leads=1, won=0),
        BandConversion("COLD", leads=0, won=0)]
    assert conversion_by_band(session, START, END)[0].rate == 0.5


def test_synthetic_leads_are_left_out_of_the_bands_by_default(session):
    _lead(session, band="COLD", synthetic=True)
    assert conversion_by_band(session, START, END)[2].leads == 0
    assert conversion_by_band(session, START, END, include_synthetic=True)[2].leads == 1


# --- retorno por campaña ----------------------------------------------------------------------------

def test_campaign_return_counts_leads_qualified_and_closed(session):
    closed = _lead(session, campaign="feria", qualified=True, status="closed_won")
    _deal(session, closed)
    _lead(session, campaign="feria", qualified=True)
    _lead(session, campaign="feria")
    _lead(session, campaign="volante")
    _lead(session)
    rows = [(r.campaign, r.leads, r.qualified, r.closed) for r in campaign_return(session, START, END)]
    assert rows == [("feria", 3, 2, 1), ("volante", 1, 0, 0), (None, 1, 0, 0)]


# --- el reporte completo -----------------------------------------------------------------------------

def test_the_report_has_the_four_metrics_and_rejects_an_empty_period(session):
    report = attribution_report(session, START, END, include_synthetic=True)
    assert isinstance(report, AttributionReport) and report.include_synthetic
    assert [band.band for band in report.by_band] == ["HOT", "WARM", "COLD"]
    with pytest.raises(ValueError):
        attribution_report(session, END, START)


def test_no_result_carries_a_lead(session):
    """DT-41: una vista agregada cuenta leads, nunca los lista."""
    lead = _lead(session, campaign="feria", band="HOT")
    _deal(session, lead)
    report = attribution_report(session, START, END)
    for item in [report.attribution, report.time_to_close, *report.by_band, *report.by_campaign]:
        names = {field.name for field in fields(item)}
        assert not names & {"lead_id", "lead", "leads_ids", "phone", "email", "telegram_user_id", "name"}
        assert str(lead.id) not in repr(item)
