"""Tests de #217: la memoria de hechos verificados de la conversación.

Lo que una tool dijo de una propiedad en un turno anterior respalda los números de la respuesta (A-5), pero
nunca el estado, y vence. Lógica pura (`app/facts.py`), el agente con Bedrock guionado y el turno completo.
"""
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from bedrock_fakes import ScriptedBedrock, classification, text
from catalog_stub import stub_call
from sqlalchemy import text as sql

from app import facts, factuality
from app.agent import AgentResult, BedrockAgent, TurnInput
from app.bedrock_llm import BedrockLLM
from app.rate_limit import RateLimits
from app.turn import process_turn
from db.repo_root import repo_root

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
PROP = {"id": "prop_00231", "title": "2-bedroom apartment in El Poblado", "type": "venta", "kind": "departamento",
        "zone": "El Poblado", "price": 185000, "m2": 78.5, "bedrooms": 2, "bathrooms": 2,
        "amenities": ["parking", "gym"], "status": "disponible", "address_approx": "El Poblado, Medellin",
        "address_exact": "Carrera 37 # 8A-24, El Poblado", "photo_url": "https://example.com/p/231/800x600.jpg",
        "requirements": {"down_payment_percent": 10, "down_payment_min": 18500, "income_multiple": None,
                         "monthly_income_min": None, "references_required": False}}
OTHER = {"id": "prop_00235", "title": "2-bedroom apartment in Laureles", "price": 1150, "status": "disponible"}
ALT = {"id": "prop_00240", "title": "Studio in El Poblado", "price": 600, "status": "disponible"}


def _carry(current, previous=None, now=NOW):
    return facts.carry({}, current, previous or {}, now)


# --- qué se guarda ---------------------------------------------------------------------------------------

def test_only_the_citable_fields_are_kept_without_status_address_or_photo():
    saved = _carry({"get_property": PROP})[facts.FACTS_KEY]["prop_00231"]
    assert saved["price"] == 185000 and saved["requirements"]["down_payment_min"] == 18500
    assert {"status", "address_exact", "photo_url"}.isdisjoint(saved)
    assert saved[facts.SEEN_AT] == NOW.isoformat()


def test_search_results_and_alternatives_are_kept_too():
    saved = _carry({"search_properties": {"properties": [OTHER], "alternatives": [ALT], "unavailable": []}})
    assert set(saved[facts.FACTS_KEY]) == {"prop_00235", "prop_00240"}


def test_without_any_property_nothing_is_added():
    assert facts.carry({"x": 1}, {"list_zones": {"zones": []}}, {}, NOW) == {"x": 1}


def test_previous_facts_are_kept_and_the_current_turn_refreshes_its_property():
    earlier = _carry({"get_property": PROP}, now=NOW - timedelta(hours=2))
    saved = _carry({"get_property": {**PROP, "price": 180000}, "search_properties": {"properties": [OTHER]}},
                   earlier)[facts.FACTS_KEY]
    assert saved["prop_00231"]["price"] == 180000 and saved["prop_00231"][facts.SEEN_AT] == NOW.isoformat()
    assert "prop_00235" in saved


def test_facts_expire_after_the_ttl():
    earlier = _carry({"get_property": PROP}, now=NOW - facts.TTL - timedelta(minutes=1))
    assert facts.FACTS_KEY not in _carry({}, earlier)
    assert facts.evidence(earlier, NOW) == {}


def test_at_most_the_newest_properties_are_kept():
    previous = {}
    for i in range(facts.MAX_PROPERTIES + 3):
        previous = facts.carry(previous, {"get_property": {"id": f"p{i}", "price": i}}, previous,
                               NOW + timedelta(minutes=i))
    saved = previous[facts.FACTS_KEY]
    assert len(saved) == facts.MAX_PROPERTIES and "p0" not in saved and f"p{facts.MAX_PROPERTIES + 2}" in saved


def test_evidence_drops_the_time_and_ignores_what_it_cannot_read():
    previous = _carry({"get_property": PROP})
    previous[facts.FACTS_KEY]["broken"] = {"price": 1}  # sin hora: no vale
    previous[facts.FACTS_KEY]["bad_time"] = {"price": 2, facts.SEEN_AT: "ayer"}
    evidence = facts.evidence(previous, NOW + timedelta(hours=1))
    assert list(evidence) == ["prop_00231"] and facts.SEEN_AT not in evidence["prop_00231"]
    assert facts.evidence({facts.FACTS_KEY: "no es un dict"}, NOW) == {}


# --- la factualidad con lo recordado ---------------------------------------------------------------------

REMEMBERED = facts.evidence(_carry({"get_property": PROP}), NOW)


def test_a_remembered_price_backs_the_reply():
    assert factuality.check("It's $185,000 with 78.5 m² and a $18,500 down payment.", {}, "", REMEMBERED) == []


def test_a_number_no_tool_ever_gave_is_still_blocked():
    assert factuality.check("It's $175,000.", {}, "", REMEMBERED) == [factuality.Violation("number", "175000")]


def test_the_status_never_comes_from_memory():
    with_status = {"prop_00231": {**REMEMBERED["prop_00231"], "status": "disponible"}}
    assert factuality.check("Yes, it's still available.", {}, "", with_status) == [
        factuality.Violation("status", "disponible")]


def test_the_time_a_fact_was_seen_does_not_back_any_number():
    assert factuality.check("It was listed on 2026-10-06.", {}, "", REMEMBERED) != []


# --- el agente --------------------------------------------------------------------------------------------

EVENT = json.loads((repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))


class NoToolsCatalog:
    async def tool_specs(self):
        return []

    async def call(self, name, arguments):
        raise AssertionError("el turno no tendría que consultar el catálogo")


def _agent(*replies):
    client = ScriptedBedrock(classification("consulta_precio"), *replies)
    return BedrockAgent(BedrockLLM(client, "chat", "fast", RateLimits(10, 60.0)))


def test_repeating_a_price_from_an_earlier_turn_is_not_regenerated():
    turn = TurnInput(event=EVENT, text="how much was it again?", remembered=REMEMBERED)
    result = asyncio.run(_agent(text("It's $185,000.")).run(turn, NoToolsCatalog()))
    assert result.text == "It's $185,000." and result.guard_audits == []


def test_without_the_memory_the_same_reply_is_regenerated():
    turn = TurnInput(event=EVENT, text="how much was it again?")
    result = asyncio.run(_agent(text("It's $185,000."), text("Which property do you mean?"))
                         .run(turn, NoToolsCatalog()))
    assert result.guard_audits == [("output.factuality_regenerated", {"violations": 1})]


def test_a_remembered_amenity_backs_the_reply():
    turn = TurnInput(event=EVENT, text="does it have parking?", remembered=REMEMBERED)
    result = asyncio.run(_agent(text("Yes, it has parking and a gym.")).run(turn, NoToolsCatalog()))
    assert result.guard_audits == []


# --- el turno completo: se guarda y llega al turno siguiente ------------------------------------------------

INBOUND = {key: value for key, value in EVENT.items() if key not in ("property_id", "utm_campaign")}


class LooksUp:
    name = "consulta"

    async def run(self, turn, catalog):
        return AgentResult(text="It's $185,000.", intent="consulta_precio", tool_results={"get_property": PROP},
                           tool_calls=[{"name": "get_property", "is_error": False}])


class Remembers:
    name = "recuerda"

    def __init__(self):
        self.seen = None

    async def run(self, turn, catalog):
        self.seen = dict(turn.remembered)
        return AgentResult(text="Anything else?", intent="otro")


class Catalog:
    async def call(self, tool, arguments=None):
        return stub_call(tool, arguments)


def test_the_facts_are_saved_and_reach_the_next_turns(engine):
    conversation_id, lead_id = str(uuid.uuid4()), str(uuid.uuid4())

    def say(message, agent):
        event = INBOUND | {"conversation_id": conversation_id, "lead_id": lead_id, "event_id": str(uuid.uuid4()),
                           "text": message}
        asyncio.run(process_turn(engine, event, Catalog(), agent=agent))

    say("How much is the El Poblado apartment?", LooksUp())
    with engine.connect() as connection:
        saved = connection.execute(sql("SELECT last_tool_results FROM fsm_context WHERE conversation_id = :c"),
                                   {"c": conversation_id}).scalar_one()
    assert saved[facts.FACTS_KEY]["prop_00231"]["price"] == 185000
    assert "status" not in saved[facts.FACTS_KEY]["prop_00231"]

    for _ in range(2):  # un turno sin tools no la borra: sigue llegando al siguiente
        agent = Remembers()
        say("And what else?", agent)
        assert agent.seen["prop_00231"]["price"] == 185000 and facts.SEEN_AT not in agent.seen["prop_00231"]
