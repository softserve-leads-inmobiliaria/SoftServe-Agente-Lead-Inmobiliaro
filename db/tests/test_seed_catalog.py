"""Tests de C-9 contra un Postgres real: `scripts/seed_catalog.py` siembra el inventario de demo.

CI no corre el servicio `seed` del compose, así que sin este test el seed podría romperse (una columna
renombrada, un tipo que no encaja) sin que nada lo note hasta levantar el stack.
"""
import importlib.util
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select, update

from db.models import Promotion, Property
from db.repo_root import repo_root
from db.testing import temporary_database

ROOT = repo_root()
_spec = importlib.util.spec_from_file_location("seed_catalog", ROOT / "scripts" / "seed_catalog.py")
seed_catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_catalog)


@pytest.fixture(scope="module")
def engine():
    with temporary_database() as url:
        engine = create_engine(url)
        yield engine
        engine.dispose()


def _count(engine, model) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(model)).scalar_one()


def test_seed_loads_the_whole_inventory_from_the_catalog_data(engine):
    props, promos = seed_catalog.seed(engine)

    assert [p.id for p in props] == [p["id"] for p in seed_catalog.PROPERTIES]
    assert [p.id for p in promos] == [p["id"] for p in seed_catalog.PROMOTIONS]
    assert {p.id for p in props if p.bedrooms == 0} == {"prop_00240", "prop_00241", "prop_00242"}
    assert [p.id for p in promos if p.valid_until < date.today()] == ["promo_003"]


def test_seed_is_idempotent(engine):
    seed_catalog.seed(engine)
    seed_catalog.seed(engine)

    assert _count(engine, Property) == len(seed_catalog.PROPERTIES)
    assert _count(engine, Promotion) == len(seed_catalog.PROMOTIONS)


def test_seed_fills_missing_photos_without_touching_anything_else(engine):
    """B-10: una base sembrada antes de las fotos las recibe; el resto de la fila no se repara."""
    seed_catalog.seed(engine)
    with engine.begin() as connection:
        connection.execute(update(Property).where(Property.id == "prop_00231")
                           .values(photo_url=None, title="Editado a mano"))
        connection.execute(update(Property).where(Property.id == "prop_00232")
                           .values(photo_url="https://example.com/propia.jpg"))

    props = {p.id: p for p in seed_catalog.seed(engine)[0]}

    expected = {p["id"]: p["photo_url"] for p in seed_catalog.PROPERTIES}
    assert props["prop_00231"].photo_url == expected["prop_00231"]
    assert props["prop_00231"].title == "Editado a mano"
    assert props["prop_00232"].photo_url == "https://example.com/propia.jpg"
