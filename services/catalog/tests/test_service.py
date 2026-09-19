"""Tests de CT-8: lógica del catálogo falso y datos en memoria (sin MCP)."""
import json
from datetime import date, datetime, timezone

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app import data
from app.service import CatalogError, CatalogService
from db.repo_root import repo_root

CONTRACTS = repo_root() / "contracts" / "mcp"
TODAY = date(2026, 9, 14)
PROFILE = {"down_payment": 0, "monthly_income": 0, "has_references": False}


def output_validator(tool):
    schema = json.loads((CONTRACTS / f"{tool}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema["outputSchema"], format_checker=FormatChecker())


@pytest.fixture
def service():
    return CatalogService(today=lambda: TODAY,
                          now=lambda: datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc))


def test_dataset_size_and_status_mix():
    assert 8 <= len(data.PROPERTIES) <= 12
    assert len(data.ADVISORS) == 3
    assert len({p["id"] for p in data.PROPERTIES}) == len(data.PROPERTIES)
    statuses = {p["status"] for p in data.PROPERTIES}
    assert "apartado" in statuses and "disponible" in statuses


def test_every_property_serializes_to_the_contract(service):
    validator = output_validator("get_property")
    for prop in data.PROPERTIES:
        validator.validate(service.get_property(prop["id"], lead_qualified=True))


def test_search_without_filters_returns_everything_without_exact_address(service):
    result = service.search_properties()
    output_validator("search_properties").validate(result)
    assert len(result["properties"]) == len(data.PROPERTIES)
    assert all("address_exact" not in p for p in result["properties"])


def test_search_filters_combine(service):
    result = service.search_properties(zone="providencia", type="venta", bedrooms=2,
                                      budget_range={"min": 1500000, "max": 2500000})
    assert [p["id"] for p in result["properties"]] == ["prop_00231"]


def test_search_without_matches_is_empty_not_an_error(service):
    assert service.search_properties(zone="Narnia") == {"properties": []}


def test_search_inverted_budget_is_invalid_input(service):
    with pytest.raises(CatalogError) as exc:
        service.search_properties(budget_range={"min": 10, "max": 1})
    assert exc.value.error_code == "INVALID_INPUT"


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
    result = service.get_availability("prop_00234")
    output_validator("get_availability").validate(result)
    assert result["status"] == "apartado"
    assert result["checked_at"] == "2026-09-14T15:00:00+00:00"


def test_promotions_only_include_current_ones(service):
    result = service.get_promotions()
    output_validator("get_promotions").validate(result)
    ids = {p["id"] for p in result["promotions"]}
    assert "promo_001" in ids and "promo_003" not in ids  # promo_003 está vencida


def test_promotions_for_property_without_any_current_one_is_empty(service):
    assert service.get_promotions("prop_00232") == {"promotions": []}
    assert service.get_promotions("prop_00233") == {"promotions": []}  # solo tenía una vencida


def test_promotion_is_valid_through_its_last_day():
    assert CatalogService(today=lambda: date(2099, 12, 31)).get_promotions("prop_00231")["promotions"]
    assert CatalogService(today=lambda: date(2100, 1, 1)).get_promotions("prop_00231") == {"promotions": []}


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


@pytest.mark.parametrize("call", [
    lambda s: s.search_properties(),
    lambda s: s.get_property("prop_00231", False),
    lambda s: s.get_availability("prop_00231"),
    lambda s: s.get_promotions(),
    lambda s: s.check_requirements("prop_00231", PROFILE),
])
def test_upstream_unavailable_on_every_tool(call):
    with pytest.raises(CatalogError) as exc:
        call(CatalogService(available=lambda: False))
    assert exc.value.error_code == "UPSTREAM_UNAVAILABLE"
