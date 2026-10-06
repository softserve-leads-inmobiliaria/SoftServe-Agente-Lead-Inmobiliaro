"""B-9: el dashboard cuenta bien contra un funnel sembrado a mano, y no rompe con la base vacía."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.funnel import funnel_report
from app.main import app, get_session
from db.models import Advisor, Conversation, FunnelEvent, Lead, LeadScore, Message
from db.testing import temporary_database


@pytest.fixture
def clean_engine():
    """Base propia: el dashboard es global y cualquier resto de otros tests contaminaría los conteos."""
    from sqlalchemy import create_engine
    with temporary_database() as url:
        engine = create_engine(url)
        yield engine
        engine.dispose()


@pytest.fixture
def session_factory(clean_engine):
    return sessionmaker(clean_engine, expire_on_commit=False)


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


def _lead(session, *, zone, advisor=None, status="nuevo", band=None, total=0, reached=(),
          handed_off=False, reply_after=None) -> Lead:
    lead = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}", zone=zone,
                status=status, advisor_id=advisor.id if advisor else None)
    session.add(lead)
    session.flush()
    if band:
        session.add(LeadScore(lead_id=lead.id, need_fit=0, budget=0, authority=0, timing=0,
                              eligibility=0, total=total, band=band, reasons=[]))
    for state in reached:
        session.add(FunnelEvent(lead_id=lead.id, event_type=f"state.{state}"))
    if handed_off:
        session.add(FunnelEvent(lead_id=lead.id, event_type="state.HUMAN_HANDOFF"))
    if reply_after is not None:
        started = datetime.now(timezone.utc)
        conversation = Conversation(id=uuid.uuid4(), lead_id=lead.id, started_at=started)
        session.add(conversation)
        session.flush()
        session.add(Message(conversation_id=conversation.id, role="assistant", content="hola",
                            created_at=started + reply_after))
    session.flush()
    return lead


@pytest.fixture
def mundo(session_factory):
    """4 leads: 2 en El Poblado (una HOT asignada, en gestión), 1 en Usaquen traspasado, 1 sin zona."""
    with session_factory() as session:
        valeria = Advisor(id="adv_v", name="Valeria", zone="El Poblado", access_token="tok-v")
        session.add(valeria)
        session.flush()
        _lead(session, zone="El Poblado", advisor=valeria, status="sql", band="HOT", total=90,
              reached=("DISCOVERY", "QUALIFY", "RECOMMEND"), reply_after=timedelta(seconds=2))
        _lead(session, zone="El Poblado", band="COLD", total=10, reached=("DISCOVERY",),
              reply_after=timedelta(seconds=4))
        _lead(session, zone="Usaquen", band="WARM", total=50, reached=("DISCOVERY", "QUALIFY"),
              handed_off=True, reply_after=timedelta(seconds=6))
        _lead(session, zone=None)  # nunca disparó una tool: sin zona, sin transición
        session.commit()
        return valeria


def test_los_conteos_por_etapa_son_leads_distintos_que_llegaron(session_factory, mundo):
    with session_factory() as session:
        report = funnel_report(session)

    assert report.total_leads == 4
    por_etapa = {s.name: s.leads for s in report.happy_path + report.side_states}
    assert por_etapa["DISCOVERY"] == 3
    assert por_etapa["QUALIFY"] == 2
    assert por_etapa["RECOMMEND"] == 1
    assert por_etapa["SCHEDULE"] == 0
    assert por_etapa["HUMAN_HANDOFF"] == 1
    assert report.happy_path[0].share == pytest.approx(0.75)


def test_la_primera_respuesta_es_la_mediana_de_las_conversaciones(session_factory, mundo):
    with session_factory() as session:
        report = funnel_report(session)

    assert report.first_response.conversations == 3
    assert report.first_response.p50 == timedelta(seconds=4)
    assert report.first_response.p90 > timedelta(seconds=4)


def test_la_contencion_es_la_parte_que_no_pidio_un_humano(session_factory, mundo):
    with session_factory() as session:
        report = funnel_report(session)

    assert report.handed_off == 1
    assert report.containment_rate == pytest.approx(0.75)


def test_el_corte_por_zona_y_por_asesor(session_factory, mundo):
    with session_factory() as session:
        report = funnel_report(session)

    zonas = {row.label: row for row in report.by_zone}
    assert zonas["El Poblado"].leads == 2 and zonas["El Poblado"].hot == 1
    assert zonas["Usaquen"].warm == 1
    assert zonas["no area"].leads == 1

    asesores = {row.label: row for row in report.by_advisor}
    assert asesores["Valeria"].leads == 1 and asesores["Valeria"].managed == 1
    assert asesores["unassigned"].leads == 3
    assert report.by_advisor[-1].label == "unassigned"  # los no asignados van al final


def test_la_historia_sintetica_no_cuenta_en_el_funnel(session_factory, mundo):
    # N-9 siembra leads sintéticos para la vista de negocio; el funnel es de la operación real.
    with session_factory() as session:
        session.add(Lead(telegram_user_id="synthetic-0001", zone="El Poblado", advisor_id=mundo.id,
                         status="sql", synthetic=True))
        session.commit()
        report = funnel_report(session)

    assert report.total_leads == 4
    assert {row.label: row.leads for row in report.by_zone}["El Poblado"] == 2
    assert {row.label: row.leads for row in report.by_advisor}["Valeria"] == 1


def test_con_la_base_vacia_renderiza_sin_romper(client, session_factory):
    with session_factory() as session:
        session.add(Advisor(id="adv_solo", name="Sola", zone="Envigado", access_token="tok-solo"))
        session.commit()
        assert session.execute(text("SELECT count(*) FROM leads")).scalar_one() == 0

    page = client.get("/panel/tok-solo/funnel")

    assert page.status_code == 200
    assert "0 leads started" in page.text
    assert "No leads yet" in page.text


def test_con_un_solo_lead_el_texto_va_en_singular(client, session_factory):
    with session_factory() as session:
        session.add(Advisor(id="adv_solo", name="Sola", zone="Envigado", access_token="tok-solo"))
        _lead(session, zone="Envigado", reached=("DISCOVERY",), reply_after=timedelta(seconds=3))
        session.commit()

    page = client.get("/panel/tok-solo/funnel").text

    assert "1 lead started a conversation" in page
    assert "over 1 conversation<" in page
    assert "1 conversations" not in page


def test_el_dashboard_es_global_y_exige_token_valido(client, session_factory, mundo):
    page = client.get("/panel/tok-v/funnel")
    assert page.status_code == 200
    assert "Usaquen" in page.text  # Valeria es de El Poblado y ve el negocio entero
    assert "75%" in page.text       # contención

    assert client.get("/panel/token-falso/funnel").status_code == 404
