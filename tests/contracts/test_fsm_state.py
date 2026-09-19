"""Contract tests de CT-3: FsmState / LeadScore / Conversation / ScoreSignal."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SCHEMA = json.loads((CONTRACTS / "fsm_state.schema.json").read_text(encoding="utf-8"))

# nombre del ejemplo -> ruta de $defs (None = raíz del schema)
CASES = {
    "fsm_state": None,
    "lead_score": "LeadScore",
    "conversation": "Conversation",
}


def _validator(defname):
    schema = SCHEMA if defname is None else {
        "$schema": SCHEMA["$schema"],
        "$ref": f"#/$defs/{defname}",
        "$defs": SCHEMA["$defs"],
    }
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _example(name, kind):
    return json.loads((CONTRACTS / "examples" / f"{name}.{kind}.json").read_text(encoding="utf-8"))


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("name,defname", CASES.items())
def test_valid_example_passes(name, defname):
    _validator(defname).validate(_example(name, "valid"))


@pytest.mark.parametrize("name,defname", CASES.items())
def test_invalid_example_fails(name, defname):
    assert list(_validator(defname).iter_errors(_example(name, "invalid")))


def test_fsm_state_enum_matches_design_doc():
    assert SCHEMA["$defs"]["FsmStateName"]["enum"] == [
        "GREETING", "DISCOVERY", "QUALIFY", "RECOMMEND", "SCHEDULE",
        "HANDOFF", "HUMAN_HANDOFF", "NURTURE", "LOST",
    ]


def test_score_signal_has_no_protected_category_field():
    props = set(SCHEMA["$defs"]["ScoreSignal"]["properties"])
    assert props == {
        "budget_hint", "decision_maker", "urgency",
        "matching_properties", "meets_requirements",
    }


@pytest.mark.parametrize("total,band,ok", [
    (40, "COLD", True), (41, "COLD", False),
    (41, "WARM", True), (70, "WARM", True), (71, "WARM", False),
    (71, "HOT", True), (70, "HOT", False),
])
def test_band_matches_total(total, band, ok):
    score = _example("lead_score", "valid") | {"total": total, "band": band}
    errors = list(_validator("LeadScore").iter_errors(score))
    assert (not errors) is ok


def test_schema_declares_postgres_persistence():
    assert "Postgres" in SCHEMA["description"] and "db/models.py" in SCHEMA["description"]
