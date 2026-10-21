"""B-17: login del panel con roles de asesor y gerente, sesión firmada, CSRF y las rutas viejas con token."""
from __future__ import annotations

import time
import uuid

import pytest
from conftest import MANAGER, PASSWORD
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy.orm import sessionmaker

from app.auth import EXAMPLE_SESSION_SECRET, InvalidSessionSecret, session_secret_from_env
from app.main import LOGIN_ERROR, app, get_session
from db.models import Advisor, Lead


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
    """Valeria y Andrés en zonas distintas, cada uno con un lead de su zona."""
    zone = f"zona-{uuid.uuid4().hex[:6]}"
    with session_factory() as session:
        valeria = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Valeria", zone=zone, access_token=uuid.uuid4().hex)
        andres = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Andrés", zone=f"otra-{zone}",
                         access_token=uuid.uuid4().hex)
        session.add_all([valeria, andres])
        session.flush()
        suyo = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}", zone=zone, status="mql")
        ajeno = Lead(id=uuid.uuid4(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}", zone=andres.zone, status="mql")
        session.add_all([suyo, ajeno])
        session.commit()
        return {"valeria": valeria, "andres": andres, "suyo": suyo, "ajeno": ajeno}


PROTECTED = ["/panel", "/panel/avisos", f"/panel/leads/{uuid.uuid4()}", f"/panel/avisos/{uuid.uuid4()}",
             "/panel/funnel", "/negocio"]


# --- login y logout ------------------------------------------------------------------------------------------

def test_the_advisor_lands_on_the_queue_and_the_manager_on_the_business_view(client, mundo, login):
    login(mundo["valeria"])
    assert client.get("/panel").status_code == 200
    response = client.post("/login", data={"username": mundo["valeria"].id, "password": PASSWORD},
                           follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/panel"

    login()
    response = client.post("/login", data={"username": MANAGER, "password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/negocio"


def test_the_login_page_is_public(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert 'name="username"' in page.text and 'type="password"' in page.text


def test_a_wrong_password_and_an_unknown_user_get_the_same_answer(client, mundo, login):
    login(mundo["valeria"])
    client.cookies.clear()

    wrong = client.post("/login", data={"username": mundo["valeria"].id, "password": "otra"})
    unknown = client.post("/login", data={"username": "nadie", "password": PASSWORD})

    assert wrong.status_code == unknown.status_code == 200
    assert LOGIN_ERROR in wrong.text and LOGIN_ERROR in unknown.text
    assert wrong.text.replace(mundo["valeria"].id, "nadie") == unknown.text  # solo cambia el usuario tecleado
    assert client.get("/panel").url.path == "/login"  # ninguno de los dos abrió sesión


def test_without_a_session_every_protected_route_goes_to_login(client):
    for path in PROTECTED:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login", path


def test_logout_closes_the_session(client, mundo, login):
    csrf = login(mundo["valeria"])

    response = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert client.get("/panel").url.path == "/login"


def test_an_expired_session_goes_back_to_login(client, mundo, login, monkeypatch):
    login(mundo["valeria"])
    assert client.get("/panel").url.path == "/panel"

    later = int(time.time()) + 8 * 60 * 60 + 1
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: later)

    assert client.get("/panel").url.path == "/login"


def test_the_session_cookie_is_httponly_and_samesite_lax(client, mundo, login):
    login(mundo["valeria"])
    response = client.post("/login", data={"username": mundo["valeria"].id, "password": PASSWORD},
                           follow_redirects=False)

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=28800" in cookie
    assert PASSWORD not in response.headers["set-cookie"]


# --- permisos: el mismo 404 de siempre (DT-5) --------------------------------------------------------------

def test_an_advisor_gets_404_on_the_manager_views_and_on_another_advisors_lead(client, mundo, login):
    login(mundo["valeria"])
    not_found = client.get(f"/panel/leads/{uuid.uuid4()}")

    for path in ("/negocio", "/panel/funnel", f"/panel/leads/{mundo['ajeno'].id}"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.text == not_found.text, path


def test_the_manager_gets_404_on_the_queue_the_card_and_the_notices(client, mundo, login):
    csrf = login()

    for path in ("/panel", "/panel/avisos", f"/panel/leads/{mundo['suyo'].id}"):
        assert client.get(path).status_code == 404, path
    response = client.post(f"/panel/leads/{mundo['suyo'].id}/gestionar", data={"csrf_token": csrf},
                           follow_redirects=False)
    assert response.status_code == 404


# --- CSRF ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("data", [{}, {"csrf_token": "otro"}, {"csrf_token": "ñandú"}])
def test_a_post_without_the_session_csrf_token_is_rejected_and_writes_nothing(
        client, session_factory, mundo, login, data):
    login(mundo["valeria"])

    response = client.post(f"/panel/leads/{mundo['suyo'].id}/gestionar", data=data, follow_redirects=False)

    assert response.status_code == 403
    with session_factory() as session:
        lead = session.get(Lead, mundo["suyo"].id)
        assert lead.status == "mql" and lead.advisor_id is None
    assert client.post("/logout", data=data, follow_redirects=False).status_code == 403
    assert client.get("/panel").status_code == 200  # el logout rechazado no cerró la sesión


def test_the_csrf_token_of_another_session_does_not_work(client, mundo, login):
    other = login(mundo["andres"])
    login(mundo["valeria"])

    response = client.post(f"/panel/leads/{mundo['suyo'].id}/gestionar", data={"csrf_token": other},
                           follow_redirects=False)

    assert response.status_code == 403


# --- rutas viejas con el token ------------------------------------------------------------------------------

def test_an_old_url_with_a_valid_token_redirects_and_does_not_authenticate(client, mundo):
    token, lead = mundo["valeria"].access_token, mundo["suyo"]

    for old, new in ((f"/panel/{token}", "/panel"), (f"/panel/{token}/leads/{lead.id}", f"/panel/leads/{lead.id}"),
                     (f"/panel/{token}/avisos", "/panel/avisos"), (f"/panel/{token}/funnel", "/panel/funnel"),
                     (f"/panel/{token}/negocio?periodo=mes", "/negocio?periodo=mes")):
        response = client.get(old, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == new, old

    page = client.get(f"/panel/{token}")
    assert page.url.path == "/login" and lead.telegram_user_id not in page.text


def test_a_valid_and_an_invalid_token_redirect_the_same_way(client, mundo):
    valid = client.get(f"/panel/{mundo['valeria'].access_token}", follow_redirects=False)
    invalid = client.get("/panel/token-que-no-existe", follow_redirects=False)
    assert (valid.status_code, valid.headers["location"]) == (invalid.status_code, invalid.headers["location"])


def test_the_links_the_core_already_sends_still_work_after_login(client, mundo, login):
    """`notifications.panel_link()` manda `/panel/<token>` y el reporte semanal, `/panel/<your token>/negocio`."""
    login(mundo["valeria"])
    queue = client.get(f"/panel/{mundo['valeria'].access_token}")
    assert queue.url.path == "/panel" and mundo["suyo"].telegram_user_id in queue.text

    login()
    business = client.get("/panel/<your token>/negocio")
    assert business.url.path == "/negocio" and business.status_code == 200


def test_the_public_pages_need_no_login(client):
    assert client.get("/health").status_code == 200
    assert client.get("/properties").status_code == 200


# --- arranque ------------------------------------------------------------------------------------------------

def test_the_panel_does_not_start_without_a_session_secret():
    with pytest.raises(InvalidSessionSecret):
        session_secret_from_env({})
    with pytest.raises(InvalidSessionSecret):
        session_secret_from_env({"PANEL_SESSION_SECRET": "   "})


def test_the_example_secret_only_works_in_the_demo():
    with pytest.raises(InvalidSessionSecret):
        session_secret_from_env({"PANEL_SESSION_SECRET": EXAMPLE_SESSION_SECRET})
    with pytest.raises(InvalidSessionSecret):
        session_secret_from_env({"PANEL_SESSION_SECRET": EXAMPLE_SESSION_SECRET, "PANEL_DEMO": "0"})
    assert session_secret_from_env({"PANEL_SESSION_SECRET": EXAMPLE_SESSION_SECRET, "PANEL_DEMO": "1"})
    assert session_secret_from_env({"PANEL_SESSION_SECRET": "un-secreto-propio"}) == "un-secreto-propio"
