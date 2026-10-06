"""Contract tests de CT-2: las 5 tools catalog.* (input, output y errores)."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
TOOLS = [
    "search_properties",
    "get_property",
    "get_availability",
    "get_promotions",
    "check_requirements",
]
ERROR_CODES = {"NOT_FOUND", "INVALID_INPUT", "UPSTREAM_UNAVAILABLE"}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def tool(name):
    return _load(CONTRACTS / "mcp" / f"{name}.schema.json")


def example(name, kind=None, validity="valid"):
    stem = f"{name}.{kind}.{validity}" if kind else f"{name}.{validity}"
    return _load(CONTRACTS / "examples" / f"{stem}.json")


def validator(schema):
    return Draft202012Validator(schema, format_checker=FormatChecker())


ERROR_SCHEMA = _load(CONTRACTS / "mcp" / "catalog_error.schema.json")


def test_error_schema_is_valid_and_lists_the_three_codes():
    Draft202012Validator.check_schema(ERROR_SCHEMA)
    assert set(ERROR_SCHEMA["properties"]["error_code"]["enum"]) == ERROR_CODES


def test_error_examples():
    v = validator(ERROR_SCHEMA)
    v.validate(example("catalog_error"))
    assert list(v.iter_errors(example("catalog_error", validity="invalid")))


@pytest.mark.parametrize("name", TOOLS)
def test_tool_shape_and_schemas_are_valid(name):
    t = tool(name)
    assert t["name"] == name
    Draft202012Validator.check_schema(t["inputSchema"])
    Draft202012Validator.check_schema(t["outputSchema"])
    assert t["errorSchema"] == "catalog_error.schema.json"


@pytest.mark.parametrize("name", TOOLS)
def test_tool_declares_every_error_case_explicitly(name):
    t = tool(name)
    declared = set(t["errors"]) | set(t.get("errors_not_applicable", {}))
    assert declared == ERROR_CODES
    assert set(t["errors"]) <= ERROR_CODES
    assert all(msg for msg in t["errors"].values())


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("kind,key", [("input", "inputSchema"), ("output", "outputSchema")])
def test_valid_example_passes(name, kind, key):
    validator(tool(name)[key]).validate(example(name, kind))


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("kind,key", [("input", "inputSchema"), ("output", "outputSchema")])
def test_invalid_example_fails(name, kind, key):
    assert list(validator(tool(name)[key]).iter_errors(example(name, kind, "invalid")))


def test_search_output_can_never_carry_address_exact():
    item = tool("search_properties")["outputSchema"]["properties"]["properties"]["items"]
    assert "address_exact" not in item["properties"]
    assert item["additionalProperties"] is False


def test_get_property_requires_address_exact_key_nullable():
    out = tool("get_property")["outputSchema"]
    assert "address_exact" in out["required"]
    assert "null" in out["properties"]["address_exact"]["type"]


@pytest.mark.parametrize("photo", ["omitida", None, "https://images.unsplash.com/photo-1?w=1024"])
def test_get_property_photo_url_is_optional_and_nullable(photo):
    out = tool("get_property")["outputSchema"]
    assert "photo_url" not in out["required"]
    item = example("get_property", "output")
    item.pop("photo_url", None)
    if photo != "omitida":
        item["photo_url"] = photo
    validator(out).validate(item)


def test_get_property_requirements_are_optional_but_complete_when_present():
    # #201 (DT-56): opcional como photo_url; si viene, trae los cinco campos y nada más.
    out = tool("get_property")["outputSchema"]
    assert "requirements" not in out["required"]
    item = example("get_property", "output")
    validator(out).validate({k: v for k, v in item.items() if k != "requirements"})
    rent = {**item, "requirements": {"down_payment_percent": None, "down_payment_min": None, "income_multiple": 3,
                                     "monthly_income_min": 3450, "references_required": True}}
    validator(out).validate(rent)
    for broken in ({k: v for k, v in rent["requirements"].items() if k != "references_required"},
                   {**rent["requirements"], "approved": True}):
        with pytest.raises(ValidationError):
            validator(out).validate({**item, "requirements": broken})


def test_get_property_input_requires_lead_qualified():
    assert "lead_qualified" in tool("get_property")["inputSchema"]["required"]


def test_promotion_requires_valid_until():
    item = tool("get_promotions")["outputSchema"]["properties"]["promotions"]["items"]
    assert "valid_until" in item["required"]


def test_status_enum_matches_design_doc():
    props = tool("get_availability")["outputSchema"]["properties"]
    assert props["status"]["enum"] == ["disponible", "apartado", "cerrado"]


def test_kind_is_a_closed_vocabulary_apart_from_the_operation():
    # #188: `type` es la operación y `kind` el inmueble; son filtros distintos y ambos cerrados.
    kinds = ["departamento", "casa", "terreno", "local", "estudio"]
    search_in = tool("search_properties")["inputSchema"]["properties"]
    assert search_in["kind"]["enum"] == kinds
    assert search_in["type"]["enum"] == ["venta", "renta", "preventa"]
    item = tool("search_properties")["outputSchema"]["properties"]["properties"]["items"]
    assert item["properties"]["kind"]["enum"] == kinds
    assert "kind" in item["required"]
    out = tool("get_property")["outputSchema"]
    assert out["properties"]["kind"]["enum"] == kinds
    assert "kind" in out["required"]
