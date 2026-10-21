"""#178: rediseño de UI/UX del panel — navegación, identificador legible, confirmación y gráficas.

Los tests de las historias (B-5, B-6, B-9, B-12, B-15, N-8) siguen cubriendo datos y rutas; acá se
comprueba lo que el rediseño agrega y que no rompe la privacidad ni las rutas.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app, get_session
from db.models import Advisor, Conversation, Lead, LeadScore


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


@pytest.fixture
def mundo(session_factory):
    """Un asesor con zona única y un lead HOT en gestión, para la navegación y las gráficas."""
    zone = f"ui-{uuid.uuid4().hex[:6]}"
    with session_factory() as session:
        advisor = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Valeria UI", zone=zone,
                          access_token=uuid.uuid4().hex)
        lead = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}", zone=zone,
                    status="mql", qualified_at=datetime.now(UTC) - timedelta(hours=1))
        session.add_all([advisor, lead])
        session.flush()
        session.add(LeadScore(lead_id=lead.id, need_fit=18, budget=15, authority=12, timing=14,
                              eligibility=0, total=90, band="HOT", reasons=[]))
        session.add(Conversation(id=uuid.uuid4(), lead_id=lead.id, fsm_state="RECOMMEND"))
        session.commit()
        return {"advisor": advisor, "lead": lead, "token": advisor.access_token}


def test_la_navegacion_esta_en_todas_las_vistas_y_marca_la_activa(client, mundo, login):
    # B-17: cada rol navega solo lo suyo. El asesor, su cola y sus avisos; el gerente, negocio y funnel.
    login(mundo["advisor"])
    pages = {"leads": client.get("/panel").text, "avisos": client.get("/panel/avisos").text}
    for name, page in pages.items():
        for href in ("/panel", "/panel/avisos"):
            assert f'href="{href}"' in page, (name, href)
        assert 'href="/negocio"' not in page and 'href="/panel/funnel"' not in page
        assert 'action="/logout"' in page
    assert 'href="/panel" aria-current="page"' in pages["leads"]
    assert 'href="/panel/avisos" aria-current="page"' in pages["avisos"]

    login()
    pages = {"funnel": client.get("/panel/funnel").text, "negocio": client.get("/negocio").text}
    for name, page in pages.items():
        for href in ("/negocio", "/panel/funnel"):
            assert f'href="{href}"' in page, (name, href)
        assert 'href="/panel"' not in page and 'href="/panel/avisos"' not in page
        assert 'action="/logout"' in page
    assert 'href="/panel/funnel" aria-current="page"' in pages["funnel"]
    assert 'href="/negocio" aria-current="page"' in pages["negocio"]


def test_la_cola_identifica_al_lead_de_forma_legible(client, mundo, login):
    login(mundo["advisor"])
    page = client.get("/panel").text
    short = str(mundo["lead"].id).replace("-", "")[:6]

    assert f"Lead {short}" in page
    # el identificador real sigue disponible (en el `title`) para no perder trazabilidad
    assert mundo["lead"].telegram_user_id in page


def test_marcar_en_gestion_muestra_una_confirmacion(client, mundo, login):
    lead = mundo["lead"]
    csrf = login(mundo["advisor"])

    page = client.post(f"/panel/leads/{lead.id}/gestionar", data={"csrf_token": csrf}, follow_redirects=True).text

    assert "Lead marked as in progress." in page


def test_las_graficas_son_svg_inline(client, mundo, login):
    login()
    assert '<svg class="bar"' in client.get("/panel/funnel").text


def test_la_ficha_muestra_el_score_con_barras(client, mundo, login):
    login(mundo["advisor"])
    page = client.get(f"/panel/leads/{mundo['lead'].id}").text

    assert "Score BANT-E" in page and '<svg class="bar"' in page


def test_el_modo_oscuro_sigue_la_preferencia_del_sistema(client, mundo, login):
    # Sin toggle ni JavaScript: los tokens oscuros se activan con prefers-color-scheme.
    login(mundo["advisor"])
    page = client.get("/panel").text

    assert "@media (prefers-color-scheme: dark)" in page
