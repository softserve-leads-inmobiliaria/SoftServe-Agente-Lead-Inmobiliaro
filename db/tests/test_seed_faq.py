"""Tests de C-5 contra un Postgres real: `scripts/seed_faq.py` siembra la FAQ con embeddings.

CI no corre el servicio `seed` del compose, así que sin este test el seed podría romperse sin que nada
lo note hasta levantar el stack. Usa `HashEncoder` para no bajar el modelo real ni tocar la red.
"""
import importlib.util

import pytest
from sqlalchemy import create_engine, func, select, update

from db.models import FaqEntry
from db.repo_root import repo_root
from db.testing import temporary_database
from services.catalog.app.embeddings import HashEncoder

ROOT = repo_root()
_spec = importlib.util.spec_from_file_location("seed_faq", ROOT / "scripts" / "seed_faq.py")
seed_faq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_faq)

ENCODER = HashEncoder()


@pytest.fixture(scope="module")
def engine():
    with temporary_database() as url:
        engine = create_engine(url)
        yield engine
        engine.dispose()


def _count(engine) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(FaqEntry)).scalar_one()


def test_seed_loads_the_whole_faq(engine):
    assert 15 <= len(seed_faq.FAQ) <= 25
    assert seed_faq.seed(engine, encoder=ENCODER) == len(seed_faq.FAQ)
    assert _count(engine) == len(seed_faq.FAQ)


def test_seed_is_idempotent(engine):
    seed_faq.seed(engine, encoder=ENCODER)
    seed_faq.seed(engine, encoder=ENCODER)
    assert _count(engine) == len(seed_faq.FAQ)


def test_seed_refreshes_a_hand_edited_answer(engine):
    seed_faq.seed(engine, encoder=ENCODER)
    question = seed_faq.FAQ[0]["question"]
    with engine.begin() as connection:
        connection.execute(update(FaqEntry).where(FaqEntry.question == question)
                           .values(answer="editada a mano"))

    seed_faq.seed(engine, encoder=ENCODER)

    with engine.connect() as connection:
        answer = connection.execute(
            select(FaqEntry.answer).where(FaqEntry.question == question)
        ).scalar_one()
    assert answer == seed_faq.FAQ[0]["answer"]


def test_clear_removes_only_the_faq_questions(engine):
    seed_faq.seed(engine, encoder=ENCODER)
    with engine.begin() as connection:
        connection.execute(update(FaqEntry).where(FaqEntry.question == seed_faq.FAQ[0]["question"])
                           .values(question="pregunta que no está en faq_data"))

    assert seed_faq.clear(engine) == len(seed_faq.FAQ) - 1
    assert _count(engine) == 1
