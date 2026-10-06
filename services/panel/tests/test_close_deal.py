"""N-3: el asesor cierra la operación desde la ficha del lead.

En una sola transacción: la fila en `deals`, el lead a `closed_won` y la propiedad a `cerrado` por
`db/property_history.py`, con el asesor como actor. Un cierre rechazado no escribe nada.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.inventory import LOCAL_TZ
from app.main import app, get_session
from app.repo import close_options, closing_time
from db.models import Advisor, Deal, Lead, Property, PropertyInterest, PropertyStatusEvent


@pytest.fixture
def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def zona():
    """Zona única por test: la base es compartida en el módulo y el selector se filtra por zona."""
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
    advisor = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Valeria", zone=zone,
                      access_token=uuid.uuid4().hex)
    session.add(advisor)
    session.flush()
    return advisor


def _lead(session, zone, *, advisor_id=None, synthetic=False) -> Lead:
    lead = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}", zone=zone, status="sql",
                advisor_id=advisor_id, synthetic=synthetic)
    session.add(lead)
    session.flush()
    return lead


def _property(session, zone, *, status="disponible", type_="venta", title="Depa") -> Property:
    prop = Property(id=f"prop_{uuid.uuid4().hex[:8]}", title=title, price=2_500_000, type=type_, kind="departamento",
                    zone=zone, m2=90, bedrooms=2, bathrooms=2, status=status, address_approx=zone,
                    address_exact=f"Calle 1, {zone}")
    session.add(prop)
    session.flush()
    return prop


def _interest(session, lead, prop, *, minutes_ago=0, kind="consulta", synthetic=False) -> None:
    session.add(PropertyInterest(lead_id=lead.id, property_id=prop.id, kind=kind, synthetic=synthetic,
                                 created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago)))
    session.flush()


@pytest.fixture
def mundo(session_factory, zona):
    """Una asesora, su lead, una propiedad que el lead consultó y otra disponible en la zona."""
    with session_factory() as session:
        valeria = _advisor(session, zona)
        lead = _lead(session, zona)
        consultada = _property(session, zona, title="Depa consultado")
        otra = _property(session, zona, title="Casa disponible", type_="renta")
        _interest(session, lead, consultada)
        session.commit()
        return {"valeria": valeria, "lead": lead, "consultada": consultada, "otra": otra}


def _close(client, advisor, lead, prop_id, *, kind="venta", price="2350000", closed_on=None):
    return client.post(f"/panel/{advisor.access_token}/leads/{lead.id}/cerrar", follow_redirects=False,
                       data={"property_id": prop_id, "kind": kind, "price": price,
                             "closed_on": closed_on or datetime.now(UTC).astimezone(LOCAL_TZ).date().isoformat()})


def _written(session_factory, lead, prop_id):
    """Lo que un cierre pudo escribir: deals del lead, estado del lead y de la propiedad, y sus eventos."""
    with session_factory() as session:
        return {
            "deals": session.execute(select(func.count()).select_from(Deal)
                                     .where(Deal.lead_id == lead.id)).scalar_one(),
            "lead": session.get(Lead, lead.id).status,
            "property": session.get(Property, prop_id).status,
            "events": session.execute(select(func.count()).select_from(PropertyStatusEvent)
                                      .where(PropertyStatusEvent.property_id == prop_id)).scalar_one(),
        }


# --- el cierre feliz ---------------------------------------------------------------------------------------

def test_closing_writes_the_deal_closes_the_lead_and_the_property_and_goes_back_to_the_card(
        client, session_factory, mundo):
    valeria, lead, prop = mundo["valeria"], mundo["lead"], mundo["consultada"]

    response = _close(client, valeria, lead, prop.id, price="2350000.50")

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/panel/{valeria.access_token}/leads/{lead.id}")
    with session_factory() as session:
        (deal,) = session.execute(select(Deal).where(Deal.lead_id == lead.id)).scalars()
        assert (deal.property_id, deal.advisor_id, deal.kind, deal.price, deal.synthetic) == (
            prop.id, valeria.id, "venta", Decimal("2350000.50"), False)
        assert session.get(Lead, lead.id).status == "closed_won"
        assert session.get(Property, prop.id).status == "cerrado"
        (event,) = session.execute(select(PropertyStatusEvent)
                                   .where(PropertyStatusEvent.property_id == prop.id)).scalars()
        assert (event.from_status, event.to_status, event.actor) == ("disponible", "cerrado", valeria.id)
        assert event.changed_at == deal.closed_at  # el historial y la operación cuentan el mismo momento


def test_the_card_shows_the_deal_after_closing(client, mundo):
    valeria, lead, prop = mundo["valeria"], mundo["lead"], mundo["consultada"]
    _close(client, valeria, lead, prop.id, price="2350000")

    page = client.get(f"/panel/{valeria.access_token}/leads/{lead.id}").text

    assert "No closed deal" not in page
    assert "Depa consultado" in page and "$2,350,000.00" in page
    assert "deal closed" in page  # la etiqueta del estado del lead
    assert "<td>sale</td>" in page and "<td>venta</td>" not in page  # #202: el tipo, en inglés


def test_a_lead_without_advisor_stays_with_whoever_closed_it(client, session_factory, mundo):
    _close(client, mundo["valeria"], mundo["lead"], mundo["consultada"].id)
    with session_factory() as session:
        assert session.get(Lead, mundo["lead"].id).advisor_id == mundo["valeria"].id


def test_a_past_date_closes_at_local_noon_of_that_day():
    now = datetime(2026, 10, 4, 18, 30, tzinfo=UTC)
    assert closing_time(date(2026, 10, 4), now) == now
    assert closing_time(date(2026, 9, 30), now) == datetime(2026, 9, 30, 12, tzinfo=LOCAL_TZ)


def test_a_past_closing_date_is_kept(client, session_factory, mundo):
    _close(client, mundo["valeria"], mundo["lead"], mundo["consultada"].id, closed_on="2026-09-01")
    with session_factory() as session:
        deal = session.execute(select(Deal).where(Deal.lead_id == mundo["lead"].id)).scalar_one()
        assert deal.closed_at == datetime(2026, 9, 1, 12, tzinfo=LOCAL_TZ)


def test_a_real_deal_counts_in_the_business_view(client, session_factory, mundo):
    """Las métricas de N-6 y N-7 leen `deals` sin la marca sintética: el cierre del panel ya cuenta."""
    valeria, lead, prop = mundo["valeria"], mundo["lead"], mundo["consultada"]
    _close(client, valeria, lead, prop.id)
    page = client.get(f"/panel/{valeria.access_token}/negocio?periodo=semana").text
    assert prop.title in page


# --- quién puede cerrar (DT-5, DT-42) ------------------------------------------------------------------------

def test_an_advisor_of_another_zone_gets_404_and_nothing_is_written(client, session_factory, mundo):
    with session_factory() as session:
        andres = _advisor(session, f"otra-{uuid.uuid4().hex[:6]}")
        session.commit()

    response = _close(client, andres, mundo["lead"], mundo["consultada"].id)

    assert response.status_code == 404
    assert _written(session_factory, mundo["lead"], mundo["consultada"].id) == {
        "deals": 0, "lead": "sql", "property": "disponible", "events": 0}


def test_the_assigned_advisor_of_another_zone_can_close(client, session_factory, zona):
    """Como en la ficha y "Marcar en gestión": el asesor asignado ve el lead aunque sea de otra zona."""
    with session_factory() as session:
        andres = _advisor(session, f"otra-{uuid.uuid4().hex[:6]}")
        lead = _lead(session, zona, advisor_id=andres.id)
        prop = _property(session, zona)
        session.commit()

    assert _close(client, andres, lead, prop.id).status_code == 303
    assert _written(session_factory, lead, prop.id)["lead"] == "closed_won"


def test_a_synthetic_lead_gets_404(client, session_factory, zona):
    with session_factory() as session:
        valeria = _advisor(session, zona)
        lead = _lead(session, zona, synthetic=True)
        prop = _property(session, zona)
        session.commit()

    assert _close(client, valeria, lead, prop.id).status_code == 404
    assert _written(session_factory, lead, prop.id)["events"] == 0


def test_an_unknown_token_or_lead_gets_the_same_404(client, mundo):
    valeria, prop = mundo["valeria"], mundo["consultada"]
    unknown_token = client.post(f"/panel/no-existe/leads/{mundo['lead'].id}/cerrar",
                                data={"property_id": prop.id, "kind": "venta", "price": "1", "closed_on": "2026-10-01"})
    unknown_lead = client.post(f"/panel/{valeria.access_token}/leads/{uuid.uuid4()}/cerrar",
                               data={"property_id": prop.id, "kind": "venta", "price": "1", "closed_on": "2026-10-01"})
    assert unknown_token.status_code == unknown_lead.status_code == 404
    assert unknown_token.text == unknown_lead.text


# --- un cierre rechazado no escribe nada --------------------------------------------------------------------------

def test_a_closed_property_cannot_be_closed_again(client, session_factory, mundo):
    valeria, lead, prop = mundo["valeria"], mundo["lead"], mundo["consultada"]
    with session_factory() as session:
        other_lead = _lead(session, lead.zone)
        session.commit()
    assert _close(client, valeria, other_lead, prop.id).status_code == 303  # otro lead la cerró antes

    response = _close(client, valeria, lead, prop.id)

    assert response.status_code == 422
    assert "That property is already off the market: nothing was recorded." in response.text
    assert _written(session_factory, lead, prop.id) == {"deals": 0, "lead": "sql", "property": "cerrado",
                                                        "events": 1}


def test_the_form_marks_a_closed_property_and_does_not_let_it_be_picked(client, session_factory, zona):
    with session_factory() as session:
        valeria = _advisor(session, zona)
        lead = _lead(session, zona)
        cerrada = _property(session, zona, status="cerrado", title="Ya vendida")
        _interest(session, lead, cerrada)
        session.commit()

    page = client.get(f"/panel/{valeria.access_token}/leads/{lead.id}").text

    option = page[page.index(f'value="{cerrada.id}"'):]
    assert option.index("disabled") < option.index(">") and "off the market" in option[:option.index("</option>")]


@pytest.mark.parametrize("field,value,message", [
    ("price", "-1", "cannot be negative"),
    ("price", "mucho", "has to be a number"),
    ("price", "NaN", "has to be a number"),
    ("price", "1e15", "too high"),
    ("price", "1e30", "too high"),  # más dígitos de los que `quantize` puede redondear
    ("kind", "permuta", "a rental or a sale"),
    ("closed_on", "ayer", "date is invalid"),
    ("closed_on", (date.today() + timedelta(days=2)).isoformat(), "cannot be in the future"),
])
def test_an_invalid_form_shows_the_error_and_writes_nothing(client, session_factory, mundo, field, value, message):
    valeria, lead, prop = mundo["valeria"], mundo["lead"], mundo["consultada"]
    response = _close(client, valeria, lead, prop.id, **{field: value})

    assert response.status_code == 422 and message in response.text
    assert _written(session_factory, lead, prop.id) == {"deals": 0, "lead": "sql", "property": "disponible",
                                                        "events": 0}


def test_a_property_that_is_not_offered_to_this_lead_is_rejected(client, session_factory, mundo):
    with session_factory() as session:
        ajena = _property(session, f"otra-{uuid.uuid4().hex[:6]}")
        session.commit()

    response = _close(client, mundo["valeria"], mundo["lead"], ajena.id)

    assert response.status_code == 422 and "not linked to this lead" in response.text
    assert _written(session_factory, mundo["lead"], ajena.id)["events"] == 0


def test_a_rejected_form_keeps_what_the_advisor_typed(client, mundo):
    response = _close(client, mundo["valeria"], mundo["lead"], mundo["consultada"].id, price="-5")
    assert 'value="-5"' in response.text
    assert f'value="{mundo["consultada"].id}" data-kind="venta" selected' in response.text


# --- el selector de propiedades ----------------------------------------------------------------------------------

def test_the_selector_lists_what_the_lead_touched_first_then_the_zone(session_factory, zona):
    with session_factory() as session:
        lead = _lead(session, zona)
        vieja = _property(session, zona, title="vista hace rato")
        reciente = _property(session, f"otra-{uuid.uuid4().hex[:6]}", title="de otra zona, consultada")
        apartada = _property(session, zona, status="apartado")
        cerrada_sin_tocar = _property(session, zona, status="cerrado")
        disponible = _property(session, zona)
        _property(session, f"otra-{uuid.uuid4().hex[:6]}")  # ni la tocó ni es de su zona
        sintetica = _property(session, f"otra-{uuid.uuid4().hex[:6]}")
        _interest(session, lead, vieja, minutes_ago=60)
        _interest(session, lead, vieja, minutes_ago=50, kind="busqueda")  # repetida: sale una vez
        _interest(session, lead, reciente, minutes_ago=5)
        _interest(session, lead, sintetica, synthetic=True)  # la historia de demo no cuenta

        options = close_options(session, lead)

    assert [(o.id, o.touched) for o in options] == [
        (reciente.id, True), (vieja.id, True),
        *sorted([(apartada.id, False), (disponible.id, False)]),
    ]
    assert cerrada_sin_tocar.id not in {o.id for o in options}


def test_a_lead_without_zone_only_sees_what_it_touched(session_factory):
    with session_factory() as session:
        lead = _lead(session, None)
        prop = _property(session, f"z-{uuid.uuid4().hex[:6]}")
        _interest(session, lead, prop)
        assert [o.id for o in close_options(session, lead)] == [prop.id]


def test_a_preventa_suggests_a_sale():
    from app.repo import PropertyOption

    assert PropertyOption("p", "t", "preventa", "disponible", touched=False).kind == "venta"
    assert PropertyOption("p", "t", "renta", "disponible", touched=False).kind == "renta"


def test_the_card_offers_the_close_button_with_today_as_default(client, mundo):
    page = client.get(f"/panel/{mundo['valeria'].access_token}/leads/{mundo['lead'].id}").text
    today = datetime.now(timezone.utc).astimezone(LOCAL_TZ).date().isoformat()
    assert "<summary>Close a deal</summary>" in page
    assert f'name="closed_on" max="{today}" required value="{today}"' in page
    assert page.index("The ones the lead was interested in") < page.index("Available in their area")
