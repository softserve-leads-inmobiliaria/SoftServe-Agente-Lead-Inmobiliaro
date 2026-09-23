import os
import pytest
from datetime import date
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from db.models import Property, Promotion
from db.settings import database_url
from app import data


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Siembra la BD PostgreSQL con el catálogo de pruebas para toda la suite (Historia C-1).

    Usa TRUNCATE ... CASCADE para limpiar sin depender del orden de las FK.
    Hace flush() tras insertar propiedades para que el FK de promociones se satisfaga.
    """
    engine = create_engine(database_url())

    # Comprobamos conectividad; sin Postgres disponible saltamos (igual que db/testing.py).
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        engine.dispose()
        if os.environ.get("REQUIRE_DB") == "1":
            raise
        pytest.skip(
            f"Postgres no disponible ({type(exc).__name__}); levantarlo con "
            "`docker compose up -d postgres`"
        )

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with SessionLocal() as db:
        # TRUNCATE CASCADE limpia en un solo paso respetando el orden de FK.
        db.execute(text("TRUNCATE promotions, properties RESTART IDENTITY CASCADE"))
        db.commit()

        # Insertamos propiedades primero y hacemos flush para que las FK de promociones se satisfagan.
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

    with SessionLocal() as db:
        db.execute(text("TRUNCATE promotions, properties RESTART IDENTITY CASCADE"))
        db.commit()

    engine.dispose()