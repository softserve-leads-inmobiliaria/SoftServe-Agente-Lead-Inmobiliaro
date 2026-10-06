"""Tests de C-1: Lógica del catálogo ejecutándose sobre PostgreSQL real."""
import json
from datetime import date, datetime, timezone

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import data
from app.service import CatalogError, CatalogService
from db.models import PROPERTY_KINDS
from db.repo_root import repo_root
from db.settings import database_url

CONTRACTS = repo_root() / "contracts" / "mcp"
TODAY = date(2026, 9, 14)
PROFILE = {"down_payment": 0, "monthly_income": 0, "has_references": False}

def output_validator(tool):
    schema = json.loads((CONTRACTS / f"{tool}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema["outputSchema"], format_checker=FormatChecker())

@pytest.fixture
def real_db_session_factory():
    """Devuelve un factory de sesiones conectado a la base de datos de pruebas real."""
    engine = create_engine(database_url())
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture
def make_service(real_db_session_factory):
    """Construye un CatalogService sobre la BD real con el reloj que se le pase.

    Permite fijar `today` para probar el filtro de vigencia de promociones (#26).
    """
    def _make(today=TODAY, now=datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)):
        return CatalogService(
            db_session_factory=real_db_session_factory,
            today=lambda: today,
            now=lambda: now,
        )
    return _make

@pytest.fixture
def service(make_service):
    """Instancia el servicio usando la BD real, eliminando los mocks."""
    return make_service()

def test_dataset_size_and_status_mix():
    assert 8 <= len(data.PROPERTIES) <= 12
    assert len({p["id"] for p in data.PROPERTIES}) == len(data.PROPERTIES)
    statuses = {p["status"] for p in data.PROPERTIES}
    assert "apartado" in statuses and "disponible" in statuses
    # #188: cada propiedad trae su tipo de inmueble, dentro del vocabulario cerrado.
    assert all(p["kind"] in PROPERTY_KINDS for p in data.PROPERTIES)
    assert {p["kind"] for p in data.PROPERTIES} == set(PROPERTY_KINDS)

def test_every_property_serializes_to_the_contract(service):
    validator = output_validator("get_property")
    for prop in data.PROPERTIES:
        # Probamos contra la BD real
        validator.validate(service.get_property(prop["id"], lead_qualified=True))

def test_get_property_returns_the_photo_but_search_does_not(service):
    # B-10: la foto viaja en la ficha; la búsqueda no la trae (su contrato no la admite).
    prop = data.PROPERTIES[0]
    assert service.get_property(prop["id"], lead_qualified=False)["photo_url"] == prop["photo_url"]
    assert all("photo_url" not in p for p in service.search_properties()["properties"])

def test_search_without_filters_returns_everything_without_exact_address(service):
    result = service.search_properties()
    output_validator("search_properties").validate(result)
    assert all("address_exact" not in p for p in result["properties"])

def test_search_filters_combine(service):
    # Los cuatro filtros a la vez: solo prop_00231 cumple zona + tipo + recámaras + precio.
    result = service.search_properties(zone="providencia", type="venta", bedrooms=2,
                                      budget_range={"min": 1500000, "max": 2500000})
    output_validator("search_properties").validate(result)
    assert {p["id"] for p in result["properties"]} == {"prop_00231"}

def test_search_without_matches_is_empty_not_an_error(service):
    # En la BD real no existe la zona 'Narnia'
    assert service.search_properties(zone="Narnia") == {"properties": []}
    
def test_search_inverted_budget_is_invalid_input(service):
    with pytest.raises(CatalogError) as exc:
        service.search_properties(budget_range={"min": 10, "max": 1})
    assert exc.value.error_code == "INVALID_INPUT"


def test_search_by_kind_does_not_return_other_kinds(service):
    # #188: "busco depa" no debe traer casas ni terrenos.
    result = service.search_properties(kind="departamento")
    output_validator("search_properties").validate(result)
    assert result["properties"]
    assert {p["kind"] for p in result["properties"]} == {"departamento"}
    assert not {p["id"] for p in result["properties"]} & {"prop_00233", "prop_00241"}  # casa y terreno


def test_search_without_kind_behaves_like_before(service):
    with_kind = service.search_properties(kind="casa")["properties"]
    without = service.search_properties()["properties"]
    assert {p["id"] for p in with_kind} < {p["id"] for p in without}


def test_unknown_kind_is_invalid_input(service):
    with pytest.raises(CatalogError) as exc:
        service.search_properties(kind="depa")  # el valor normalizado no entra: es "departamento"
    assert exc.value.error_code == "INVALID_INPUT"


def test_get_property_returns_the_kind(service):
    assert service.get_property("prop_00241", lead_qualified=False)["kind"] == "terreno"

def test_address_exact_only_when_lead_is_qualified(service):
    assert service.get_property("prop_00231", lead_qualified=False)["address_exact"] is None
    assert service.get_property("prop_00231", lead_qualified=True)["address_exact"]

@pytest.mark.parametrize("call", [
    lambda s: s.get_property("nope", False),
    lambda s: s.get_availability("nope"),
    lambda s: s.get_promotions("nope"),
    lambda s: s.check_requirements("nope", PROFILE),
])
def test_unknown_property_is_not_found(service, call):
    with pytest.raises(CatalogError) as exc:
        call(service)
    assert exc.value.error_code == "NOT_FOUND"

def test_availability_reports_apartado_and_uses_the_clock(service):
    # prop_00234 ya está insertada como 'apartado' gracias al conftest.py
    result = service.get_availability("prop_00234")
    output_validator("get_availability").validate(result)
    assert result["status"] == "apartado"
    assert result["checked_at"] == "2026-09-14T15:00:00+00:00"

def test_promotions_only_include_current_ones(service):
    # Criterio de aceptación de C-1a: get_promotions filtra por vigencia.
    result = service.get_promotions()
    output_validator("get_promotions").validate(result)
    ids = {p["id"] for p in result["promotions"]}
    assert "promo_001" in ids and "promo_003" not in ids  # promo_003 está vencida

def test_promotions_for_property_without_any_current_one_is_empty(service):
    assert service.get_promotions("prop_00232") == {"promotions": []}
    assert service.get_promotions("prop_00233") == {"promotions": []}  # solo tenía una vencida

def test_promotion_is_valid_through_its_last_day(make_service):
    # `valid_until` es inclusivo: la promoción sigue viva el mismo día que vence.
    on_last_day = make_service(today=date(2099, 12, 31)).get_promotions("prop_00231")
    the_next_day = make_service(today=date(2100, 1, 1)).get_promotions("prop_00231")
    assert [p["id"] for p in on_last_day["promotions"]] == ["promo_001"]
    assert the_next_day == {"promotions": []}

@pytest.mark.parametrize("prop,profile,expected", [
    ("prop_00231", {"down_payment": 500000, "monthly_income": 60000, "has_references": False}, True),
    ("prop_00231", {"down_payment": 1000, "monthly_income": 60000, "has_references": True}, False),
    ("prop_00237", {"down_payment": 500000, "monthly_income": 60000, "has_references": True}, False),
    ("prop_00235", {"down_payment": 0, "monthly_income": 60000, "has_references": True}, True),
    ("prop_00235", {"down_payment": 0, "monthly_income": 20000, "has_references": True}, False),
    ("prop_00235", {"down_payment": 0, "monthly_income": 60000, "has_references": False}, False),
])
def test_check_requirements(service, prop, profile, expected):
    result = service.check_requirements(prop, profile)
    output_validator("check_requirements").validate(result)
    assert result["meets_requirements"] is expected

def test_upstream_unavailable_on_every_tool(real_db_session_factory):
    # Simulamos la caída del servicio inyectando False en _available
    service = CatalogService(db_session_factory=real_db_session_factory, available=lambda: False)
    with pytest.raises(CatalogError) as exc:
        service.search_properties()
    assert exc.value.error_code == "UPSTREAM_UNAVAILABLE"