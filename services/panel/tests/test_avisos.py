"""B-15: los avisos del asesor (lead calificado, traspaso) en el panel, sin depender de Telegram."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import REFRESH_SECONDS, app, get_session
from db.models import Advisor, AdvisorNotification, Lead


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


def _advisor(session, zone: str) -> Advisor:
    advisor = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name=f"Asesor {zone}", zone=zone,
                      access_token=uuid.uuid4().hex)
    session.add(advisor)
    return advisor


@pytest.fixture
def mundo(session_factory):
    """Valeria (El Poblado) tiene un aviso viejo leído y uno nuevo; Andrés (Usaquen), uno propio.
    El lead del traspaso no tiene zona: la cola general se lo asignó a Valeria."""
    with session_factory() as session:
        valeria, andres = _advisor(session, "El Poblado"), _advisor(session, "Usaquen")
        session.flush()
        sin_zona = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}", zone=None,
                        advisor_id=valeria.id)
        de_andres = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}", zone="Usaquen",
                         advisor_id=andres.id)
        session.add_all([sin_zona, de_andres])
        session.flush()
        moment = datetime.now(timezone.utc)
        leido = AdvisorNotification(id=uuid.uuid4(), advisor_id=valeria.id, lead_id=sin_zona.id,
                                    kind="calificado", body="Lead calificado (WARM · 60/100)",
                                    created_at=moment - timedelta(hours=1), read_at=moment)
        nuevo = AdvisorNotification(id=uuid.uuid4(), advisor_id=valeria.id, lead_id=sin_zona.id,
                                    kind="traspaso", body="Traspaso de lead (HANDOFF): visita agendada",
                                    created_at=moment)
        ajeno = AdvisorNotification(id=uuid.uuid4(), advisor_id=andres.id, lead_id=de_andres.id,
                                    kind="calificado", body="Aviso de Andrés", created_at=moment)
        session.add_all([leido, nuevo, ajeno])
        session.commit()
        return {"valeria": valeria, "andres": andres, "sin_zona": sin_zona, "nuevo": nuevo, "ajeno": ajeno}


def test_el_asesor_ve_sus_avisos_los_nuevos_primero_y_no_los_de_otros(client, mundo):
    page = client.get(f"/panel/{mundo['valeria'].access_token}/avisos")

    assert page.status_code == 200
    assert page.text.index("visita agendada") < page.text.index("WARM · 60/100")
    assert "Aviso de Andrés" not in page.text
    assert "1 sin leer" in page.text
    assert f'http-equiv="refresh" content="{REFRESH_SECONDS}"' in page.text


def test_la_lista_de_leads_cuenta_los_avisos_sin_leer(client, mundo):
    page = client.get(f"/panel/{mundo['valeria'].access_token}")

    assert "1 sin leer" in page.text  # el contador de Avisos de la navegación (#178)


def test_abrir_un_aviso_lo_marca_leido_y_lleva_a_la_ficha(client, mundo):
    token, lead_id = mundo["valeria"].access_token, mundo["sin_zona"].id

    response = client.get(f"/panel/{token}/avisos/{mundo['nuevo'].id}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/panel/{token}/leads/{lead_id}"
    assert "0 sin leer" in client.get(f"/panel/{token}/avisos").text


def test_la_ficha_de_un_lead_asignado_abre_aunque_no_sea_de_su_zona(client, mundo):
    """La cola general le asigna a Valeria un lead sin zona: su aviso tiene que llevar a una ficha que abra."""
    token = mundo["valeria"].access_token

    assert client.get(f"/panel/{token}/leads/{mundo['sin_zona'].id}").status_code == 200
    assert mundo["sin_zona"].telegram_user_id in client.get(f"/panel/{token}").text
    # Andrés no lo tiene asignado ni es de su zona.
    assert client.get(f"/panel/{mundo['andres'].access_token}/leads/{mundo['sin_zona'].id}").status_code == 404


def test_el_aviso_de_otro_asesor_es_404_igual_que_uno_inexistente(client, mundo):
    token = mundo["valeria"].access_token

    ajeno = client.get(f"/panel/{token}/avisos/{mundo['ajeno'].id}", follow_redirects=False)
    inexistente = client.get(f"/panel/{token}/avisos/{uuid.uuid4()}", follow_redirects=False)

    assert ajeno.status_code == inexistente.status_code == 404
    assert ajeno.text == inexistente.text


def test_sin_avisos_la_pagina_lo_dice(client, session_factory):
    with session_factory() as session:
        sola = _advisor(session, "Envigado")
        session.commit()

    page = client.get(f"/panel/{sola.access_token}/avisos")

    assert page.status_code == 200
    assert "Todavía no tienes avisos" in page.text


def test_un_token_invalido_es_404(client):
    assert client.get("/panel/token-que-no-existe/avisos").status_code == 404
