"""N-8: la vista de inventario y negocio muestra las consultas de N-6 y N-7 del periodo, solo agregados."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.business import INVALID_RANGE, parse_period
from app.main import app, get_session
from db.models import Advisor, Deal, Lead, LeadScore, Property, PropertyInterest, UnmetSearch
from db.property_history import record_listing, set_status
from db.testing import temporary_database

TODAY = date(2026, 10, 3)  # sábado
# La semana del 14 al 20 de septiembre, pedida como rango para que el test no dependa de la fecha de hoy.
WEEK = "/panel/tok-v/negocio?periodo=rango&desde=2026-09-14&hasta=2026-09-20"
IN_WEEK = datetime(2026, 9, 16, 18, tzinfo=UTC)
EMPTY = "Sin datos en este periodo."
SECTIONS = 9
TOTALS = {"HOT": 90, "WARM": 50, "COLD": 10}  # el total tiene que caer en la banda

PHONE, EMAIL, TELEGRAM = "3312345678", "lead@ejemplo.com", "tg-secreto-n8"


@pytest.fixture
def clean_engine():
    """Base propia: la vista es global y cualquier resto de otros tests contaminaría los conteos."""
    with temporary_database() as url:
        engine = create_engine(url)
        yield engine
        engine.dispose()


@pytest.fixture
def session_factory(clean_engine):
    factory = sessionmaker(clean_engine, expire_on_commit=False)
    with factory() as session:
        session.add(Advisor(id="adv_v", name="Valeria", zone="Providencia", access_token="tok-v"))
        session.commit()
    return factory


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


def _property(session, pid, *, zone, title, listed, status_now="disponible", synthetic=False):
    session.add(Property(id=pid, title=title, price=2_500_000, type="venta", zone=zone, m2=100, bedrooms=2,
                         bathrooms=1, address_approx="cerca del parque", address_exact="Calle Reservada 123"))
    session.flush()
    record_listing(session, pid, "test", at=listed, synthetic=synthetic)
    if status_now != "disponible":
        set_status(session, pid, status_now, "adv_v", at=IN_WEEK, synthetic=synthetic)


def _lead(session, *, campaign=None, band="HOT", synthetic=False, **fields) -> Lead:
    lead = Lead(telegram_user_id=fields.pop("telegram_user_id", f"tg-{uuid.uuid4().hex}"),
                utm_campaign=campaign, synthetic=synthetic, created_at=IN_WEEK - timedelta(days=1), **fields)
    session.add(lead)
    session.flush()
    session.add(LeadScore(lead_id=lead.id, need_fit=0, budget=0, authority=0, timing=0, eligibility=0,
                          total=TOTALS[band], band=band, reasons=[]))
    return lead


@pytest.fixture
def mundo(session_factory):
    """Una venta en Providencia atribuida al bot, una estancada en Zapopan y una búsqueda sin resultado.
    Más una venta sintética en Andares, que solo aparece con `demo=1`."""
    with session_factory() as session:
        lead = _lead(session, campaign="feria-sep", status="closed_won", phone=PHONE, email=EMAIL,
                     telegram_user_id=TELEGRAM)
        _property(session, "prop_vendida", zone="Providencia", title="Casa Jardín",
                  listed=IN_WEEK - timedelta(days=40), status_now="cerrado")
        session.add(PropertyInterest(lead_id=lead.id, property_id="prop_vendida", kind="consulta",
                                     created_at=IN_WEEK - timedelta(days=2)))
        session.add(Deal(property_id="prop_vendida", lead_id=lead.id, advisor_id="adv_v", kind="venta",
                         price=Decimal("2400000"), closed_at=IN_WEEK))
        _property(session, "prop_quieta", zone="Zapopan", title="Depto Olvidado",
                  listed=IN_WEEK - timedelta(days=75))
        session.add(UnmetSearch(zone="Chapalita", type="renta", bedrooms=3, created_at=IN_WEEK))

        demo = _lead(session, synthetic=True, band="WARM")
        _property(session, "prop_demo", zone="Andares", title="Casa Demo", listed=IN_WEEK - timedelta(days=20),
                  status_now="cerrado", synthetic=True)
        session.add(Deal(property_id="prop_demo", lead_id=demo.id, advisor_id="adv_v", kind="venta",
                         price=Decimal("5000000"), closed_at=IN_WEEK, synthetic=True))
        session.commit()
        return lead.id


# --- periodo ---------------------------------------------------------------------------------------------

def test_the_default_period_is_the_last_seven_days_including_today():
    period = parse_period(None, None, None, today=TODAY)

    assert period.kind == "semana"
    assert (period.first_day, period.last_day) == (date(2026, 9, 27), TODAY)
    assert period.end - period.start == timedelta(days=7)
    assert period.error is None


def test_the_month_is_the_last_thirty_days():
    period = parse_period("mes", None, None, today=TODAY)

    assert (period.first_day, period.last_day) == (date(2026, 9, 4), TODAY)


def test_a_range_includes_both_days():
    period = parse_period("rango", "2026-09-14", "2026-09-20", today=TODAY)

    assert period.kind == "rango"
    assert period.start.date() == date(2026, 9, 14)
    assert period.end.date() == date(2026, 9, 21)  # la medianoche que cierra el día 20
    assert period.start.utcoffset() == timedelta(hours=-6)


@pytest.mark.parametrize("since, until", [
    ("2026-09-20", "2026-09-14"),  # termina antes de empezar
    ("ayer", "2026-09-14"),
    (None, "2026-09-14"),
    ("2024-01-01", "2026-09-14"),  # más de un año
])
def test_a_range_that_is_not_valid_falls_back_to_the_week_with_a_notice(since, until):
    period = parse_period("rango", since, until, today=TODAY)

    assert period.kind == "semana"
    assert period.error == INVALID_RANGE


def test_an_unknown_period_is_the_week():
    assert parse_period("trimestre", None, None, today=TODAY).kind == "semana"


# --- la página -------------------------------------------------------------------------------------------

def test_a_token_that_does_not_exist_is_404(client):
    assert client.get("/panel/token-falso/negocio").status_code == 404


def test_with_an_empty_database_every_section_says_no_data(client):
    page = client.get("/panel/tok-v/negocio")

    assert page.status_code == 200
    assert page.text.count(EMPTY) == SECTIONS


def test_the_page_shows_the_inventory_and_attribution_of_the_period(client, mundo):
    page = client.get(WEEK)

    assert page.status_code == 200
    assert "Del 14/09/2026 al 20/09/2026" in page.text
    assert "1 operaciones cerradas por $2,400,000" in page.text
    assert "Casa Jardín" in page.text  # en el ranking
    assert "Depto Olvidado" in page.text and "sin consultas" in page.text  # estancada, +60
    assert "+60" in page.text
    assert "Chapalita" in page.text  # búsqueda sin resultado
    assert "100%" in page.text  # el único cierre es del bot
    assert "feria-sep" in page.text


def test_no_section_shows_a_lead(client, mundo):
    page = client.get(WEEK + "&demo=1").text

    for secret in (PHONE, EMAIL, TELEGRAM, str(mundo), "/leads/", "Calle Reservada"):
        assert secret not in page


def test_the_synthetic_history_only_shows_up_when_asked(client, mundo):
    assert "Andares" not in client.get(WEEK).text

    with_demo = client.get(WEEK + "&demo=1").text
    assert "Andares" in with_demo
    assert "2 operaciones cerradas" in with_demo
    assert "Incluye la historia sintética" in with_demo


def test_an_invalid_range_shows_the_week_with_a_notice(client):
    page = client.get("/panel/tok-v/negocio?periodo=rango&desde=2026-09-20&hasta=2026-09-14")

    assert page.status_code == 200
    assert INVALID_RANGE in page.text


def test_the_funnel_links_to_the_view_and_back(client):
    assert 'href="/panel/tok-v/negocio"' in client.get("/panel/tok-v/funnel").text
    assert 'href="/panel/tok-v/funnel"' in client.get("/panel/tok-v/negocio").text


def test_the_view_shows_how_long_each_advisor_takes_to_claim(client, session_factory):
    with session_factory() as session:
        _lead(session, status="sql", advisor_id="adv_v", qualified_at=IN_WEEK,
              claimed_at=IN_WEEK + timedelta(minutes=20))
        session.commit()

    page = client.get(WEEK).text

    section = page.split("Tiempo de atención del asesor")[1].split("</table>")[0]
    assert "Valeria" in section and "20 min" in section
