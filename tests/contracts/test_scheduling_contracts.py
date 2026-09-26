"""Contract tests de C-2: las 3 operaciones de agenda (assign_advisor, get_slots, book).

Viven en `contracts/scheduling/` y no en `contracts/mcp/`: no son tools MCP, así que el LLM nunca las
ve (DT-32). Por lo demás siguen la misma forma que las tools del catálogo.
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
OPERATIONS = ["assign_advisor", "get_slots", "book"]
ERROR_CODES = {"NOT_FOUND", "INVALID_INPUT", "SLOT_TAKEN", "UPSTREAM_UNAVAILABLE"}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def operation(name):
    return _load(CONTRACTS / "scheduling" / f"{name}.schema.json")


def example(name, kind=None, validity="valid"):
    stem = f"{name}.{kind}.{validity}" if kind else f"{name}.{validity}"
    return _load(CONTRACTS / "examples" / f"{stem}.json")


def validator(schema):
    return Draft202012Validator(schema, format_checker=FormatChecker())


ERROR_SCHEMA = _load(CONTRACTS / "scheduling" / "scheduling_error.schema.json")


def test_error_schema_is_valid_and_lists_the_four_codes():
    Draft202012Validator.check_schema(ERROR_SCHEMA)
    assert set(ERROR_SCHEMA["properties"]["error_code"]["enum"]) == ERROR_CODES


def test_error_schema_extends_the_catalog_error_codes():
    catalog = _load(CONTRACTS / "mcp" / "catalog_error.schema.json")
    assert set(catalog["properties"]["error_code"]["enum"]) | {"SLOT_TAKEN"} == ERROR_CODES


def test_error_examples():
    v = validator(ERROR_SCHEMA)
    v.validate(example("scheduling_error"))
    assert list(v.iter_errors(example("scheduling_error", validity="invalid")))


@pytest.mark.parametrize("name", OPERATIONS)
def test_operation_shape_and_schemas_are_valid(name):
    op = operation(name)
    assert op["name"] == name
    assert op["http"] == f"POST /scheduling/{name}"
    Draft202012Validator.check_schema(op["inputSchema"])
    Draft202012Validator.check_schema(op["outputSchema"])
    assert op["errorSchema"] == "scheduling_error.schema.json"


@pytest.mark.parametrize("name", OPERATIONS)
def test_operation_declares_every_error_case_explicitly(name):
    op = operation(name)
    declared = set(op["errors"]) | set(op.get("errors_not_applicable", {}))
    assert declared == ERROR_CODES
    assert all(msg for msg in op["errors"].values())


@pytest.mark.parametrize("name", OPERATIONS)
@pytest.mark.parametrize("kind,key", [("input", "inputSchema"), ("output", "outputSchema")])
def test_valid_example_passes(name, kind, key):
    validator(operation(name)[key]).validate(example(name, kind))


@pytest.mark.parametrize("name", OPERATIONS)
@pytest.mark.parametrize("kind,key", [("input", "inputSchema"), ("output", "outputSchema")])
def test_invalid_example_fails(name, kind, key):
    assert list(validator(operation(name)[key]).iter_errors(example(name, kind, "invalid")))


def test_book_requires_an_idempotency_key():
    assert "idempotency_key" in operation("book")["inputSchema"]["required"]


def test_book_tells_a_retry_from_a_new_visit():
    assert "created" in operation("book")["outputSchema"]["required"]


def test_scheduling_operations_are_not_mcp_tools():
    # Si estuvieran en contracts/mcp, el catálogo las publicaría por MCP y core se las daría al LLM.
    assert not any((CONTRACTS / "mcp" / f"{name}.schema.json").exists() for name in OPERATIONS)
