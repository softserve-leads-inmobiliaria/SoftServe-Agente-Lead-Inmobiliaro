import os
import uuid
from datetime import date

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import data
from db.models import Promotion, Property
from db.settings import database_url as default_url
from db.testing import _admin_engine, alembic_config

_temp_db_url = None

def pytest_configure(config):
    """Crea la base de datos temporal ANTES de recolectar los tests y exporta DATABASE_URL."""
    global _temp_db_url
    
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        admin.dispose()
        if os.environ.get("REQUIRE_DB") == "1":
            raise
        return  # Setup database will skip properly later

    name = f"leads_test_cat_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    
    url = make_url(default_url()).set(database=name).render_as_string(hide_password=False)
    command.upgrade(alembic_config(url), "head")
    
    _temp_db_url = url
    os.environ["DATABASE_URL"] = url
    admin.dispose()

def pytest_unconfigure(config):
    """Limpia la base temporal al terminar."""
    if _temp_db_url:
        admin = _admin_engine()
        name = make_url(_temp_db_url).database
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()

@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Siembra la BD temporal con el catálogo de pruebas."""
    if not _temp_db_url:
        pytest.skip("Postgres no disponible")

    engine = create_engine(_temp_db_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with SessionLocal() as db:
        for prop_data in data.PROPERTIES:
            db.add(Property(**prop_data))
        db.flush()

        for promo_data in data.PROMOTIONS:
            promo_copy = promo_data.copy()
            valid_until_str = promo_copy.pop("valid_until")
            promo_copy["valid_until"] = date.fromisoformat(valid_until_str)
            db.add(Promotion(**promo_copy))

        db.commit()

    yield
    engine.dispose()