"""B-16: la landing pública lista las propiedades disponibles y arma el enlace de Telegram por propiedad.

El enlace se pasa por `canonical.parse_deep_link` (B-3), el mismo que corre en el bot, para probar que
tocar el botón hace que el agente arranque con esa propiedad. Core y panel comparten el paquete `app`, así
que `canonical` se carga por ruta bajo un nombre propio: no se toca `services/core`.
"""
from __future__ import annotations

import importlib.util
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.landing import DEEP_LINK_PAYLOAD_MAX, bot_username_from_env, telegram_link
from app.main import app, get_session
from db.models import Advisor, Lead, Property
from services.catalog.app.data import PROPERTIES

BOT = "demo_leads_bot"
PAYLOAD = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _load_canonical():
    path = Path(__file__).resolve().parents[2] / "core" / "app" / "channels" / "canonical.py"
    spec = importlib.util.spec_from_file_location("core_canonical", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


canonical = _load_canonical()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


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


def _property(session, *, title, status="disponible", photo=True) -> Property:
    prop = Property(
        id=f"prop_{uuid.uuid4().hex[:8]}", title=title, price=Decimal("185000"), type="venta",
        kind="departamento", zone="El Poblado", m2=Decimal("78.5"), bedrooms=2,
        bathrooms=Decimal("2.0"), address_approx="El Poblado, Medellin",
        address_exact="Carrera 37 # 8A-24, El Poblado", status=status,
        photo_url="https://images.example/photo.jpg" if photo else None)
    session.add(prop)
    return prop


@pytest.fixture
def mundo(session_factory):
    """Dos propiedades publicables (una sin foto), una apartada y una cerrada, más un asesor y un lead:
    la landing solo puede mostrar las dos primeras y ningún dato del panel."""
    with session_factory() as session:
        advisor = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Valeria Ortiz", zone="El Poblado",
                          access_token=f"tok-secreto-{uuid.uuid4().hex}")
        lead = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-secreto-{uuid.uuid4().hex}", zone="El Poblado")
        disponible = _property(session, title="Apartment with photo")
        sin_foto = _property(session, title="Apartment without photo", photo=False)
        apartado = _property(session, title="Reserved apartment", status="apartado")
        cerrado = _property(session, title="Off-market apartment", status="cerrado")
        session.add_all([advisor, lead])
        session.commit()
        return {"disponible": disponible, "sin_foto": sin_foto, "apartado": apartado,
                "cerrado": cerrado, "advisor": advisor, "lead": lead}


def test_la_landing_es_publica_y_solo_lista_disponibles(client, mundo):
    page = client.get("/properties")

    assert page.status_code == 200
    assert mundo["disponible"].title in page.text
    assert mundo["sin_foto"].title in page.text
    assert mundo["apartado"].title not in page.text
    assert mundo["cerrado"].title not in page.text


def test_cada_tarjeta_arma_el_enlace_que_el_bot_entiende(client, mundo, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", BOT)

    page = client.get("/properties").text

    for prop in (mundo["disponible"], mundo["sin_foto"]):
        href = f'https://t.me/{BOT}?start={prop.id}'
        assert f'href="{href}"' in page
        payload = href.split("start=", 1)[1]
        assert PAYLOAD.fullmatch(payload) and len(payload) <= DEEP_LINK_PAYLOAD_MAX
        assert canonical.parse_deep_link(payload) == {"property_id": prop.id}


@pytest.mark.parametrize("prop", PROPERTIES, ids=lambda prop: prop["id"])
def test_el_enlace_de_cada_propiedad_sembrada_lo_entiende_el_bot(prop):
    """El AC: se genera el enlace de cada propiedad del seed y `parse_deep_link` devuelve su `property_id`."""
    payload = telegram_link(BOT, prop["id"]).split("start=", 1)[1]

    assert PAYLOAD.fullmatch(payload) and len(payload) <= DEEP_LINK_PAYLOAD_MAX
    assert canonical.parse_deep_link(payload) == {"property_id": prop["id"]}


def test_la_landing_no_expone_datos_reservados_ni_del_panel(client, mundo, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", BOT)

    page = client.get("/properties").text

    assert mundo["disponible"].address_exact not in page  # dirección reservada (guardrails.md)
    assert mundo["advisor"].access_token not in page
    assert mundo["lead"].telegram_user_id not in page
    assert "/panel/" not in page  # nada del resto del panel


def test_sin_usuario_de_bot_la_landing_no_muestra_boton(client, mundo, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "")

    page = client.get("/properties").text

    assert "t.me" not in page
    assert "Telegram isn't configured" in page
    assert mundo["disponible"].title in page  # las tarjetas salen igual


def test_la_landing_es_de_solo_lectura(client, mundo, session_factory):
    def count() -> int:
        with session_factory() as session:
            return session.execute(select(func.count()).select_from(Property)).scalar_one()

    before = count()
    client.get("/properties")
    assert count() == before


def test_la_landing_se_ve_bien_en_un_celular(client, mundo):
    page = client.get("/properties").text

    assert 'name="viewport"' in page
    assert "grid-template-columns: repeat(auto-fill" in page


@pytest.mark.parametrize("raw, expected", [
    ("demo_bot", "demo_bot"),
    ("@demo_bot", "demo_bot"),
    ("  demo_bot  ", "demo_bot"),
    ("", None),
    ("@", None),
])
def test_bot_username_from_env(monkeypatch, raw, expected):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", raw)
    assert bot_username_from_env() == expected
