"""#247: de qué propiedad mostrada habla el mensaje (`refers_to`). Lo decide el clasificador, eligiendo solo entre
las propiedades que la conversación ya mostró; el código valida la elección y no adivina cuando no la hay."""
import asyncio
import uuid

import pytest
from bedrock_fakes import ScriptedBedrock, classification
from catalog_stub import stub_call
from test_bedrock_llm import llm
from test_visits_turn import INBOUND, agenda  # noqa: F401 (fixture)

from app.agent import UNCLEAR_NOTE, AgentResult, _with_notes
from app.bedrock_llm import Classification
from app.interest import (
    REMEMBERED_KEY,
    UNCLEAR,
    focused_property,
    refers_to,
    remember_property,
    shown_lines,
    shown_properties,
)
from app.turn import _visit_property, process_turn

HOUSE = {"id": "prop_00233", "title": "3-bedroom house in Usaquen", "kind": "casa", "type": "venta",
         "zone": "Usaquen", "price": 365000, "bedrooms": 3}
BIG_HOUSE = {"id": "prop_00234", "title": "4-bedroom house in Usaquen", "kind": "casa", "type": "venta",
             "zone": "Usaquen", "price": 520000, "bedrooms": 4}
STUDIO = {"id": "prop_00240", "title": "Studio for rent in El Poblado", "kind": "estudio", "type": "renta",
          "zone": "El Poblado", "price": 600, "bedrooms": 0}
REMEMBERED = {p["id"]: p for p in (HOUSE, BIG_HOUSE)}


# --- la lista y la validación ---------------------------------------------------------------------------------

def test_the_shown_properties_are_the_listing_and_the_memory_without_repeats_oldest_first():
    shown = shown_properties(REMEMBERED, listing=STUDIO)
    assert [p["id"] for p in shown] == ["prop_00240", "prop_00233", "prop_00234"]
    assert [p["id"] for p in shown_properties({**REMEMBERED, STUDIO["id"]: STUDIO}, STUDIO)][-1] == "prop_00240"


def test_each_shown_property_says_what_tells_it_apart_in_english():
    lines = shown_lines([HOUSE, STUDIO]).splitlines()
    assert lines == ["- prop_00233: 3-bedroom house in Usaquen (house, for sale, Usaquen, $365,000, 3 bedrooms)",
                     "- prop_00240: Studio for rent in El Poblado (studio, for rent, El Poblado, $600, 0 bedrooms)"]


def test_only_a_shown_property_or_unclear_is_accepted():
    shown = shown_properties(REMEMBERED)
    assert refers_to("prop_00234", shown) == "prop_00234"
    assert refers_to(UNCLEAR, shown) == UNCLEAR
    assert refers_to("prop_00455", shown) is None  # el lead nunca la vio: no se usa
    assert refers_to(None, shown) is None and refers_to(["prop_00233"], shown) is None


def test_the_focused_property_is_kept_without_its_status():
    assert focused_property("prop_00234", [{**BIG_HOUSE, "status": "apartado"}]) == {
        "id": "prop_00234", "title": "4-bedroom house in Usaquen", "zone": "Usaquen"}
    assert focused_property(UNCLEAR, [BIG_HOUSE]) is None and focused_property(None, [BIG_HOUSE]) is None


def test_the_focus_becomes_the_property_of_interest_over_a_search_but_not_over_a_looked_up_one():
    focus = focused_property("prop_00234", [BIG_HOUSE])
    searched = {"search_properties": {"properties": [HOUSE, BIG_HOUSE]}}
    assert remember_property(searched, {}, focus)[REMEMBERED_KEY] == focus
    looked = {"get_property": HOUSE}
    assert remember_property(looked, {}, focus)[REMEMBERED_KEY]["id"] == "prop_00233"


# --- el clasificador ------------------------------------------------------------------------------------------

def test_the_classifier_sees_the_shown_properties_and_an_invented_id_is_dropped():
    client = ScriptedBedrock(classification("otro", refers_to="prop_00234"),
                             classification("otro", refers_to="prop_00455"))
    shown = shown_properties(REMEMBERED)
    assert asyncio.run(llm(client).classify("And the bigger one?", shown=shown)).refers_to == "prop_00234"
    prompt = client.requests[0]["messages"][0]["content"][0]["text"]
    assert prompt.startswith("PROPERTIES SHOWN (oldest first):\n- prop_00233: 3-bedroom house in Usaquen")
    assert asyncio.run(llm(client).classify("The one on 5th avenue", shown=shown)).refers_to is None


def test_without_shown_properties_the_prompt_does_not_list_any():
    client = ScriptedBedrock(classification("saludo"))
    assert asyncio.run(llm(client).classify("Hi")).refers_to is None
    assert "PROPERTIES SHOWN" not in client.requests[0]["messages"][0]["content"][0]["text"]


# --- lo que lee el modelo de chat -----------------------------------------------------------------------------

def test_the_chat_model_is_told_which_property_the_message_is_about():
    # Prueba de jurado M4 t6: "the price of the reserved apartment" se contestó con una referencia inventada.
    text = _with_notes("What's the price of the bigger one?", None, remembered=REMEMBERED, about="prop_00234")
    assert "(The lead's message is about prop_00234 (4-bedroom house in Usaquen).)" in text


def test_when_it_is_unclear_the_chat_model_is_told_to_ask():
    assert _with_notes("I want to see it", None, remembered=REMEMBERED, about=UNCLEAR).endswith(UNCLEAR_NOTE)


# --- la propiedad que se agenda -------------------------------------------------------------------------------

def test_the_focus_decides_the_property_to_visit_over_the_first_search_result():
    # "the cheaper one" agendaba el primero de la búsqueda (#247).
    searched = {"search_properties": {"properties": [BIG_HOUSE, HOUSE]}}
    assert _visit_property(searched, {}, "prop_00233", [HOUSE, BIG_HOUSE])["id"] == "prop_00233"


def test_unclear_never_guesses_even_with_a_remembered_property():
    previous = {REMEMBERED_KEY: {"id": "prop_00233", "title": "3-bedroom house in Usaquen"}}
    assert _visit_property({}, previous)["id"] == "prop_00233"
    assert _visit_property({}, previous, UNCLEAR, [HOUSE, BIG_HOUSE]) is None


# --- en turnos reales -----------------------------------------------------------------------------------------

class Catalog:
    """El catálogo de prueba, con el estado actual de cada propiedad."""

    def __init__(self, statuses):
        self.statuses, self.checked = statuses, []

    async def call(self, tool, arguments=None):
        if tool == "get_availability":
            self.checked.append(arguments["property_id"])
            return {"property_id": arguments["property_id"], "status": self.statuses[arguments["property_id"]],
                    "checked_at": "2026-10-07T12:00:00Z"}
        return stub_call(tool, arguments)


class Scripted:
    """Un agente con turnos guionados: busca dos casas (o mira la ficha de `first`), y después pide visitar la que
    diga `refers_to`."""

    name = "guionado"

    def __init__(self, about, first=None):
        self.about, self.turns = about, 0
        found = {"properties": [{**BIG_HOUSE, "status": "apartado"}, {**HOUSE, "status": "disponible"}]}
        self.first = first or {"search_properties": found}

    async def run(self, turn, catalog):
        self.turns += 1
        if self.turns == 1:
            return AgentResult(text="Here it is.", intent="busqueda_propiedad", tool_results=self.first,
                               tool_calls=[{"name": name, "is_error": False} for name in self.first])
        flags = {"wants_visit": True}
        # Como el agente de Bedrock: si la agenda va a contestar, solo clasifica; si no, pregunta cuál.
        answered = turn.answered_by_code(Classification("agendar_visita", {}, flags, None, self.about))
        return AgentResult(text="" if answered else "Which of the two houses would you like to see?",
                           intent="agendar_visita", flags=flags, refers_to=self.about,
                           route="agenda" if answered else "agent")


def _conversation(engine, about, statuses, first=None):
    agent, catalog = Scripted(about, first), Catalog(statuses)
    ids = {"conversation_id": str(uuid.uuid4()), "lead_id": str(uuid.uuid4())}

    def say(message):
        event = INBOUND | ids | {"event_id": str(uuid.uuid4()), "text": message}
        return asyncio.run(process_turn(engine, event, catalog, agent=agent))["text"]
    return say, catalog


def test_the_visit_is_offered_for_the_property_the_lead_means(engine, agenda):  # noqa: F811
    say, catalog = _conversation(engine, "prop_00233", {"prop_00233": "disponible", "prop_00234": "apartado"})
    say("Houses in Usaquen?")
    reply = say("I'd like to see the cheaper one")
    assert reply.startswith("Here are the available times to visit 3-bedroom house in Usaquen")
    assert catalog.checked == ["prop_00233"]


def test_a_reserved_property_gets_no_slots_and_the_lead_is_told_why(engine, agenda):  # noqa: F811
    # Prueba de jurado: pedir la casa apartada ofrecía horarios de la otra.
    say, catalog = _conversation(engine, "prop_00234", {"prop_00233": "disponible", "prop_00234": "apartado"})
    say("Houses in Usaquen?")
    reply = say("Can I visit the 4-bedroom one?")
    assert reply.startswith("4-bedroom house in Usaquen is reserved right now, so I can't book a visit to it.")
    assert not any(operation == "get_slots" for operation, _ in agenda.calls)


@pytest.mark.parametrize("about", [UNCLEAR, None])
def test_without_knowing_which_one_no_visit_is_offered(engine, agenda, about):  # noqa: F811
    say, _ = _conversation(engine, about, {})
    say("Houses in Usaquen?")
    assert say("ok i want to see it") == "Which of the two houses would you like to see?"
    assert agenda.calls == []


def test_a_remembered_reserved_property_gets_no_slots_either(engine, agenda):  # noqa: F811
    # Prueba de jurado O6: la recordada se guarda sin estado, y desde el anuncio de la casa apartada "something
    # similar that I can visit" ofrecía horarios de esa misma casa.
    say, catalog = _conversation(engine, None, {"prop_00234": "apartado"},
                                 first={"get_property": BIG_HOUSE})  # sin estado, como la recordada
    say("Tell me about the 4-bedroom house")
    reply = say("Show me something similar that I can visit")
    assert reply.startswith("4-bedroom house in Usaquen is reserved right now")
    assert catalog.checked == ["prop_00234"] and not any(op == "get_slots" for op, _ in agenda.calls)
