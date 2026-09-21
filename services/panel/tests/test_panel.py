"""B-5: el asesor ve su cola y la ficha completa; todo lo demás es 404."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app, get_session
from db.models import Advisor, Conversation, Lead, LeadScore, Message, Property, Visit

PROVIDENCIA = "Providencia"
ZAPOPAN = "Zapopan"


@pytest.fixture
def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def client(engine, session_factory):
    """Cliente con la sesión apuntada a la base temporal del test."""
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


@pytest.fixture
def mundo(session_factory):
    """Dos asesores en zonas distintas y un lead con score, visita y transcripción."""
    with session_factory() as session:
        valeria = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Valeria Ortiz", zone=PROVIDENCIA,
                          access_token=uuid.uuid4().hex)
        andres = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Andrés Núñez", zone=ZAPOPAN,
                         access_token=uuid.uuid4().hex)
        lead = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}",
                    zone=PROVIDENCIA, status="nuevo", budget="3 millones")
        ajeno = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}",
                     zone=ZAPOPAN, status="nuevo")
        propiedad = Property(id=f"prop_{uuid.uuid4().hex[:6]}", title="Depa en Providencia",
                             price=3_000_000, type="venta", zone=PROVIDENCIA, m2=90,
                             bedrooms=2, bathrooms=2, address_approx="Av. Providencia",
                             address_exact="Av. Providencia 1234")
        # Los modelos usan FK sin relationship(), así que el orden de inserción es cosa nuestra:
        # primero las filas referenciadas, después las que apuntan a ellas.
        session.add_all([valeria, andres, lead, ajeno, propiedad])
        session.flush()

        conversation = Conversation(id=uuid.uuid4(), lead_id=lead.id, fsm_state="RECOMMEND")
        session.add(conversation)
        session.flush()

        moment = datetime.now(timezone.utc)
        session.add_all([
            LeadScore(lead_id=lead.id, need_fit=18, budget=15, authority=12, timing=14,
                      eligibility=0, total=59, band="WARM", reasons=["presupuesto declarado"]),
            Visit(id=uuid.uuid4(), lead_id=lead.id, property_id=propiedad.id, advisor_id=valeria.id,
                  scheduled_at=moment + timedelta(days=2), idempotency_key=uuid.uuid4().hex),
            Message(conversation_id=conversation.id, role="user", content="Hola, busco depa",
                    created_at=moment),
            Message(conversation_id=conversation.id, role="assistant", content="¡Hola! ¿En qué zona?",
                    created_at=moment + timedelta(microseconds=1)),
        ])
        session.commit()
        return {"valeria": valeria, "andres": andres, "lead": lead, "ajeno": ajeno}


def test_la_lista_muestra_solo_los_leads_de_la_zona_del_asesor(client, mundo):
    page = client.get(f"/panel/{mundo['valeria'].access_token}")

    assert page.status_code == 200
    assert mundo["lead"].telegram_user_id in page.text
    assert mundo["ajeno"].telegram_user_id not in page.text


def test_un_token_invalido_es_404(client, mundo):
    assert client.get("/panel/token-que-no-existe").status_code == 404


def test_un_asesor_sin_leads_ve_la_lista_vacia_sin_romper(client, session_factory):
    with session_factory() as session:
        sola = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Camila Reyes", zone="Andares",
                       access_token=uuid.uuid4().hex)
        session.add(sola)
        session.commit()
        token = sola.access_token

    page = client.get(f"/panel/{token}")

    assert page.status_code == 200
    assert "Todavía no hay leads" in page.text


def test_la_ficha_trae_score_visita_y_transcripcion_en_orden(client, mundo):
    page = client.get(f"/panel/{mundo['valeria'].access_token}/leads/{mundo['lead'].id}")

    assert page.status_code == 200
    assert "WARM" in page.text and "59" in page.text
    assert "3 millones" in page.text
    assert "Hola, busco depa" in page.text
    assert page.text.index("Hola, busco depa") < page.text.index("¡Hola! ¿En qué zona?")


def test_un_lead_de_otra_zona_es_404_igual_que_uno_inexistente(client, mundo):
    """El AC lo pide explícito: el código de estado no puede revelar si el lead existe."""
    ajeno = client.get(f"/panel/{mundo['valeria'].access_token}/leads/{mundo['ajeno'].id}")
    inexistente = client.get(f"/panel/{mundo['valeria'].access_token}/leads/{uuid.uuid4()}")

    assert ajeno.status_code == 404
    assert inexistente.status_code == 404
    assert ajeno.text == inexistente.text


def test_un_lead_sin_score_no_rompe_la_ficha(client, mundo, session_factory):
    """Es el estado normal hasta que A-9 puntúa: la ficha tiene que abrir igual."""
    with session_factory() as session:
        lead = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}",
                    zone=PROVIDENCIA, status="nuevo")
        session.add(lead)
        session.commit()
        lead_id = lead.id

    page = client.get(f"/panel/{mundo['valeria'].access_token}/leads/{lead_id}")

    assert page.status_code == 200
    assert "Todavía sin calificar" in page.text
