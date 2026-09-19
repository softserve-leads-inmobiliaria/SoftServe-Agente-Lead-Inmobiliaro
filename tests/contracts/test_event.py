"""Contract tests de CT-1: evento de mensaje canónico."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SCHEMA = json.loads((CONTRACTS / "event.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def _example(kind):
    return json.loads((CONTRACTS / "examples" / f"event.{kind}.json").read_text(encoding="utf-8"))


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("kind,ok", [("valid", True), ("invalid", False)])
def test_examples(kind, ok):
    assert (not list(VALIDATOR.iter_errors(_example(kind)))) is ok


def test_property_id_and_utm_campaign_are_optional():
    assert "property_id" in SCHEMA["properties"] and "utm_campaign" in SCHEMA["properties"]
    assert not {"property_id", "utm_campaign"} & set(SCHEMA["required"])
    minimal = {k: v for k, v in _example("valid").items() if k not in ("property_id", "utm_campaign")}
    VALIDATOR.validate(minimal)


def test_inbound_deep_link_may_have_empty_text():
    VALIDATOR.validate(_example("valid") | {"text": ""})


@pytest.mark.parametrize("field", [
    "schema_version", "event_id", "conversation_id", "lead_id",
    "channel", "direction", "text", "timestamp",
])
def test_required_fields(field):
    doc = {k: v for k, v in _example("valid").items() if k != field}
    assert list(VALIDATOR.iter_errors(doc))


@pytest.mark.parametrize("field,value", [
    ("channel", "whatsapp"), ("direction", "entrante"), ("schema_version", 2),
    ("timestamp", "ayer"), ("event_id", "no-uuid"), ("property_id", None), ("utm_campaign", ""),
])
def test_bad_values_rejected(field, value):
    assert list(VALIDATOR.iter_errors(_example("valid") | {field: value}))


def test_unknown_field_rejected():
    assert list(VALIDATOR.iter_errors(_example("valid") | {"telegram_chat_id": 123}))


def test_schema_version_is_pinned_to_one():
    assert SCHEMA["properties"]["schema_version"]["const"] == 1
