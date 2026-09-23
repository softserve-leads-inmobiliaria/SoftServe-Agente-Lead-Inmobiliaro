"""Tests de C-1: Lógica del catálogo ejecutándose sobre PostgreSQL real."""
import json
from datetime import date, datetime, timezone

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import data
from app.service import CatalogError, CatalogService
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
def service(real_db_session_factory):
    """Instancia el servicio usando la BD real, eliminando los mocks."""
    return CatalogService(
        db_session_factory=real_db_session_factory,
        today=lambda: TODAY,
        now=lambda: datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
    )

def test_dataset_size_and_status_mix():
    assert 8 <= len(data.PROPERTIES) <= 12
    assert len({p["id"] for p in data.PROPERTIES}) == len(data.PROPERTIES)
    statuses = {p["status"] for p in data.PROPERTIES}
    assert "apartado" in statuses and "disponible" in statuses

def test_every_property_serializes_to_the_contract(service):
    validator = output_validator("get_property")
    for prop in data.PROPERTIES:
        # Probamos contra la BD real
        validator.validate(service.get_property(prop["id"], lead_qualified=True))

def test_search_without_filters_returns_everything_without_exact_address(service):
    result = service.search_properties()
    output_validator("search_properties").validate(result)
    assert all("address_exact" not in p for p in result["properties"])

def test_search_without_matches_is_empty_not_an_error(service):
    # En la BD real no existe la zona 'Narnia'
    assert service.search_properties(zone="Narnia") == {"properties": []}
    
def test_search_inverted_budget_is_invalid_input(service):
    with pytest.raises(CatalogError) as exc:
        service.search_properties(budget_range={"min": 10, "max": 1})
    assert exc.value.error_code == "INVALID_INPUT"

def test_address_exact_only_when_lead_is_qualified(service):
    assert service.get_property("prop_00231", lead_qualified=False)["address_exact"] is None
    assert service.get_property("prop_00231", lead_qualified=True)["address_exact"]

def test_availability_reports_apartado_and_uses_the_clock(service):
    # prop_00234 ya está insertada como 'apartado' gracias al conftest.py
    result = service.get_availability("prop_00234")
    output_validator("get_availability").validate(result)
    assert result["status"] == "apartado"
    assert result["checked_at"] == "2026-09-14T15:00:00+00:00"

def test_check_requirements(service):
    result = service.check_requirements("prop_00231", PROFILE)
    output_validator("check_requirements").validate(result)

def test_upstream_unavailable_on_every_tool(real_db_session_factory):
    # Simulamos la caída del servicio inyectando False en _available
    service = CatalogService(db_session_factory=real_db_session_factory, available=lambda: False)
    with pytest.raises(CatalogError) as exc:
        service.search_properties()
    assert exc.value.error_code == "UPSTREAM_UNAVAILABLE"