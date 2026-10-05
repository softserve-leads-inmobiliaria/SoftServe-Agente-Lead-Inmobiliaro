"""Tests de C-5: búsqueda en la FAQ con embeddings en pgvector (DT-7).

Usan `HashEncoder` (bolsa de palabras determinista) en vez del modelo real: prueban la forma del
contrato, el orden por distancia coseno y la validación de entrada sin bajar el modelo ni tocar la
red. La calidad del modelo real se mide en la demo, no en la suite.
"""
import json

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.embeddings import EMBEDDING_DIM, HashEncoder
from app.faq_data import FAQ
from app.service import CatalogError, CatalogService
from db.models import FaqEntry
from db.repo_root import repo_root
from db.settings import database_url

CONTRACTS = repo_root() / "contracts" / "mcp"
ENCODER = HashEncoder()
PETS = next(item["question"] for item in FAQ if "mascotas" in item["question"])


def output_validator(tool):
    schema = json.loads((CONTRACTS / f"{tool}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema["outputSchema"], format_checker=FormatChecker())


@pytest.fixture
def session_factory():
    engine = create_engine(database_url())
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()


@pytest.fixture
def faq_db(session_factory):
    """Deja la tabla de la FAQ con los pares de `faq_data.py` y sus vectores, y la limpia al salir."""
    vectors = ENCODER.encode([item["question"] for item in FAQ])
    with session_factory() as db:
        db.execute(delete(FaqEntry))
        for item, vector in zip(FAQ, vectors, strict=True):
            db.add(FaqEntry(question=item["question"], answer=item["answer"], embedding=vector))
        db.commit()
    yield
    with session_factory() as db:
        db.execute(delete(FaqEntry))
        db.commit()


@pytest.fixture
def service(session_factory):
    return CatalogService(db_session_factory=session_factory, encoder=ENCODER)


def test_seed_has_between_15_and_25_unique_pairs():
    assert 15 <= len(FAQ) <= 25
    assert len({item["question"] for item in FAQ}) == len(FAQ)


def test_encoder_output_dimension_matches_the_column():
    assert len(ENCODER.encode(["una pregunta"])[0]) == EMBEDDING_DIM


def test_search_returns_contract_valid_matches(service, faq_db):
    result = service.search_faq("¿Cuánto debo dar de enganche para comprar?")
    output_validator("search_faq").validate(result)
    assert result["matches"]
    assert len(result["matches"]) <= 3


def test_search_finds_the_most_relevant_pair(service, faq_db):
    result = service.search_faq("¿aceptan mascotas en la renta?", top_k=1)
    assert result["matches"][0]["question"] == PETS


def test_top_k_limits_the_number_of_matches(service, faq_db):
    assert len(service.search_faq("crédito", top_k=2)["matches"]) == 2


def test_empty_faq_is_empty_matches_not_an_error(service, session_factory):
    with session_factory() as db:
        db.execute(delete(FaqEntry))
        db.commit()
    assert service.search_faq("cualquier cosa") == {"matches": []}


@pytest.mark.parametrize("query", ["", "   ", None, 123])
def test_invalid_query_is_invalid_input(service, query):
    with pytest.raises(CatalogError) as exc:
        service.search_faq(query)
    assert exc.value.error_code == "INVALID_INPUT"


@pytest.mark.parametrize("top_k", [0, 11, -1, 2.5, True])
def test_invalid_top_k_is_invalid_input(service, top_k):
    with pytest.raises(CatalogError) as exc:
        service.search_faq("una pregunta", top_k=top_k)
    assert exc.value.error_code == "INVALID_INPUT"


def test_upstream_unavailable(session_factory):
    service = CatalogService(db_session_factory=session_factory, available=lambda: False,
                             encoder=ENCODER)
    with pytest.raises(CatalogError) as exc:
        service.search_faq("una pregunta")
    assert exc.value.error_code == "UPSTREAM_UNAVAILABLE"
