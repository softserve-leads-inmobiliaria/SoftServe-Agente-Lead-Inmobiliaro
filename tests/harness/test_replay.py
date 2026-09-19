"""Tests de CT-9: el arnés de canal."""
import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))

import run_all  # noqa: E402
from replay import (  # noqa: E402
    VALIDATOR,
    ScriptError,
    build_events,
    default_responder,
    load_script,
    replay,
)

SCRIPTS = run_all.discover()


def test_there_are_at_least_two_example_scripts():
    assert len(SCRIPTS) >= 2


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_every_discovered_script_passes(path):
    replay(path, default_responder())


def test_new_fixture_is_discovered_without_code_changes(tmp_path):
    (tmp_path / "nuevo.jsonl").write_text('{"text": "Hola"}\n', encoding="utf-8")
    (tmp_path / "ignorado.txt").write_text("no soy un guion", encoding="utf-8")
    assert [p.name for p in run_all.discover(tmp_path)] == ["nuevo.jsonl"]


def test_built_events_are_valid_and_deterministic():
    turns = [{"text": "Hola", "property_id": "prop_1"}, {"text": "Adiós"}]
    events = build_events("demo", turns)
    for event in events:
        VALIDATOR.validate(event)
    assert events == build_events("demo", turns)
    assert len({e["event_id"] for e in events}) == 2
    assert len({e["conversation_id"] for e in events}) == 1
    assert events[0]["property_id"] == "prop_1" and "property_id" not in events[1]
    assert events[0]["timestamp"] < events[1]["timestamp"]


def test_different_scripts_get_different_conversations():
    a = build_events("a", [{"text": "x"}])[0]
    b = build_events("b", [{"text": "x"}])[0]
    assert a["conversation_id"] != b["conversation_id"]


def _write(tmp_path, content):
    path = tmp_path / "g.jsonl"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("content", [
    "no es json\n",
    '{"sin_text": 1}\n',
    '{"text": 5}\n',
    '{"text": "x", "otro": 1}\n',
    '{"text": "x", "expect_reply_contains": 3}\n',
    "\n\n",
])
def test_malformed_scripts_are_rejected(tmp_path, content):
    with pytest.raises(ScriptError):
        load_script(_write(tmp_path, content))


def test_blank_lines_are_ignored(tmp_path):
    turns = load_script(_write(tmp_path, '{"text": "a"}\n\n{"text": "b"}\n'))
    assert [t["text"] for t in turns] == ["a", "b"]


def test_failed_expectation_is_reported(tmp_path):
    path = _write(tmp_path, json.dumps({"text": "Hola", "expect_reply_contains": "inexistente"}))
    with pytest.raises(AssertionError, match="inexistente"):
        replay(path, default_responder())


def test_invalid_responder_output_is_reported(tmp_path):
    path = _write(tmp_path, '{"text": "Hola"}\n')
    with pytest.raises(jsonschema.ValidationError):
        replay(path, lambda event: {"texto": "no canónico"})
