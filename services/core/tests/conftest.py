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
