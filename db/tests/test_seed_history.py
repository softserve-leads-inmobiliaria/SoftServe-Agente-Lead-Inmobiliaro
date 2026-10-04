"""Tests de N-9 contra un Postgres real: `scripts/seed_history.py` siembra seis meses de historia sintética.

La base del módulo tiene los asesores y el inventario de demo, como la del stack. Cada test vuelve a sembrar
la historia (el seed borra la anterior), y el que agrega filas reales las quita al terminar.
"""
import importlib.util
import sys
import uuid
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.orm import Session

from db.metrics import attribution, inventory, sla
from db.models import Lead, Property, PropertyInterest, PropertyStatusEvent, UnmetSearch
from db.property_history import record_listing
from db.repo_root import repo_root
from db.testing import temporary_database


def _load(name):
    spec = importlib.util.spec_from_file_location(name, repo_root() / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # los dataclasses del script buscan su módulo al definirse
    spec.loader.exec_module(module)
    return module


seed_advisors, seed_catalog, seed_history = (_load(n) for n in ("seed_advisors", "seed_catalog", "seed_history"))

TODAY = date(2026, 10, 3)
END = datetime.combine(TODAY, time.min, tzinfo=inventory.LOCAL_TZ)
START = END - timedelta(days=seed_history.DAYS + 1)
SYNTHETIC_TABLES = ("leads", "property_status_events", "deals", "property_interest", "unmet_searches")
# Lo que dejó el seed, sin los ids seriales: esos los da la secuencia de la base, no el seed.
SNAPSHOT = {
    "leads": "SELECT id, telegram_user_id, zone, status, advisor_id, qualified_at, claimed_at, utm_campaign,"
             " phone, email, created_at, updated_at FROM leads",
    "lead_scores": "SELECT * FROM lead_scores",
    "deals": "SELECT * FROM deals",
    "events": "SELECT property_id, from_status, to_status, changed_at, actor FROM property_status_events",
    "interest": "SELECT lead_id, property_id, kind, created_at FROM property_interest",
    "unmet": "SELECT zone, type, bedrooms, budget_min, budget_max, created_at FROM unmet_searches",
    "properties": "SELECT id, status FROM properties",
}


@pytest.fixture(scope="module")
def engine():
    with temporary_database() as url:
        engine = create_engine(url)
        seed_advisors.seed(engine)
        seed_catalog.seed(engine)
        yield engine
        engine.dispose()


@pytest.fixture
def summary(engine):
    return seed_history.seed(engine, today=TODAY)


def _snapshot(engine) -> dict[str, list]:
    with engine.connect() as connection:
        return {name: sorted(map(tuple, connection.execute(text(sql))), key=repr) for name, sql in SNAPSHOT.items()}


def _count(engine, table, where="true") -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar_one()


# --- el guion ------------------------------------------------------------------------------------------

def test_every_story_ends_where_the_catalog_is():
    catalog = {prop["id"]: prop["status"] for prop in seed_catalog.PROPERTIES}
    assert set(seed_history.TIMELINES) == set(catalog)
    assert {pid: steps[-1][2] for pid, steps in seed_history.TIMELINES.items()} == catalog
    for property_id in seed_history.DEALS:
        assert [s for _, _, s in seed_history.TIMELINES[property_id]].count("cerrado") == 1


# --- lo que escribe ------------------------------------------------------------------------------------

def test_everything_it_writes_is_synthetic_and_the_inventory_stays_as_it_was(engine, summary):
    assert summary.skipped == {}
    assert summary.leads == _count(engine, "leads") > 100
    assert summary.events == _count(engine, "property_status_events")
    assert summary.deals == _count(engine, "deals") == len(seed_history.DEALS)
    assert summary.interest == _count(engine, "property_interest") > 0
    assert summary.unmet == _count(engine, "unmet_searches") == seed_history.UNMET_SEARCHES
    for table in SYNTHETIC_TABLES:
        assert _count(engine, table, "NOT synthetic") == 0, table
    assert dict(_snapshot(engine)["properties"]) == {p["id"]: p["status"] for p in seed_catalog.PROPERTIES}


def test_its_leads_cannot_be_taken_for_real_ones(engine, summary):
    with Session(engine) as session:
        leads = session.execute(select(Lead)).scalars().all()
    assert all(lead.phone is None and lead.email is None for lead in leads)
    assert all(lead.telegram_user_id.startswith("synthetic-") for lead in leads)
    assert not any(lead.telegram_user_id.isdigit() for lead in leads)
    assert _count(engine, "funnel_events") == 0


def test_nothing_happens_after_the_day_before_the_demo(engine, summary):
    with engine.connect() as connection:
        latest = connection.execute(text(
            "SELECT greatest((SELECT max(changed_at) FROM property_status_events),"
            " (SELECT max(closed_at) FROM deals), (SELECT max(created_at) FROM property_interest),"
            " (SELECT max(claimed_at) FROM leads), (SELECT max(updated_at) FROM leads),"
            " (SELECT max(created_at) FROM unmet_searches))")).scalar_one()
        earliest = connection.execute(text("SELECT min(changed_at) FROM property_status_events")).scalar_one()
    assert END - timedelta(days=1) <= latest < END
    assert earliest >= END - timedelta(days=seed_history.DAYS)


def test_two_runs_leave_the_database_the_same(engine, summary):
    first = _snapshot(engine)
    again = seed_history.seed(engine, today=TODAY)
    assert again == summary
    assert _snapshot(engine) == first


# --- lo que muestra (propuestas.md §2.2) ---------------------------------------------------------------

def test_the_history_shows_the_cases_of_the_proposal(engine, summary):
    with Session(engine) as session:
        report = inventory.inventory_report(session, START, END, include_synthetic=True)
        bot = attribution.attribution_report(session, START, END, include_synthetic=True)
        claims = sla.claim_times(session, START, END, include_synthetic=True)

    rotation = {row.name: row.median_days for row in report.days_on_market_by_zone}
    assert max(rotation["Chapalita"], rotation["Providencia"]) < 20  # rotan rápido
    assert min(rotation["Andares"], rotation["Tlaquepaque"]) > 100  # rotan lento

    demand = {row.property_id: row.demand for row in report.stagnant}
    assert demand["prop_00231"] == inventory.INQUIRIES_WITHOUT_VISITS
    assert demand["prop_00232"] == demand["prop_00233"] == inventory.NO_INQUIRIES

    assert 0 < bot.attribution.attributed < bot.attribution.deals  # atribuibles y no atribuibles
    rates = {row.band: row.rate for row in bot.by_band}
    assert rates["HOT"] > rates["COLD"]

    assert [row.advisor for row in claims] == ["Andrés Núñez", "Camila Reyes", "Valeria Ortiz"]  # B-12
    assert report.unmet_demand[0].zone == "Providencia" and report.unmet_demand[0].type == "renta"


def test_the_last_week_has_something_to_show(engine, summary):
    with Session(engine) as session:
        week = inventory.placements(session, END - timedelta(days=7), END, include_synthetic=True)
    assert week.deals >= 1


def test_without_demo_the_views_do_not_see_it(engine, summary):
    with Session(engine) as session:
        report = inventory.inventory_report(session, START, END)
        bot = attribution.attribution_report(session, START, END)
        assert sla.claim_times(session, START, END) == []
    assert report.placements.deals == 0 and report.stagnant == [] and report.unmet_demand == []
    assert bot.attribution.deals == 0 and all(row.leads == 0 for row in bot.by_band)


# --- borrar y convivir con datos reales ----------------------------------------------------------------

def test_remove_takes_only_the_synthetic_history(engine, summary):
    with Session(engine) as session:
        real = Lead(telegram_user_id=f"{uuid.uuid4().int % 10**9}", zone="Providencia")
        session.add(real)
        session.flush()
        real_id = real.id
        session.add_all([PropertyInterest(lead_id=real_id, property_id="prop_00231", kind="consulta"),
                         UnmetSearch(zone="Providencia", type="renta")])
        session.commit()
    try:
        removed = seed_history.remove(engine)

        assert removed["leads"] == summary.leads and removed["events"] == summary.events
        for table in SYNTHETIC_TABLES:
            assert _count(engine, table, "synthetic") == 0, table
        assert _count(engine, "leads") == _count(engine, "property_interest") == _count(engine, "unmet_searches") == 1
        assert dict(_snapshot(engine)["properties"]) == {p["id"]: p["status"] for p in seed_catalog.PROPERTIES}
    finally:
        with Session(engine) as session:
            session.execute(delete(UnmetSearch).where(UnmetSearch.synthetic.is_(False)))
            session.execute(delete(Lead).where(Lead.id == real_id))
            session.commit()


def test_a_property_with_real_history_or_another_status_is_left_alone(engine):
    with Session(engine) as session:
        record_listing(session, "prop_00241", "adv_002")
        session.execute(update(Property).where(Property.id == "prop_00242").values(status="apartado"))
        session.commit()
    try:
        summary = seed_history.seed(engine, today=TODAY)

        assert set(summary.skipped) == {"prop_00241", "prop_00242"}
        assert "historial real" in summary.skipped["prop_00241"]
        assert _count(engine, "property_status_events", "property_id = 'prop_00241'") == 1
        assert _count(engine, "property_status_events", "property_id = 'prop_00242'") == 0
        assert _count(engine, "property_interest", "property_id IN ('prop_00241', 'prop_00242')") == 0
    finally:
        with Session(engine) as session:
            session.execute(delete(PropertyStatusEvent).where(PropertyStatusEvent.synthetic.is_(False)))
            session.execute(update(Property).where(Property.id == "prop_00242").values(status="disponible"))
            session.commit()


def test_a_skipped_closing_takes_its_lead_with_it(engine):
    with Session(engine) as session:
        record_listing(session, "prop_00239", "adv_002")
        session.commit()
    try:
        summary = seed_history.seed(engine, today=TODAY)
        assert "prop_00239" in summary.skipped
        assert summary.deals == len(seed_history.DEALS) - 1
        assert _count(engine, "leads", "status = 'closed_won'") == summary.deals
    finally:
        with Session(engine) as session:
            session.execute(delete(PropertyStatusEvent).where(PropertyStatusEvent.synthetic.is_(False)))
            session.commit()


def test_it_needs_the_advisors_and_the_catalog():
    with temporary_database() as url:
        empty = create_engine(url)
        try:
            with pytest.raises(RuntimeError, match="seed_advisors"):
                seed_history.seed(empty, today=TODAY)
            seed_advisors.seed(empty)
            with pytest.raises(RuntimeError, match="seed_catalog"):
                seed_history.seed(empty, today=TODAY)
        finally:
            empty.dispose()
