"""Tests de CT-7: fake LLM y fake core."""
import json
import socket

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.fakes.fake_core import FakeCore
from app.fakes.fake_llm import INTENTS, REPLIES, FakeLLM
from db.repo_root import repo_root

ROOT = repo_root()
EVENT_SCHEMA = json.loads((ROOT / "contracts" / "event.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(EVENT_SCHEMA, format_checker=FormatChecker())
INBOUND = json.loads((ROOT / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("los fakes no deben abrir sockets")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.mark.parametrize("text,intent", [
    ("Hola, buenas tardes", "saludo"),
    ("Busco un departamento de 2 recámaras", "busqueda_propiedad"),
    ("¿Cuánto cuesta?", "consulta_precio"),
    ("Quiero agendar una visita", "agendar_visita"),
    ("Quiero hablar con un asesor", "solicitud_humano"),
    ("¿Cómo va el clima hoy?", "otro"),
])
def test_intent_per_category(text, intent):
    assert FakeLLM().classify_intent(text) == intent


def test_case_and_accents_are_ignored():
    assert FakeLLM().classify_intent("QUIERO AGENDAR UNA VISITA") == "agendar_visita"
    assert FakeLLM().classify_intent("cuanto CUÉSTA") == "consulta_precio"


def test_first_matching_category_wins():
    # Mezcla saludo + búsqueda: gana búsqueda (más específica que el saludo).
    assert FakeLLM().classify_intent("Hola, busco casa") == "busqueda_propiedad"
    # Mezcla búsqueda + humano: gana el traspaso.
    assert FakeLLM().classify_intent("busco casa, pásame con un asesor") == "solicitud_humano"


def test_empty_text_is_treated_as_greeting():
    assert FakeLLM().classify_intent("") == "saludo"
    assert FakeLLM().classify_intent("   ") == "saludo"


def test_every_intent_has_a_fixed_reply():
    assert set(REPLIES) == set(INTENTS)
    assert all(FakeLLM().generate_reply(i) for i in INTENTS)


def test_unknown_intent_raises():
    with pytest.raises(ValueError):
        FakeLLM().generate_reply("inventada")


@pytest.mark.parametrize("text", ["Hola", "Busco depa", "¿Precio?", "Quiero una visita", "Un asesor", "xyz"])
def test_core_output_is_a_valid_canonical_event(text):
    out = FakeCore().handle_event(INBOUND | {"text": text})
    VALIDATOR.validate(out)
    assert out["direction"] == "outbound"


def test_core_keeps_conversation_and_lead_and_channel():
    out = FakeCore().handle_event(INBOUND)
    for field in ("conversation_id", "lead_id", "channel", "schema_version", "timestamp"):
        assert out[field] == INBOUND[field]
    assert out["event_id"] != INBOUND["event_id"]


def test_core_reply_matches_the_intent_reply():
    out = FakeCore().handle_event(INBOUND | {"text": "Quiero agendar una visita"})
    assert out["text"] == REPLIES["agendar_visita"]


def test_core_is_deterministic():
    assert FakeCore().handle_event(INBOUND) == FakeCore().handle_event(INBOUND)


def test_core_rejects_outbound_and_incomplete_events():
    with pytest.raises(ValueError):
        FakeCore().handle_event(INBOUND | {"direction": "outbound"})
    with pytest.raises(ValueError):
        FakeCore().handle_event({k: v for k, v in INBOUND.items() if k != "text"})


def test_replies_make_no_factual_claims():
    # Los fakes no deben afirmar cifras: eso lo verifican los guardrails reales.
    assert not any(ch.isdigit() for reply in REPLIES.values() for ch in reply)
