"""Contract tests de OBS-5: ningún contrato queda fuera de CI.

Los demás tests de esta carpeta nombran sus contratos a mano. Este recorre `contracts/` y valida cada
ejemplo contra su schema, así un schema nuevo entra en CI sin tocar ninguna lista: si le faltan sus
ejemplos (o sobra un ejemplo sin schema), falla.

Forma de los ejemplos (`contracts/examples/`):
- un schema simple `<nombre>.schema.json`: `<nombre>.valid.json` y `<nombre>.invalid.json`;
- una tool u operación (`inputSchema` + `outputSchema`): `<nombre>.{input,output}.{valid,invalid}.json`;
- una entidad de `$defs` con ejemplo propio: se declara en `DEF_EXAMPLES`.
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
EXAMPLES = CONTRACTS / "examples"

# ejemplo -> (schema que lo define, entrada de `$defs`)
DEF_EXAMPLES = {
    "lead_score": ("fsm_state", "LeadScore"),
    "conversation": ("fsm_state", "Conversation"),
}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMAS = {path.name.removesuffix(".schema.json"): _load(path)
           for path in sorted(CONTRACTS.rglob("*.schema.json"))}


def _is_operation(schema):
    return "inputSchema" in schema and "outputSchema" in schema


def _cases():
    """(ejemplo sin `.valid`/`.invalid`, schema contra el que se valida)."""
    for name, schema in SCHEMAS.items():
        if _is_operation(schema):
            yield f"{name}.input", schema["inputSchema"]
            yield f"{name}.output", schema["outputSchema"]
        else:
            yield name, schema
    for example, (name, defname) in DEF_EXAMPLES.items():
        root = SCHEMAS[name]
        yield example, {"$schema": root["$schema"], "$ref": f"#/$defs/{defname}", "$defs": root["$defs"]}


CASES = dict(_cases())


def _validator(schema):
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_every_contract_is_found():
    """Guarda contra un `rglob` que deje de ver carpetas: los contratos que ya existían."""
    assert {"event", "fsm_state", "catalog_error", "scheduling_error", "search_properties",
            "get_property", "get_availability", "get_promotions", "check_requirements",
            "assign_advisor", "get_slots", "book"} <= set(SCHEMAS)


def test_no_two_contracts_share_a_name():
    assert len(SCHEMAS) == len(list(CONTRACTS.rglob("*.schema.json")))


@pytest.mark.parametrize("name", SCHEMAS)
def test_every_schema_is_valid_draft_2020_12(name):
    schema = SCHEMAS[name]
    for part in ([schema["inputSchema"], schema["outputSchema"]] if _is_operation(schema) else [schema]):
        Draft202012Validator.check_schema(part)


@pytest.mark.parametrize("stem", CASES)
def test_every_contract_has_a_valid_example_that_passes(stem):
    path = EXAMPLES / f"{stem}.valid.json"
    assert path.exists(), f"falta el ejemplo {path.name}"
    _validator(CASES[stem]).validate(_load(path))


@pytest.mark.parametrize("stem", CASES)
def test_every_contract_has_an_invalid_example_that_fails(stem):
    path = EXAMPLES / f"{stem}.invalid.json"
    assert path.exists(), f"falta el ejemplo {path.name}"
    assert list(_validator(CASES[stem]).iter_errors(_load(path)))


def test_every_example_belongs_to_a_contract():
    expected = {f"{stem}.{validity}.json" for stem in CASES for validity in ("valid", "invalid")}
    orphans = sorted(path.name for path in EXAMPLES.glob("*.json") if path.name not in expected)
    assert not orphans, f"ejemplos sin contrato: {orphans}"
