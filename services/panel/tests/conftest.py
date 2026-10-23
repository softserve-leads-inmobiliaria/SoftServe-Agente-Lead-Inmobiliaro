import os
import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert

from db.models import PanelUser
from db.passwords import hash_password
from db.testing import temporary_database

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# B-17: el panel no arranca sin secreto de sesión. Tiene que estar antes de que un test importe `app.main`.
os.environ.setdefault("PANEL_SESSION_SECRET", "panel-tests-secret")

PASSWORD = "clave-de-test"
PASSWORD_HASH = hash_password(PASSWORD)  # una vez: scrypt es lento a propósito
MANAGER = "gerente-test"
_CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture(scope="module")
def database_url():
    with temporary_database() as url:
        yield url


@pytest.fixture(scope="module")
def engine(database_url):
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


def add_panel_user(session_factory, advisor=None) -> str:
    """El usuario del panel de `advisor` (su id es el username), o el gerente si no se pasa ninguno."""
    username = advisor.id if advisor is not None else MANAGER
    with session_factory() as session:
        session.execute(insert(PanelUser).values(
            username=username, password_hash=PASSWORD_HASH, role="asesor" if advisor is not None else "gerente",
            advisor_id=advisor.id if advisor is not None else None,
        ).on_conflict_do_nothing(index_elements=["username"]))
        session.commit()
    return username


def csrf_of(page: str) -> str:
    match = _CSRF.search(page)
    assert match, "la página no trae el token CSRF"
    return match.group(1)


@pytest.fixture
def login(client, session_factory):
    """Inicia sesión en `client` (B-17) como el asesor dado, o como el gerente sin argumentos.

    Usa el `client` y la `session_factory` de cada archivo de tests. Devuelve el token CSRF de la sesión,
    para los POST del panel.
    """
    def _login(advisor=None) -> str:
        username = add_panel_user(session_factory, advisor)
        response = client.post("/login", data={"username": username, "password": PASSWORD})
        assert response.status_code == 200, response.text  # después del 303 a /panel o /negocio
        return csrf_of(response.text)

    return _login
