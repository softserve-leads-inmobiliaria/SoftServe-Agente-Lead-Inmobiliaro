"""Arnés de canal (CT-9): reproduce un guion `.jsonl` como eventos canónicos, sin Telegram real.

Cada línea del guion es un turno del lead (ver harness/README.md). El arnés construye el evento
`inbound` canónico (contracts/event.schema.json), se lo entrega a un *responder* y valida que la
respuesta sea otro evento canónico y cumpla lo que el guion espera.

Uso:
    python harness/scripts/replay.py harness/fixtures/saludo_y_busqueda.jsonl
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
EVENT_SCHEMA = json.loads((ROOT / "contracts" / "event.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(EVENT_SCHEMA, format_checker=FormatChecker())

_NAMESPACE = uuid.UUID("b7e2c1a4-5d3f-4e6a-9c8b-1d2e3f4a5b6c")
_START = datetime(2026, 9, 14, 15, 0, 0, tzinfo=timezone(timedelta(hours=-6)))
_TURN_GAP = timedelta(seconds=30)
_TURN_FIELDS = {"text", "property_id", "utm_campaign", "expect_reply_contains"}

Responder = Callable[[dict[str, Any]], dict[str, Any]]


class ScriptError(ValueError):
    """El guion está mal formado."""


def load_script(path: Path) -> list[dict[str, Any]]:
    """Lee un `.jsonl`: una línea = un turno. Las líneas en blanco se ignoran."""
    turns = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{path.name}:{number}"
        try:
            turn = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ScriptError(f"{where}: no es JSON válido ({exc.msg})") from exc
        if not isinstance(turn, dict) or not isinstance(turn.get("text"), str):
            raise ScriptError(f"{where}: cada turno necesita un campo `text` de tipo string")
        unknown = set(turn) - _TURN_FIELDS
        if unknown:
            raise ScriptError(f"{where}: campos desconocidos {sorted(unknown)}")
        expected = turn.get("expect_reply_contains", [])
        if isinstance(expected, str):
            turn["expect_reply_contains"] = [expected]
        elif not (isinstance(expected, list) and all(isinstance(e, str) for e in expected)):
            raise ScriptError(f"{where}: `expect_reply_contains` debe ser string o lista de strings")
        turns.append(turn)
    if not turns:
        raise ScriptError(f"{path.name}: el guion no tiene turnos")
    return turns


def build_events(name: str, turns: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eventos `inbound` deterministas: ids derivados del nombre del guion, tiempo simulado."""
    conversation_id = str(uuid.uuid5(_NAMESPACE, f"{name}/conversation"))
    lead_id = str(uuid.uuid5(_NAMESPACE, f"{name}/lead"))
    events = []
    for index, turn in enumerate(turns):
        event = {
            "schema_version": 1,
            "event_id": str(uuid.uuid5(_NAMESPACE, f"{name}/event/{index}")),
            "conversation_id": conversation_id,
            "lead_id": lead_id,
            "channel": "telegram",
            "direction": "inbound",
            "text": turn["text"],
            "timestamp": (_START + _TURN_GAP * index).isoformat(),
        }
        for optional in ("property_id", "utm_campaign"):
            if optional in turn:
                event[optional] = turn[optional]
        events.append(event)
    return events


def replay(path: Path, responder: Responder) -> list[dict[str, Any]]:
    """Reproduce el guion. Devuelve los eventos `outbound`; lanza AssertionError si algo falla."""
    turns = load_script(path)
    inbound_events = build_events(path.stem, turns)
    outbound_events = []
    for number, (turn, inbound) in enumerate(zip(turns, inbound_events, strict=True), start=1):
        VALIDATOR.validate(inbound)
        outbound = responder(inbound)
        VALIDATOR.validate(outbound)
        where = f"{path.name} turno {number}"
        assert outbound["direction"] == "outbound", f"{where}: la respuesta no es outbound"
        for key in ("conversation_id", "lead_id"):
            assert outbound[key] == inbound[key], f"{where}: la respuesta cambió {key}"
        for expected in turn["expect_reply_contains"]:
            assert expected.lower() in outbound["text"].lower(), (
                f"{where}: se esperaba {expected!r} en la respuesta {outbound['text']!r}"
            )
        outbound_events.append(outbound)
    return outbound_events


def default_responder() -> Responder:
    """Responder en proceso: el fake core (CT-7). CT-4 lo reemplaza por el core vía HTTP."""
    sys.path.insert(0, str(ROOT / "services" / "core"))
    sys.path.insert(0, str(ROOT))
    from app.fakes.fake_core import FakeCore

    return FakeCore().handle_event


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("uso: python harness/scripts/replay.py <guion.jsonl>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    try:
        outbound = replay(path, default_responder())
    except (ScriptError, AssertionError) as exc:
        print(f"FALLA {exc}", file=sys.stderr)
        return 1
    for turn, reply in zip(load_script(path), outbound, strict=True):
        print(f"lead : {turn['text']}\nbot  : {reply['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
