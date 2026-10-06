"""B-6: la cola se ordena por calidad del lead y el asesor puede tomarlo ("marcar en gestión")."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.main import app, get_session
from app.repo import claim_lead, leads_for_advisor
from db.models import Advisor, Conversation, Lead, LeadScore


@pytest.fixture
def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def zona():
    """Zona única por test: la base es compartida en el módulo y la cola se filtra por zona."""
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


def _lead(session, zone, *, status="nuevo", created_at=None, band=None, total=None) -> Lead:
    lead = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}", zone=zone,
                status=status, created_at=created_at or datetime.now(timezone.utc))
    session.add(lead)
    session.flush()
    if band:
        session.add(LeadScore(lead_id=lead.id, need_fit=0, budget=0, authority=0, timing=0,
                              eligibility=0, total=total, band=band, reasons=[]))
        session.flush()
    return lead


def test_la_cola_va_por_banda_y_score_no_por_fecha(session_factory, zona):
    """Leads creados en orden inverso al de calidad: el más reciente es el mejor."""
    with session_factory() as session:
        valeria = _advisor(session, zona)
        t0 = datetime.now(timezone.utc)
        sin_score = _lead(session, zona, created_at=t0)
        cold = _lead(session, zona, created_at=t0 + timedelta(minutes=1), band="COLD", total=20)
        warm_bajo = _lead(session, zona, created_at=t0 + timedelta(minutes=2), band="WARM", total=45)
        warm_alto = _lead(session, zona, created_at=t0 + timedelta(minutes=3), band="WARM", total=65)
        hot = _lead(session, zona, created_at=t0 + timedelta(minutes=4), band="HOT", total=90)
        session.commit()

        ids = [lead.id for lead in leads_for_advisor(session, valeria)]
    assert ids == [hot.id, warm_alto.id, warm_bajo.id, cold.id, sin_score.id]


def test_un_lead_con_varias_conversaciones_aparece_una_sola_vez(session_factory, zona):
    """Tras un estado terminal se abre otra conversación (DT-20): la cola muestra la más reciente."""
    with session_factory() as session:
        valeria = _advisor(session, zona)
        lead = _lead(session, zona)
        t0 = datetime.now(timezone.utc)
        session.add(Conversation(id=uuid.uuid4(), lead_id=lead.id, fsm_state="HANDOFF", started_at=t0))
        session.add(Conversation(id=uuid.uuid4(), lead_id=lead.id, fsm_state="DISCOVERY",
                                 started_at=t0 + timedelta(minutes=5)))
        session.commit()

        filas = leads_for_advisor(session, valeria)
    assert len(filas) == 1
    assert filas[0].fsm_state == "DISCOVERY"


def test_marcar_en_gestion_pasa_a_sql_y_asigna_al_asesor(client, session_factory, zona):
    with session_factory() as session:
        valeria = _advisor(session, zona)
        lead = _lead(session, zona, status="mql")
        session.commit()

    response = client.post(f"/panel/{valeria.access_token}/leads/{lead.id}/gestionar",
                           follow_redirects=False)

    assert response.status_code == 303
    assert f"/leads/{lead.id}" in response.headers["location"]
    with session_factory() as session:
        fresh = session.execute(select(Lead).where(Lead.id == lead.id)).scalar_one()
    assert fresh.status == "sql" and fresh.advisor_id == valeria.id


def test_marcar_dos_veces_es_inocuo_y_respeta_a_quien_ya_lo_tenia(session_factory, zona):
    with session_factory() as session:
        valeria = _advisor(session, zona)
        otro = _advisor(session, zona)
        lead = _lead(session, zona, status="mql")
        lead.advisor_id = otro.id  # B-7 ya lo asignó a otro asesor de la zona
        session.flush()

        assert claim_lead(session, valeria, lead.id) is True
        assert claim_lead(session, valeria, lead.id) is True
        fresh = session.execute(select(Lead).where(Lead.id == lead.id)).scalar_one()
    assert fresh.status == "sql" and fresh.advisor_id == otro.id


def test_marcar_un_lead_de_otra_zona_es_404_y_no_lo_toca(client, session_factory, zona):
    with session_factory() as session:
        valeria = _advisor(session, zona)
        ajeno = _lead(session, f"otra-{zona}", status="mql")
        session.commit()

    response = client.post(f"/panel/{valeria.access_token}/leads/{ajeno.id}/gestionar",
                           follow_redirects=False)

    assert response.status_code == 404
    with session_factory() as session:
        fresh = session.execute(select(Lead).where(Lead.id == ajeno.id)).scalar_one()
    assert fresh.status == "mql" and fresh.advisor_id is None


def test_la_lista_y_la_ficha_muestran_el_boton_solo_si_corresponde(client, session_factory, zona):
    with session_factory() as session:
        valeria = _advisor(session, zona)
        abierto = _lead(session, zona, status="mql")
        tomado = _lead(session, zona, status="sql")
        session.commit()

    lista = client.get(f"/panel/{valeria.access_token}").text
    assert lista.count("Mark as in progress") == 1
    assert "in progress" in lista

    ficha_tomado = client.get(f"/panel/{valeria.access_token}/leads/{tomado.id}").text
    assert "Mark as in progress" not in ficha_tomado
    ficha_abierto = client.get(f"/panel/{valeria.access_token}/leads/{abierto.id}").text
    assert "Mark as in progress" in ficha_abierto


def test_la_historia_sintetica_no_aparece_en_la_cola_ni_se_abre(client, session_factory, zona):
    # N-9: sus leads solo existen para la vista de negocio; nadie los atiende.
    with session_factory() as session:
        valeria = _advisor(session, zona)
        real = _lead(session, zona, status="mql")
        sintetico = Lead(id=uuid.uuid4(), telegram_user_id=f"synthetic-{uuid.uuid4().hex[:4]}", zone=zona,
                         status="mql", advisor_id=valeria.id, synthetic=True)
        session.add(sintetico)
        session.commit()
        assert [lead.id for lead in leads_for_advisor(session, valeria)] == [real.id]

    token = valeria.access_token
    assert client.get(f"/panel/{token}/leads/{sintetico.id}").status_code == 404
    assert client.post(f"/panel/{token}/leads/{sintetico.id}/gestionar", follow_redirects=False).status_code == 404
