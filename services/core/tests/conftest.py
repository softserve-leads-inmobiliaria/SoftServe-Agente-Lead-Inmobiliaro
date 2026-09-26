import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from db.testing import temporary_database

# Permite `from app.scoring import ...` sin instalar el paquete.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def database_url():
    """Base Postgres temporal ya migrada (se omite si no hay Postgres; en CI es obligatoria)."""
    with temporary_database() as url:
        yield url


@pytest.fixture(scope="module")
def engine(database_url):
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


class _AgendaCaida:
    async def assign_advisor(self, lead_id, zone=None, language="es", registered_only=False):
        from app.scheduling_client import SchedulingError

        raise SchedulingError("UPSTREAM_UNAVAILABLE", "sin catálogo en los tests")


@pytest.fixture(autouse=True)
def _agenda_sin_catalogo(monkeypatch):
    """Los tests de core no levantan el catálogo: la agenda "no responde" y las notificaciones usan la
    regla local de B-7. Los tests de A-10 inyectan su propia agenda."""
    from app import scheduling_client

    monkeypatch.setattr(scheduling_client, "get_scheduling", lambda: _AgendaCaida())
