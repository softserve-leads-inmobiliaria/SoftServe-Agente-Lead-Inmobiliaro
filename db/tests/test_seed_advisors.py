"""Tests de B-5 y B-17 contra un Postgres real: `scripts/seed_advisors.py` siembra asesores y usuarios del panel.

CI no corre el servicio `seed` del compose: sin este test, el seed podría dejar el panel sin usuarios.
"""
import importlib.util

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from db.models import Advisor, PanelUser
from db.passwords import verify_password
from db.repo_root import repo_root
from db.testing import temporary_database

ROOT = repo_root()
_spec = importlib.util.spec_from_file_location("seed_advisors", ROOT / "scripts" / "seed_advisors.py")
seed_advisors = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_advisors)


@pytest.fixture(scope="module")
def engine():
    with temporary_database() as url:
        engine = create_engine(url)
        yield engine
        engine.dispose()


def _users(engine) -> dict[str, PanelUser]:
    with Session(engine) as session:
        return {user.username: user for user in session.scalars(select(PanelUser))}


def test_seed_creates_one_panel_user_per_advisor_and_a_manager(engine, monkeypatch):
    monkeypatch.delenv("PANEL_ADVISOR_PASSWORD", raising=False)
    monkeypatch.delenv("PANEL_MANAGER_PASSWORD", raising=False)
    seed_advisors.seed(engine)

    users = _users(engine)
    assert set(users) == {"valeria", "andres", "camila", "gerente"}
    assert {name: (user.role, user.advisor_id) for name, user in users.items()} == {
        "valeria": ("asesor", "adv_001"), "andres": ("asesor", "adv_002"),
        "camila": ("asesor", "adv_003"), "gerente": ("gerente", None),
    }
    assert verify_password(seed_advisors.DEMO_ADVISOR_PASSWORD, users["valeria"].password_hash)
    assert verify_password(seed_advisors.DEMO_MANAGER_PASSWORD, users["gerente"].password_hash)
    assert all("demo" not in user.password_hash for user in users.values())  # nunca en claro


def test_seed_is_idempotent_keeps_tokens_and_applies_a_new_password(engine, monkeypatch):
    seed_advisors.seed(engine)
    with Session(engine) as session:
        tokens = {a.id: a.access_token for a in session.scalars(select(Advisor))}

    monkeypatch.setenv("PANEL_ADVISOR_PASSWORD", "otra-clave")
    monkeypatch.setenv("PANEL_MANAGER_PASSWORD", "clave-gerente")
    seed_advisors.seed(engine)

    with Session(engine) as session:
        assert {a.id: a.access_token for a in session.scalars(select(Advisor))} == tokens
    users = _users(engine)
    assert len(users) == 4
    assert verify_password("otra-clave", users["camila"].password_hash)
    assert not verify_password(seed_advisors.DEMO_ADVISOR_PASSWORD, users["camila"].password_hash)
    assert verify_password("clave-gerente", users["gerente"].password_hash)
