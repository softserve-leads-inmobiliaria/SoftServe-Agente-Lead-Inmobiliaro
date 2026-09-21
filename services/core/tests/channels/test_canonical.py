"""B-3: la conversión Telegram <-> evento canónico cumple CT-1 en ambos sentidos."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.channels.canonical import from_canonical, parse_deep_link, to_canonical
from db.repo_root import repo_root

APP_DIR = Path(__file__).resolve().parents[2] / "app"
_SCHEMA = Draft202012Validator(
    json.loads((repo_root() / "contracts" / "event.schema.json").read_text()),
    format_checker=FormatChecker(),
)

LEAD_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
CONVERSATION_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _event(**kwargs):
    return to_canonical(lead_id=LEAD_ID, conversation_id=CONVERSATION_ID, **kwargs)


def test_mensaje_simple_cumple_el_contrato():
    event = _event(text="hola")
    _SCHEMA.validate(event)
    assert event["direction"] == "inbound"
    assert event["channel"] == "telegram"
    assert event["schema_version"] == 1
    assert "property_id" not in event and "utm_campaign" not in event


def test_deep_link_de_propiedad_viaja_en_el_evento():
    event = _event(text="", **parse_deep_link("prop_00231"))
    _SCHEMA.validate(event)
    assert event["property_id"] == "prop_00231"


def test_deep_link_de_campana_viaja_en_el_evento():
    event = _event(text="", **parse_deep_link("camp_primavera"))
    _SCHEMA.validate(event)
    assert event["utm_campaign"] == "primavera"


@pytest.mark.parametrize("payload", [None, "", "prop_", "otra_cosa"])
def test_deep_link_desconocido_o_vacio_no_agrega_campos(payload):
    assert parse_deep_link(payload) == {}


def test_timestamp_sin_zona_se_asume_utc():
    event = _event(text="hola", timestamp=datetime(2026, 1, 1, 12, 0, 0))
    _SCHEMA.validate(event)
    assert event["timestamp"].endswith("+00:00")


def test_cada_evento_tiene_su_propio_id():
    assert _event(text="a")["event_id"] != _event(text="a")["event_id"]


def test_from_canonical_devuelve_el_texto_del_outbound():
    outbound = {**_event(text="hola"), "direction": "outbound", "text": "¡Hola!"}
    assert from_canonical(outbound) == "¡Hola!"


def test_from_canonical_rechaza_un_inbound():
    with pytest.raises(ValueError):
        from_canonical(_event(text="hola"))


def test_timestamp_con_zona_se_respeta():
    moment = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert _event(text="hola", timestamp=moment)["timestamp"] == moment.isoformat()


def test_ningun_modulo_fuera_del_canal_importa_telegram():
    """AC de B-3: los tipos del SDK no se filtran al resto de `core`."""
    offenders = [
        path.relative_to(APP_DIR)
        for path in APP_DIR.rglob("*.py")
        if "channels" not in path.parts
        and re.search(r"^\s*(import telegram\b|from telegram\b)", path.read_text(), re.M)
    ]
    assert offenders == []
