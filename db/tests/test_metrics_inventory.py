"""N-6: colocadas, días en el mercado, estancadas con su demanda, ranking y demanda insatisfecha.

Cada test arma sus datos dentro de una transacción que se revierte, sobre una base propia del módulo: las
consultas son globales y cualquier resto de otro test cambiaría los conteos. Nada depende del *seed* de N-9.
"""
from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.metrics.inventory import (
    INQUIRIES_WITHOUT_VISITS,
    LOCAL_TZ,
    NO_INQUIRIES,
    InventoryReport,
    days_on_market,
    inventory_report,
    month_of,
    placements,
    ranking,
    stagnant,
    unmet_demand,
    week_of,
)
from db.models import Advisor, Deal, Lead, Property, PropertyInterest, UnmetSearch
from db.property_history import record_listing, set_status
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
                Advisor(id="adv_v", name="Valeria", zone="Providencia", access_token=f"t-{uuid.uuid4().hex}"),
                Advisor(id="adv_a", name="Andrés", zone="Zapopan", access_token=f"t-{uuid.uuid4().hex}"),
            ])
            session.flush()
            yield session
        transaction.rollback()


def _property(session, pid, *, zone="Providencia", type_="venta", price=1_000_000,
              listed: datetime | None = None, synthetic=False) -> Property:
    prop = Property(id=pid, title=f"Casa {pid}", price=price, type=type_, kind="casa", zone=zone, m2=100, bedrooms=2,
                    bathrooms=1, address_approx="a", address_exact="b")
    session.add(prop)
    session.flush()
    if listed is not None:
        record_listing(session, pid, "test", at=listed, synthetic=synthetic)
    return prop


def _lead(session) -> Lead:
    lead = Lead(telegram_user_id=f"tg-{uuid.uuid4().hex}")
    session.add(lead)
    session.flush()
    return lead


def _close(session, pid, *, advisor="adv_v", closed=CLOSED, price="900000", synthetic=False) -> Deal:
    set_status(session, pid, "cerrado", advisor, at=closed, synthetic=synthetic)
    deal = Deal(property_id=pid, lead_id=_lead(session).id, advisor_id=advisor, kind="venta",
                price=Decimal(price), closed_at=closed, synthetic=synthetic)
    session.add(deal)
    session.flush()
    return deal


def _interest(session, pid, kind, *, lead=None, at=START + timedelta(days=3), synthetic=False) -> Lead:
    lead = lead or _lead(session)
    session.add(PropertyInterest(lead_id=lead.id, property_id=pid, kind=kind, created_at=at, synthetic=synthetic))
    session.flush()
    return lead


# --- periodos --------------------------------------------------------------------------------------------

def test_a_week_goes_from_monday_to_monday_in_local_time():
    start, end = week_of(date(2026, 10, 3))  # sábado
    assert start == datetime(2026, 9, 28, tzinfo=LOCAL_TZ) and end == datetime(2026, 10, 5, tzinfo=LOCAL_TZ)


@pytest.mark.parametrize("day,expected", [
    (date(2026, 10, 3), (date(2026, 10, 1), date(2026, 11, 1))),
    (date(2026, 12, 31), (date(2026, 12, 1), date(2027, 1, 1))),
    (date(2026, 2, 10), (date(2026, 2, 1), date(2026, 3, 1))),
])
def test_a_month_goes_from_the_first_to_the_first_of_the_next(day, expected):
    start, end = month_of(day)
    assert (start.date(), end.date()) == expected and start.tzinfo == LOCAL_TZ


def test_a_period_that_ends_before_it_starts_is_an_error(session):
    with pytest.raises(ValueError):
        placements(session, END, START)


# --- colocadas ------------------------------------------------------------------------------------------

def test_placements_count_the_deals_of_the_period_by_zone_type_and_advisor(session):
    for pid, zone, type_, advisor, price in [
        ("p1", "Providencia", "venta", "adv_v", "1000000"), ("p2", "Providencia", "renta", "adv_v", "20000"),
        ("p3", "Zapopan", "venta", "adv_a", "2000000"),
    ]:
        _property(session, pid, zone=zone, type_=type_)
        _close(session, pid, advisor=advisor, price=price)
    _property(session, "fuera")
    _close(session, "fuera", closed=END)  # el fin del periodo es abierto

    result = placements(session, START, END)

    assert (result.deals, result.amount) == (3, Decimal("3020000"))
    assert [(g.name, g.deals) for g in result.by_zone] == [("Providencia", 2), ("Zapopan", 1)]
    assert {g.name: g.deals for g in result.by_type} == {"venta": 2, "renta": 1}
    assert [(g.name, g.deals, g.amount) for g in result.by_advisor] == [
        ("Valeria", 2, Decimal("1020000")), ("Andrés", 1, Decimal("2000000"))]


def test_an_empty_period_has_no_placements(session):
    result = placements(session, START, END)
    assert (result.deals, result.amount, result.by_zone, result.by_type, result.by_advisor) == (0, 0, [], [], [])


# --- días en el mercado ----------------------------------------------------------------------------------

def test_days_on_market_go_from_the_first_time_available_to_the_close(session):
    _property(session, "p1", listed=CLOSED - timedelta(days=10))
    _property(session, "p2", listed=CLOSED - timedelta(days=30))
    _property(session, "p3", listed=CLOSED - timedelta(days=50))
    # p3 se apartó y volvió: cuenta desde la primera vez que estuvo disponible.
    set_status(session, "p3", "apartado", "test", at=CLOSED - timedelta(days=20))
    set_status(session, "p3", "disponible", "test", at=CLOSED - timedelta(days=5))
    _property(session, "z1", zone="Zapopan", type_="renta", listed=CLOSED - timedelta(days=4))
    for pid in ("p1", "p2", "p3", "z1"):
        _close(session, pid)

    assert [(d.name, d.median_days, d.deals) for d in days_on_market(session, START, END)] == [
        ("Providencia", 30.0, 3), ("Zapopan", 4.0, 1)]
    by_type = {d.name: d.median_days for d in days_on_market(session, START, END, "type")}
    assert by_type == {"venta": 30.0, "renta": 4.0}


def test_a_close_without_listing_history_is_not_measured(session):
    _property(session, "sin_historial")
    _close(session, "sin_historial")
    assert days_on_market(session, START, END) == []
    assert placements(session, START, END).deals == 1  # pero sí cuenta como colocada


def test_days_on_market_only_by_zone_or_type(session):
    with pytest.raises(ValueError):
        days_on_market(session, START, END, "advisor")


# --- estancadas ----------------------------------------------------------------------------------------

def test_stagnant_properties_are_those_available_for_more_than_30_days_with_their_threshold(session):
    for pid, days in [("d10", 10), ("d35", 35), ("d65", 65), ("d95", 95)]:
        _property(session, pid, listed=END - timedelta(days=days))

    found = stagnant(session, START, END)

    assert [(s.property_id, s.days, s.threshold) for s in found] == [
        ("d95", 95, 90), ("d65", 65, 60), ("d35", 35, 30)]


def test_only_properties_available_at_the_cutoff_count_and_since_their_last_return(session):
    _property(session, "cerrada", listed=END - timedelta(days=100))
    _close(session, "cerrada")
    _property(session, "volvio", listed=END - timedelta(days=100))
    set_status(session, "volvio", "apartado", "test", at=END - timedelta(days=50))
    set_status(session, "volvio", "disponible", "test", at=END - timedelta(days=40))
    _property(session, "sin_historial")  # disponible, pero no se puede medir

    assert [(s.property_id, s.days) for s in stagnant(session, START, END)] == [("volvio", 40)]


def test_the_status_is_the_one_at_the_cutoff_not_today(session):
    # #177: el reporte semanal corta en el pasado; lo que pasó después del corte no cuenta.
    _property(session, "rentada_y_de_vuelta", listed=END - timedelta(days=170))
    _close(session, "rentada_y_de_vuelta", closed=END - timedelta(days=160))
    set_status(session, "rentada_y_de_vuelta", "disponible", "test", at=END + timedelta(days=2))
    _property(session, "apartada_y_de_vuelta", listed=END - timedelta(days=150))
    set_status(session, "apartada_y_de_vuelta", "apartado", "test", at=END - timedelta(days=40))
    set_status(session, "apartada_y_de_vuelta", "disponible", "test", at=END + timedelta(days=1))
    _property(session, "vendida_despues", listed=END - timedelta(days=100))
    _close(session, "vendida_despues", closed=END + timedelta(days=3))

    assert [(s.property_id, s.days) for s in stagnant(session, START, END)] == [("vendida_despues", 100)]


def test_the_cutoff_date_can_be_moved(session):
    _property(session, "p", listed=END - timedelta(days=20))
    assert stagnant(session, START, END) == []
    (moved,) = stagnant(session, START, END, as_of=END + timedelta(days=15))
    assert moved.days == 35


def test_each_stagnant_property_carries_its_demand_of_the_period(session):
    for pid in ("sin_nada", "solo_consultas", "con_visita"):
        _property(session, pid, listed=END - timedelta(days=45))
    lead = _interest(session, "solo_consultas", "consulta")
    _interest(session, "solo_consultas", "consulta", lead=lead)  # el mismo lead en otro turno: uno
    _interest(session, "solo_consultas", "anuncio")
    _interest(session, "solo_consultas", "busqueda")  # salir en una lista no es una consulta
    _interest(session, "sin_nada", "consulta", at=START - timedelta(days=1))  # fuera del periodo
    _interest(session, "con_visita", "visita")

    found = {s.property_id: s for s in stagnant(session, START, END)}

    assert (found["sin_nada"].inquiries, found["sin_nada"].demand) == (0, NO_INQUIRIES)
    assert (found["solo_consultas"].inquiries, found["solo_consultas"].visits) == (2, 0)
    assert found["solo_consultas"].demand == INQUIRIES_WITHOUT_VISITS
    assert (found["con_visita"].visits, found["con_visita"].demand) == (1, None)


# --- ranking ---------------------------------------------------------------------------------------------

def test_the_ranking_orders_by_closes_then_visits_then_inquiries(session):
    for pid in ("cerrada", "visitada", "consultada", "quieta"):
        _property(session, pid)
    _close(session, "cerrada")
    _interest(session, "visitada", "visita")
    _interest(session, "visitada", "consulta")
    for _ in range(3):
        _interest(session, "consultada", "consulta")

    result = ranking(session, START, END)

    assert [(r.property_id, r.deals, r.visits, r.inquiries) for r in result] == [
        ("cerrada", 1, 0, 0), ("visitada", 0, 1, 1), ("consultada", 0, 0, 3)]


def test_the_ranking_has_a_limit(session):
    for index in range(4):
        _property(session, f"p{index}")
        _interest(session, f"p{index}", "consulta")
    assert len(ranking(session, START, END, limit=2)) == 2


# --- demanda insatisfecha --------------------------------------------------------------------------------

def _unmet(session, zone, type_, bedrooms, times=1, *, at=START + timedelta(days=2), synthetic=False):
    session.add_all([UnmetSearch(zone=zone, type=type_, bedrooms=bedrooms, created_at=at, synthetic=synthetic)
                     for _ in range(times)])
    session.flush()


def test_unmet_demand_groups_the_most_repeated_combinations(session):
    _unmet(session, "Providencia", "renta", 3, times=2)
    _unmet(session, "providencia ", "renta", 3)  # la misma zona escrita distinto
    _unmet(session, "Zapopan", "venta", None, times=2)
    _unmet(session, None, None, 1)
    _unmet(session, "Providencia", "renta", 3, at=END)  # fuera del periodo

    result = unmet_demand(session, START, END)

    assert [(u.zone, u.type, u.bedrooms, u.searches) for u in result] == [
        ("Providencia", "renta", 3, 3), ("Zapopan", "venta", None, 2), (None, None, 1, 1)]


# --- datos sintéticos ------------------------------------------------------------------------------------

def test_every_query_can_leave_out_the_synthetic_history(session):
    _property(session, "real", listed=CLOSED - timedelta(days=10))
    _close(session, "real")
    _property(session, "demo", listed=CLOSED - timedelta(days=10), synthetic=True)
    _close(session, "demo", synthetic=True)
    _property(session, "demo_quieta", listed=END - timedelta(days=60), synthetic=True)
    _interest(session, "demo_quieta", "consulta", synthetic=True)
    _unmet(session, "Zapopan", "venta", 2, synthetic=True)

    assert placements(session, START, END).deals == 1
    assert placements(session, START, END, include_synthetic=True).deals == 2
    assert days_on_market(session, START, END)[0].deals == 1
    assert days_on_market(session, START, END, include_synthetic=True)[0].deals == 2
    assert stagnant(session, START, END) == []
    (quieta,) = stagnant(session, START, END, include_synthetic=True)
    assert quieta.inquiries == 1
    assert [r.property_id for r in ranking(session, START, END)] == ["real"]
    assert {r.property_id for r in ranking(session, START, END, include_synthetic=True)} == {
        "real", "demo", "demo_quieta"}
    assert unmet_demand(session, START, END) == []
    assert unmet_demand(session, START, END, include_synthetic=True)[0].searches == 1


# --- el reporte para N-8 ------------------------------------------------------------------------------------

def test_the_report_brings_every_query_of_the_period(session):
    _property(session, "p", listed=END - timedelta(days=40))
    _interest(session, "p", "consulta")

    report = inventory_report(session, START, END)

    assert isinstance(report, InventoryReport) and report.as_of == END and report.include_synthetic is False
    assert [s.property_id for s in report.stagnant] == ["p"]
    assert [r.property_id for r in report.ranking] == ["p"]
    assert {f.name for f in fields(InventoryReport)} == {
        "start", "end", "as_of", "include_synthetic", "placements", "days_on_market_by_zone",
        "days_on_market_by_type", "stagnant", "ranking", "unmet_demand"}


def test_no_query_returns_a_lead(session):
    """Vista agregada, como la de N-7: ningún resultado lleva ids ni datos de leads."""
    from db.metrics import inventory

    for name in ("PlacedGroup", "Placements", "DaysOnMarket", "StagnantProperty", "RankedProperty",
                 "UnmetDemand", "InventoryReport"):
        assert not any("lead" in f.name for f in fields(getattr(inventory, name)))
