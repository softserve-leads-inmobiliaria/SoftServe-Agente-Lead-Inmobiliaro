"""Tests de N-1 contra un Postgres real: las tablas de inteligencia comercial y db/property_history.py.

Igual que test_migrations.py: base temporal migrada a `head`, y cada test dentro de una transacción
que se revierte. Sin Postgres se omiten; en CI (`REQUIRE_DB=1`) fallan.
"""
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    Advisor,
    Deal,
    Lead,
    Property,
    PropertyInterest,
    PropertyStatusEvent,
    UnmetSearch,
)
from db.property_history import StatusUnchanged, UnknownProperty, record_listing, set_status
from db.testing import alembic_config, temporary_database


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
        with Session(bind=connection, expire_on_commit=False) as session:
            yield session
        transaction.rollback()


def _property(session, pid="prop_n1", status="disponible") -> Property:
    prop = Property(id=pid, title="t", price=1, type="renta", zone="Providencia", m2=1, bedrooms=1,
                    bathrooms=1, address_approx="a", address_exact="b", status=status)
    session.add(prop)
    session.flush()
    return prop


def _lead(session) -> Lead:
    lead = Lead(telegram_user_id=f"tg_{uuid.uuid4().hex}")
    session.add(lead)
    session.flush()
    return lead


def _advisor(session) -> Advisor:
    advisor = Advisor(id="adv_n1", name="n", zone="Providencia", access_token=f"tok_{uuid.uuid4().hex}")
    session.add(advisor)
    session.flush()
    return advisor


def _events(session, pid) -> list[tuple]:
    return session.execute(select(PropertyStatusEvent.from_status, PropertyStatusEvent.to_status,
                                  PropertyStatusEvent.actor)
                           .where(PropertyStatusEvent.property_id == pid)
                           .order_by(PropertyStatusEvent.id)).all()


# --- db/property_history.py ----------------------------------------------------------------------

def test_set_status_changes_the_property_and_writes_its_event(session):
    _property(session)

    set_status(session, "prop_n1", "apartado", "adv_001")
    set_status(session, "prop_n1", "cerrado", "adv_001")

    assert session.get(Property, "prop_n1").status == "cerrado"
    assert _events(session, "prop_n1") == [("disponible", "apartado", "adv_001"),
                                           ("apartado", "cerrado", "adv_001")]


def test_set_status_accepts_a_past_date_for_the_synthetic_history(session):
    _property(session)
    when = datetime(2026, 4, 1, 10, tzinfo=UTC)

    event = set_status(session, "prop_n1", "cerrado", "seed", at=when, synthetic=True)

    session.expire_all()
    stored = session.get(PropertyStatusEvent, event.id)
    assert (stored.changed_at, stored.synthetic) == (when, True)


def test_without_a_date_the_event_takes_the_database_time(session):
    _property(session)
    event = set_status(session, "prop_n1", "apartado", "adv_001")
    session.expire_all()
    now = session.execute(text("SELECT now()")).scalar()
    assert session.get(PropertyStatusEvent, event.id).changed_at == now  # misma transacción


def test_a_closed_property_cannot_be_closed_again(session):
    _property(session, status="cerrado")
    with pytest.raises(StatusUnchanged):
        set_status(session, "prop_n1", "cerrado", "adv_001")
    assert _events(session, "prop_n1") == []


@pytest.mark.parametrize("pid,status,error", [
    ("prop_n1", "vendida", ValueError), ("prop_no_existe", "cerrado", UnknownProperty)])
def test_set_status_rejects_unknown_statuses_and_properties(session, pid, status, error):
    _property(session)
    with pytest.raises(error):
        set_status(session, pid, status, "adv_001")
    assert session.get(Property, "prop_n1").status == "disponible"
    assert _events(session, "prop_n1") == []


def test_record_listing_marks_the_start_without_changing_the_status(session):
    _property(session)
    record_listing(session, "prop_n1", "seed")
    assert session.get(Property, "prop_n1").status == "disponible"
    assert _events(session, "prop_n1") == [(None, "disponible", "seed")]


# --- tablas nuevas ---------------------------------------------------------------------------------

def test_a_deal_links_property_lead_and_advisor(session):
    _property(session)
    lead, advisor = _lead(session), _advisor(session)
    session.add(Deal(property_id="prop_n1", lead_id=lead.id, advisor_id=advisor.id, kind="renta",
                     price=Decimal("18000")))
    session.flush()
    deal = session.execute(select(Deal)).scalar_one()
    session.refresh(deal)
    assert (deal.kind, deal.synthetic) == ("renta", False) and deal.closed_at is not None


@pytest.mark.parametrize("field,value", [("kind", "preventa"), ("price", Decimal("-1"))])
def test_deal_kind_and_price_are_constrained(session, field, value):
    _property(session)
    lead, advisor = _lead(session), _advisor(session)
    kwargs = dict(property_id="prop_n1", lead_id=lead.id, advisor_id=advisor.id, kind="venta",
                  price=Decimal("1")) | {field: value}
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Deal(**kwargs))
        session.flush()


@pytest.mark.parametrize("kind", ["anuncio", "consulta", "busqueda", "visita"])
def test_every_interest_kind_can_be_stored(session, kind):
    _property(session)
    session.add(PropertyInterest(lead_id=_lead(session).id, property_id="prop_n1", kind=kind))
    session.flush()


def test_unknown_interest_kind_is_rejected(session):
    _property(session)
    lead = _lead(session)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(PropertyInterest(lead_id=lead.id, property_id="prop_n1", kind="like"))
        session.flush()


def test_an_unmet_search_takes_any_subset_of_filters(session):
    session.add_all([UnmetSearch(), UnmetSearch(zone="Chapalita", type="renta", bedrooms=3,
                                                budget_min=Decimal("0"), budget_max=Decimal("15000"))])
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(UnmetSearch(type="traspaso"))
        session.flush()


def test_deleting_a_lead_removes_its_interest_and_deals(session):
    """N-9 borra su historia sintética borrando los leads: lo que cuelga de ellos se va también."""
    _property(session)
    lead, advisor = _lead(session), _advisor(session)
    session.add_all([PropertyInterest(lead_id=lead.id, property_id="prop_n1", kind="consulta"),
                     Deal(property_id="prop_n1", lead_id=lead.id, advisor_id=advisor.id, kind="venta",
                          price=Decimal("1"))])
    session.flush()
    session.delete(lead)
    session.flush()
    for table in ("property_interest", "deals"):
        assert session.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_new_lead_and_property_columns_are_optional(session):
    lead, prop = _lead(session), _property(session)
    session.refresh(lead)
    assert (lead.utm_campaign, lead.synthetic, prop.photo_url) == (None, False, None)


# --- la migración ----------------------------------------------------------------------------------

def test_0008_keeps_existing_leads_and_properties_and_downgrades_cleanly():
    with temporary_database(migrate=False) as url:
        config = alembic_config(url)
        command.upgrade(config, "0007")
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO leads (telegram_user_id) VALUES ('tg_viejo')"))
            connection.execute(text(
                "INSERT INTO properties (id, title, price, type, zone, m2, bedrooms, bathrooms, address_approx,"
                " address_exact) VALUES ('prop_viejo', 't', 1, 'renta', 'z', 1, 1, 1, 'a', 'b')"))
        command.upgrade(config, "0008")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT synthetic, utm_campaign FROM leads")).one() == (False, None)
            assert connection.execute(text("SELECT photo_url FROM properties")).scalar() is None
        command.downgrade(config, "0007")
        with engine.connect() as connection:
            tables = set(connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars())
            assert not tables & {"property_status_events", "deals", "property_interest", "unmet_searches"}
            assert connection.execute(text("SELECT count(*) FROM leads")).scalar() == 1
        engine.dispose()
