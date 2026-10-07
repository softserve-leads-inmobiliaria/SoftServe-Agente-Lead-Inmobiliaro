"""Tests de A-2: el orquestador. Qué ve el LLM, qué decide el código y qué pasa cuando el LLM falla."""
import asyncio
import json

import pytest
from bedrock_fakes import FakeCatalog, ScriptedBedrock, classification, text, tool_use
from botocore.exceptions import ClientError

from app.agent import (
    FACTUALITY_DEGRADE_REPLY,
    HUMAN_REQUEST_UNCONFIRMED,
    LLM_FALLBACK_REPLY,
    PROTECTED_ANSWER,
    PROTECTED_NOTE,
    BedrockAgent,
    FakeAgent,
    TurnInput,
)
from app.bedrock_llm import BedrockLLM
from app.fakes.fake_llm import REPLIES
from app.input_guard import OMITTED_MESSAGE
from app.rate_limit import RateLimits
from db.repo_root import repo_root

EVENT = json.loads((repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))
CHAT, FAST = "modelo-chat", "modelo-rapido"


def agent_for(client):
    return BedrockAgent(BedrockLLM(client, CHAT, FAST, RateLimits(10, 60.0)))


def turn(message="I'm looking for a house in El Poblado", history=(), lead_qualified=False):
    return TurnInput(event=EVENT, text=message, history=tuple(history), lead_qualified=lead_qualified)


def run(coro):
    return asyncio.run(coro)


def throttled():
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "Converse")


# --- un turno normal -------------------------------------------------------------------------------------

def test_a_normal_turn_classifies_calls_the_catalog_and_answers():
    client = ScriptedBedrock(classification("busqueda_propiedad", budget_hint="3 million"),
                             tool_use("search_properties", {"zone": "El Poblado"}),
                             text("I found a house in El Poblado."))
    catalog = FakeCatalog()
    result = run(agent_for(client).run(turn(), catalog))
    assert (result.text, result.intent) == ("I found a house in El Poblado.", "busqueda_propiedad")
    assert result.raw_signal["budget_hint"] == "3 million"
    assert result.tool_results == {"search_properties": {"properties": [{"id": "p1", "title": "Casa de prueba", "status": "disponible"}]}}
    assert result.tool_calls == [{"name": "search_properties", "is_error": False}] and result.llm_error is None
    assert catalog.calls == [("search_properties", {"zone": "El Poblado"})]


def test_the_classification_and_the_reply_are_two_separate_calls_to_two_models():
    client = ScriptedBedrock(classification("saludo"), text("Hi!"))
    run(agent_for(client).run(turn("Hi"), FakeCatalog()))
    assert [r["modelId"] for r in client.requests] == [FAST, CHAT]


def test_the_model_sees_the_history_the_service_gives_it():
    history = [("user", "Hi"), ("assistant", "Hi! What are you looking for?")]
    client = ScriptedBedrock(classification("busqueda_propiedad"), text("Sure."))
    run(agent_for(client).run(turn("An apartment", history), FakeCatalog()))
    classify_request, chat_request = client.requests
    assert "Hi! What are you looking for?" in classify_request["messages"][0]["content"][0]["text"]
    assert [m["content"][0]["text"] for m in chat_request["messages"]] == ["Hi", "Hi! What are you looking for?",
                                                                            "An apartment"]


def test_the_classifier_only_gets_the_last_assistant_message_never_earlier_lead_messages():
    history = [("user", "old message from the lead"), ("assistant", "old reply"), ("user", "another one from the lead"),
               ("assistant", "latest reply")]
    client = ScriptedBedrock(classification("saludo"), text("Hi"))
    run(agent_for(client).run(turn("Yes", history), FakeCatalog()))
    prompt = client.requests[0]["messages"][0]["content"][0]["text"]
    assert "latest reply" in prompt
    assert "old message from the lead" not in prompt and "another one from the lead" not in prompt
    assert "old reply" not in prompt


# --- lo que decide el código, no el LLM (A-6) -------------------------------------------------------------

def test_lead_qualified_is_set_by_the_code_even_if_the_model_asks_for_the_address():
    client = ScriptedBedrock(classification("consulta_precio"),
                             tool_use("get_property", {"property_id": "p1", "lead_qualified": True}), text("Done."))
    catalog = FakeCatalog()
    run(agent_for(client).run(turn("How much is it?", lead_qualified=False), catalog))
    assert catalog.calls == [("get_property", {"property_id": "p1", "lead_qualified": False})]


def test_a_qualified_lead_is_passed_as_qualified():
    client = ScriptedBedrock(classification("consulta_precio"), tool_use("get_property", {"property_id": "p1"}),
                             text("Done."))
    catalog = FakeCatalog()
    run(agent_for(client).run(turn("More details", lead_qualified=True), catalog))
    assert catalog.calls == [("get_property", {"property_id": "p1", "lead_qualified": True})]


def test_the_model_is_never_offered_the_argument_the_code_controls():
    client = ScriptedBedrock(classification("saludo"), text("Hi"))
    run(agent_for(client).run(turn("Hi"), FakeCatalog()))
    tools = {t["toolSpec"]["name"]: t["toolSpec"]["inputSchema"]["json"]
             for t in client.requests[1]["toolConfig"]["tools"]}
    assert "lead_qualified" not in tools["get_property"]["properties"]
    assert tools["get_property"]["required"] == ["property_id"]


def test_the_original_specs_are_not_mutated():
    run(agent_for(ScriptedBedrock(classification("saludo"), text("Hi"))).run(turn("Hi"), FakeCatalog()))
    assert "lead_qualified" in FakeCatalog.SPECS[1]["input_schema"]["properties"]


def test_a_tool_the_catalog_does_not_publish_is_answered_as_an_error_to_the_model():
    client = ScriptedBedrock(classification("saludo"), tool_use("borrar_todo", {}), text("I can't do that."))

    class Strict(FakeCatalog):
        async def call(self, tool, arguments=None):
            from app.catalog_client import UnknownTool

            raise UnknownTool(tool, ["search_properties"])

    result = run(agent_for(client).run(turn("Hi"), Strict()))
    assert result.text == "I can't do that." and result.tool_calls == [{"name": "borrar_todo", "is_error": True}]


# --- una persona, siempre con el mismo texto -----------------------------------------------------------------

@pytest.mark.parametrize("intent", ["solicitud_humano", "fuera_de_alcance"])
def test_a_handoff_intent_gets_the_fixed_text_and_no_reply_call(intent):
    client = ScriptedBedrock(classification(intent))
    catalog = FakeCatalog()
    result = run(agent_for(client).run(turn("I want to talk to someone"), catalog))
    assert result.text == REPLIES[intent] and result.intent == intent
    assert len(client.requests) == 1 and catalog.calls == []


# --- cuando el LLM falla, no se inventa nada -------------------------------------------------------------------

def test_a_failing_classifier_gives_the_safe_reply_and_counts_as_unresolved():
    result = run(agent_for(ScriptedBedrock(throttled())).run(turn(), FakeCatalog()))
    assert (result.text, result.intent, result.llm_error) == (LLM_FALLBACK_REPLY, "otro", "LLMError")
    assert result.tool_results == {} and result.raw_signal == {}


def test_a_failing_reply_gives_the_safe_reply_and_drops_the_partial_work():
    client = ScriptedBedrock(classification("busqueda_propiedad", budget_hint="3 million"), throttled())
    result = run(agent_for(client).run(turn(), FakeCatalog()))
    assert result.text == LLM_FALLBACK_REPLY and result.intent == "otro" and result.raw_signal == {}


def test_an_unparseable_classification_gives_the_safe_reply():
    result = run(agent_for(ScriptedBedrock(text("not sure"))).run(turn(), FakeCatalog()))
    assert result.text == LLM_FALLBACK_REPLY and result.llm_error == "LLMFormatError"


def test_an_empty_reply_gives_the_safe_reply():
    client = ScriptedBedrock(classification("saludo"), text("   "))
    assert run(agent_for(client).run(turn("Hi"), FakeCatalog())).text == LLM_FALLBACK_REPLY


def test_a_catalog_outage_is_not_swallowed_as_an_llm_problem():
    class Down(FakeCatalog):
        async def tool_specs(self):
            raise ConnectionError("catálogo caído")

    with pytest.raises(ConnectionError):
        run(agent_for(ScriptedBedrock(classification("busqueda_propiedad"))).run(turn(), Down()))


# --- mensajes sin contenido utilizable: no se gasta una llamada -------------------------------------------------------

def test_an_empty_message_is_a_greeting_and_never_reaches_the_llm():
    client = ScriptedBedrock()
    result = run(agent_for(client).run(turn(""), FakeCatalog()))
    assert result.intent == "saludo" and result.text == REPLIES["saludo"] and client.requests == []


def test_a_message_with_nothing_left_after_filtering_is_the_catch_all_without_an_llm_call():
    client = ScriptedBedrock()
    result = run(agent_for(client).run(turn(OMITTED_MESSAGE), FakeCatalog()))
    assert result.intent == "otro" and client.requests == []


# --- el agente falso conserva el comportamiento de siempre ----------------------------------------------------

def test_the_fake_agent_keeps_the_original_behavior():
    catalog = FakeCatalog(properties=[{"title": "Casa de prueba"}])
    result = run(FakeAgent().run(turn("I'm looking for an apartment"), catalog))
    assert result.intent == "busqueda_propiedad" and result.text.endswith("For example: Casa de prueba.")
    assert list(result.tool_results) == ["search_properties"]


def test_the_fake_agent_extracts_the_raw_signal_from_the_filtered_text():
    result = run(FakeAgent().run(turn("I decide and it's urgent"), FakeCatalog()))
    assert result.raw_signal["decision_maker"] == "declarado" and result.raw_signal["urgency"] == "inmediata"


def test_the_agents_have_names_for_the_traces():
    assert FakeAgent.name == "fake" and BedrockAgent.name == "bedrock"


# --- A-5: factualidad de la respuesta -----------------------------------------------------------------------------

def test_a_factual_reply_never_triggers_a_regeneration_call():
    """Una llamada de más al modelo haría fallar el test con `IndexError` (`ScriptedBedrock`). El precio lo pone
    el código en el marcador (DT-60)."""
    client = ScriptedBedrock(classification("busqueda_propiedad"), tool_use("search_properties", {"zone": "Prov."}),
                             text("I have one at {p1.price}."))
    catalog = FakeCatalog(properties=[{"id": "p1", "title": "Casa", "price": 2350000, "status": "disponible"}])
    result = run(agent_for(client).run(turn(), catalog))
    assert result.text == "I have one at $2,350,000." and result.guard_audits == []


def test_an_invented_price_gets_one_chance_to_regenerate_and_the_fix_is_used():
    client = ScriptedBedrock(classification("busqueda_propiedad"), tool_use("search_properties", {"zone": "Prov."}),
                             text("I have one at $9,999,999."), text("I have one at {p1.price}."))
    catalog = FakeCatalog(properties=[{"id": "p1", "title": "Casa", "price": 2350000, "status": "disponible"}])
    result = run(agent_for(client).run(turn(), catalog))
    assert result.text == "I have one at $2,350,000." and result.intent == "busqueda_propiedad"
    assert result.guard_audits == [("output.factuality_regenerated", {"violations": 1})]
    assert result.tool_results == {"search_properties": {"properties": catalog.properties}}


def test_an_invented_price_that_survives_the_regeneration_degrades_to_an_advisor_offer():
    client = ScriptedBedrock(classification("busqueda_propiedad"), tool_use("search_properties", {"zone": "Prov."}),
                             text("I have one at $9,999,999."), text("It still costs $9,999,999."))
    catalog = FakeCatalog(properties=[{"id": "p1", "title": "Casa", "price": 2350000, "status": "disponible"}])
    result = run(agent_for(client).run(turn(), catalog))
    assert result.text == FACTUALITY_DEGRADE_REPLY and result.intent == "otro"
    assert [a for a, _ in result.guard_audits] == ["output.factuality_regenerated", "output.factuality_degraded"]


def test_the_lead_qualified_flag_never_changes_after_a_regeneration():
    """El código sigue controlando `lead_qualified` (A-6) también en la respuesta regenerada."""
    client = ScriptedBedrock(classification("consulta_precio"),
                             tool_use("get_property", {"property_id": "p1", "lead_qualified": True}),
                             text("It costs $9,999,999."), text("It costs whatever the listing says."))
    result = run(agent_for(client).run(turn(lead_qualified=False), FakeCatalog()))
    assert result.text == "It costs whatever the listing says."
    assert "lead_qualified" not in json.dumps(client.requests[1]["toolConfig"])


def test_the_regeneration_can_call_a_tool_again_and_the_calls_are_all_kept():
    client = ScriptedBedrock(classification("busqueda_propiedad"), tool_use("search_properties", {"zone": "Prov."}),
                             text("I have one at $9,999,999."),
                             tool_use("search_properties", {"zone": "Prov."}, tool_use_id="tool-2"),
                             text("I have one at {p1.price}."))
    catalog = FakeCatalog(properties=[{"id": "p1", "title": "Casa", "price": 2350000, "status": "disponible"}])
    result = run(agent_for(client).run(turn(), catalog))
    assert result.text == "I have one at $2,350,000."
    assert result.tool_calls == [{"name": "search_properties", "is_error": False}] * 2
    assert catalog.calls == [("search_properties", {"zone": "Prov."})] * 2


def test_the_regeneration_already_has_the_tool_results_and_does_not_ask_for_them_again():
    """#232: antes, la regeneración partía del historial sin las rondas de tools y el modelo volvía a buscar."""
    client = ScriptedBedrock(classification("busqueda_propiedad"), tool_use("search_properties", {"zone": "Prov."}),
                             text("I have one at $9,999,999."), text("I have one at {p1.price}."))
    catalog = FakeCatalog(properties=[{"id": "p1", "title": "Casa", "price": 2350000, "status": "disponible"}])
    result = run(agent_for(client).run(turn(), catalog))
    assert result.text == "I have one at $2,350,000."
    assert len(client.calls_to(CHAT)) == 3  # búsqueda, borrador y una sola llamada para corregir
    assert '"price": 2350000' in json.dumps(client.calls_to(CHAT)[-1]["messages"])  # el resultado ya está
    assert catalog.calls == [("search_properties", {"zone": "Prov."})]


def test_the_correction_prompt_shows_the_model_its_own_reply_and_what_was_wrong():
    client = ScriptedBedrock(classification("busqueda_propiedad"), tool_use("search_properties", {"zone": "Prov."}),
                             text("I have one at $9,999,999."), text("I have one at {p1.price}."))
    catalog = FakeCatalog(properties=[{"id": "p1", "title": "Casa", "price": 2350000, "status": "disponible"}])
    run(agent_for(client).run(turn(), catalog))
    correction_request = client.calls_to(CHAT)[-1]
    correction_text = correction_request["system"][0]["text"]
    assert "<draft>I have one at $9,999,999.</draft>" in correction_text and "9999999" in correction_text


def test_the_correction_goes_in_the_system_so_the_model_answers_the_lead_and_not_a_correction():
    # #213, en vivo: como mensaje `user`, el modelo creía que el lead lo había corregido y contestaba
    # "You're right, I apologize… Let me search", sin buscar.
    client = ScriptedBedrock(classification("busqueda_propiedad"), tool_use("search_properties", {"zone": "Prov."}),
                             text("I have one at $9,999,999."), text("I have one at {p1.price}."))
    catalog = FakeCatalog(properties=[{"id": "p1", "title": "Casa", "price": 2350000, "status": "disponible"}])
    run(agent_for(client).run(turn(), catalog))
    first, correction = client.calls_to(CHAT)[0], client.calls_to(CHAT)[-1]
    assert correction["messages"] == first["messages"][:len(correction["messages"])]
    # Lo último que dijo alguien es el lead; después solo vienen los resultados de sus tools (#232), nunca el borrador.
    said = [(m["role"], block["text"]) for m in correction["messages"] for block in m["content"] if "text" in block]
    assert said[-1] == ("user", "I'm looking for a house in El Poblado")
    assert correction["messages"][-1]["role"] == "user" and "9,999,999" not in json.dumps(correction["messages"])
    instruction = correction["system"][0]["text"]
    assert instruction.startswith(first["system"][0]["text"]) and "the lead never sees this" in instruction
    assert "Do not apologize" in instruction and "Never tell the lead you will look something up later" in instruction
    # Y le pide los datos como marcadores (DT-60), no como números que escriba.
    assert "Write every property fact as a marker" in instruction


# --- A-6: dirección reservada en prosa ----------------------------------------------------------------------------

def test_an_address_in_prose_gets_one_chance_to_regenerate_and_the_fix_is_used():
    """El número de la dirección tampoco viene de ninguna tool: A-5 y A-6 la marcan las dos
    (violations=2) — la doble red que describe DT-23, no una casualidad del gold set."""
    client = ScriptedBedrock(classification("consulta_precio"),
                             tool_use("get_property", {"property_id": "p1", "lead_qualified": False}),
                             text("It's at Carrera 43A # 1204, El Poblado."),
                             text("It's in El Poblado, near Parque Lleras."))
    result = run(agent_for(client).run(turn(lead_qualified=False), FakeCatalog()))
    assert result.text == "It's in El Poblado, near Parque Lleras."
    assert result.guard_audits == [("output.factuality_regenerated", {"violations": 2})]


def test_an_address_that_survives_regeneration_degrades_to_an_advisor_offer():
    client = ScriptedBedrock(classification("consulta_precio"),
                             tool_use("get_property", {"property_id": "p1", "lead_qualified": False}),
                             text("It's at Carrera 43A # 1204, El Poblado."),
                             text("It's still at Carrera 43A # 1204, El Poblado."))
    result = run(agent_for(client).run(turn(lead_qualified=False), FakeCatalog()))
    assert result.text == FACTUALITY_DEGRADE_REPLY and result.intent == "otro"
    assert [a for a, _ in result.guard_audits] == ["output.factuality_regenerated", "output.factuality_degraded"]


# --- A-7: promesas no autorizadas ---------------------------------------------------------------------------------

def test_an_unauthorized_promise_gets_one_chance_to_regenerate_and_the_fix_is_used():
    client = ScriptedBedrock(classification("consulta_precio"),
                             tool_use("get_property", {"property_id": "p1", "lead_qualified": False}),
                             text("Your credit is already approved, we can move forward."),
                             text("I'll check your situation with an agent."))
    result = run(agent_for(client).run(turn(lead_qualified=False), FakeCatalog()))
    assert result.text == "I'll check your situation with an agent."
    assert result.guard_audits == [("output.factuality_regenerated", {"violations": 1})]


def test_an_unauthorized_promise_that_survives_regeneration_degrades_to_an_advisor_offer():
    client = ScriptedBedrock(classification("consulta_precio"),
                             tool_use("get_property", {"property_id": "p1", "lead_qualified": False}),
                             text("Your credit is already approved, we can move forward."),
                             text("Your credit is already approved, so we can go ahead with the purchase."))
    result = run(agent_for(client).run(turn(lead_qualified=False), FakeCatalog()))
    assert result.text == FACTUALITY_DEGRADE_REPLY and result.intent == "otro"
    assert [a for a, _ in result.guard_audits] == ["output.factuality_regenerated", "output.factuality_degraded"]


def test_a_throttling_error_during_the_regeneration_still_falls_back_to_the_safe_reply():
    client = ScriptedBedrock(classification("busqueda_propiedad"), tool_use("search_properties", {"zone": "Prov."}),
                             text("I have one at $9,999,999."), throttled())
    catalog = FakeCatalog(properties=[{"id": "p1", "title": "Casa", "price": 2350000, "status": "disponible"}])
    result = run(agent_for(client).run(turn(), catalog))
    assert result.text == LLM_FALLBACK_REPLY and result.intent == "otro" and result.llm_error == "LLMError"


# --- #122: la propiedad del anuncio -----------------------------------------------------------------------------

LISTING = {"id": "prop_00231", "title": "2-bedroom apartment in El Poblado", "zone": "El Poblado"}


def test_the_model_is_told_which_listing_the_lead_came_from():
    client = ScriptedBedrock(classification("consulta_precio"), text("Do you mean that apartment?"))
    message = TurnInput(event=EVENT, text="Is it still available?", listing=LISTING)
    run(agent_for(client).run(message, FakeCatalog()))
    sent = client.calls_to(CHAT)[0]["messages"][-1]["content"][0]["text"]
    assert sent.startswith("Is it still available?") and "ref. prop_00231" in sent


# --- #200: el guardrail quitó una mención de categoría protegida --------------------------------------------

def test_the_model_is_told_a_protected_mention_was_removed_but_not_what_it_was():
    client = ScriptedBedrock(classification("consulta_precio"), text("No, that doesn't affect anything."))
    message = TurnInput(event=EVENT, text="does that matter? how much is it?", protected_removed=True)
    run(agent_for(client).run(message, FakeCatalog()))
    sent = client.calls_to(CHAT)[0]["messages"][-1]["content"][0]["text"]
    assert sent.startswith("does that matter? how much is it?") and PROTECTED_NOTE in sent
    classified = client.calls_to(FAST)[0]["messages"][0]["content"][0]["text"]
    assert PROTECTED_NOTE not in classified  # el clasificador solo ve lo que dijo el lead


def test_without_a_removed_mention_there_is_no_note():
    client = ScriptedBedrock(classification("otro"), text("Sure."))
    run(agent_for(client).run(turn("does that matter?"), FakeCatalog()))
    assert PROTECTED_NOTE not in client.calls_to(CHAT)[0]["messages"][-1]["content"][0]["text"]


def test_the_bedrock_agent_answers_that_a_protected_characteristic_does_not_matter():
    # En vivo, el modelo ignoraba la nota y volvía al enganche: la respuesta es fija, sin llamar al chat.
    client = ScriptedBedrock(classification("otro"))
    message = TurnInput(event=EVENT, text="does that matter?", protected_removed=True)
    result = run(agent_for(client).run(message, FakeCatalog()))
    assert (result.text, result.intent) == (PROTECTED_ANSWER, "otro")
    assert client.calls_to(CHAT) == []


def test_the_fake_agent_answers_that_a_protected_characteristic_does_not_matter():
    message = TurnInput(event=EVENT, text="does that matter?", protected_removed=True)
    result = run(FakeAgent().run(message, FakeCatalog()))
    assert (result.text, result.intent, result.tool_results) == (PROTECTED_ANSWER, "otro", {})


def test_the_fake_agent_still_answers_any_other_question_in_the_message():
    message = TurnInput(event=EVENT, text="how much is it?", protected_removed=True)
    assert run(FakeAgent().run(message, FakeCatalog())).intent == "consulta_precio"


def test_the_listing_note_never_counts_as_something_the_lead_said():
    # "00231" y "2" están en la nota, no en lo que dijo el lead: un precio inventado sigue siendo inventado.
    client = ScriptedBedrock(classification("consulta_precio"), text("It costs $2,000,000."),
                             text("It costs $2,000,000."))
    message = TurnInput(event=EVENT, text="How much is it?", listing=LISTING)
    result = run(agent_for(client).run(message, FakeCatalog()))
    assert result.text == FACTUALITY_DEGRADE_REPLY


# --- #234: anunciar una búsqueda para después se regenera --------------------------------------------------

def test_announcing_a_lookup_without_calling_any_tool_is_regenerated():
    client = ScriptedBedrock(classification("otro"), text("Let me check if there are any current promotions."),
                             text("There are no promotions I can confirm right now. What's your budget?"))
    result = run(agent_for(client).run(turn("Can you give me a discount?"), FakeCatalog()))
    assert result.text.startswith("There are no promotions")
    assert result.guard_audits == [("output.factuality_regenerated", {"violations": 1})]


# --- #228: un traspaso solo si el mensaje lo pide ------------------------------------------------------------

OFFER = ("I can't promise discounts. However, I can put you in touch with an agent who can discuss payment options.")


def test_a_message_that_does_not_ask_for_a_person_is_not_handed_off_even_if_the_classifier_says_so():
    """En vivo (#217, `03_guardrails`): con los datos sensibles quitados, el clasificador leyó como pedido de
    una persona un mensaje que llegaba después de la oferta de un asesor."""
    client = ScriptedBedrock(classification("solicitud_humano"), text("I don't need that information."))
    message = "My ID number is [redacted] and my card is [redacted]"
    result = run(agent_for(client).run(turn(message, history=[("user", "Discount?"), ("assistant", OFFER)]),
                                       FakeCatalog()))
    assert result.intent == "otro" and result.text == "I don't need that information."
    assert result.guard_audits == [(HUMAN_REQUEST_UNCONFIRMED, {})]


@pytest.mark.parametrize("message", ["I want to talk to an agent", "Can someone call me?", "Is there a real person?"])
def test_asking_for_a_person_still_hands_off(message):
    result = run(agent_for(ScriptedBedrock(classification("solicitud_humano"))).run(turn(message), FakeCatalog()))
    assert result.intent == "solicitud_humano" and result.text == REPLIES["solicitud_humano"]


@pytest.mark.parametrize("message", ["yes please", "Sure, go ahead", "ok"])
def test_agreeing_to_the_advisor_offer_hands_off(message):
    result = run(agent_for(ScriptedBedrock(classification("solicitud_humano")))
                 .run(turn(message, history=[("user", "Discount?"), ("assistant", OFFER)]), FakeCatalog()))
    assert result.intent == "solicitud_humano"


def test_a_yes_without_an_advisor_offer_is_not_a_handoff():
    client = ScriptedBedrock(classification("solicitud_humano"), text("Great! Which area?"))
    history = [("user", "Hi"), ("assistant", "Would you like to see apartments in Laureles?")]
    assert run(agent_for(client).run(turn("yes please", history=history), FakeCatalog())).intent == "otro"


def test_a_long_message_that_starts_with_yes_is_not_taken_as_accepting_the_offer():
    client = ScriptedBedrock(classification("solicitud_humano"), text("Got it."))
    message = "Yes, but first tell me whether the apartment in Laureles has parking and a gym"
    result = run(agent_for(client).run(turn(message, history=[("assistant", OFFER)]), FakeCatalog()))
    assert result.intent == "otro"


# --- DT-60: los datos de la propiedad los pone el código ------------------------------------------------------

P1 = [{"id": "p1", "title": "Casa", "price": 2350000, "bedrooms": 3, "status": "disponible"}]


def test_the_lead_reads_the_values_the_code_put_in_the_markers():
    client = ScriptedBedrock(classification("consulta_precio"), tool_use("search_properties", {"zone": "Prov."}),
                             text("It's {p1.price}, with {p1.bedrooms} bedrooms, and it's {p1.status}."))
    result = run(agent_for(client).run(turn(), FakeCatalog(properties=P1)))
    assert result.text == "It's $2,350,000, with 3 bedrooms, and it's available." and result.guard_audits == []


def test_a_figure_typed_from_the_tool_result_is_regenerated_with_the_marker_to_use():
    """Escrito a mano, el precio real de una propiedad podría ser el de otra: se pide el marcador."""
    client = ScriptedBedrock(classification("consulta_precio"), tool_use("search_properties", {"zone": "Prov."}),
                             text("It's $2,350,000."), text("It's {p1.price}."))
    result = run(agent_for(client).run(turn(), FakeCatalog(properties=P1)))
    assert result.text == "It's $2,350,000." and [a for a, _ in result.guard_audits] == ["output.factuality_regenerated"]
    assert "write {p1.price} instead" in client.calls_to(CHAT)[-1]["system"][0]["text"]


def test_a_marker_for_a_property_no_tool_returned_is_regenerated():
    client = ScriptedBedrock(classification("consulta_precio"), tool_use("search_properties", {"zone": "Prov."}),
                             text("It's {p9.price}."), text("It's {p1.price}."))
    result = run(agent_for(client).run(turn(), FakeCatalog(properties=P1)))
    assert result.text == "It's $2,350,000."
    assert "marker '{p9.price}'" in client.calls_to(CHAT)[-1]["system"][0]["text"]


def test_a_marker_that_cannot_be_filled_is_never_sent_to_the_lead():
    client = ScriptedBedrock(classification("consulta_precio"), text("It's {p9.price}."), text("It's {p9.price}."))
    result = run(agent_for(client).run(turn(), FakeCatalog()))
    assert result.text == FACTUALITY_DEGRADE_REPLY and "{" not in result.text


def test_remembered_facts_fill_the_markers_but_never_the_status():
    remembered = {"p1": {"price": 2350000}}
    client = ScriptedBedrock(classification("consulta_precio"), text("It's {p1.price}."))
    message = TurnInput(event=EVENT, text="How much was it?", remembered=remembered)
    assert run(agent_for(client).run(message, FakeCatalog())).text == "It's $2,350,000."
    # El estado de la memoria no vale (A-4): el marcador no se puede completar.
    client = ScriptedBedrock(classification("consulta_precio"), text("It's {p1.status}."), text("It's {p1.status}."))
    assert run(agent_for(client).run(message, FakeCatalog())).text == FACTUALITY_DEGRADE_REPLY


def test_the_leads_own_numbers_from_earlier_messages_can_be_repeated():
    history = [("user", "My budget is $200,000"), ("assistant", "Got it.")]
    client = ScriptedBedrock(classification("otro"), text("With your $200,000 budget, I can help you search."))
    result = run(agent_for(client).run(turn("What can I get?", history=history), FakeCatalog()))
    assert result.text.startswith("With your $200,000 budget") and result.guard_audits == []


def test_a_listing_reference_the_lead_came_from_can_be_named():
    client = ScriptedBedrock(classification("consulta_precio"), text("Do you mean prop_00231?"))
    message = TurnInput(event=EVENT, text="Is it still available?", listing=LISTING)
    assert run(agent_for(client).run(message, FakeCatalog())).text == "Do you mean prop_00231?"


def test_a_reference_no_tool_returned_is_regenerated():
    client = ScriptedBedrock(classification("consulta_precio"), text("Look at prop_99999."), text("Which one?"))
    result = run(agent_for(client).run(turn(), FakeCatalog()))
    assert result.text == "Which one?"
    assert "reference 'prop_99999'" in client.calls_to(CHAT)[-1]["system"][0]["text"]


# --- DT-60: el clasificador contesta si pide una persona -------------------------------------------------------

def test_the_classifiers_answer_decides_a_handoff_over_the_rule():
    """La regla de #228 no lo reconoce como pedido de una persona, pero el clasificador sí."""
    client = ScriptedBedrock(classification("solicitud_humano", asks_for_person=True))
    result = run(agent_for(client).run(turn("Can I speak with the office directly?"), FakeCatalog()))
    assert result.intent == "solicitud_humano" and result.flags == {"asks_for_person": True}


def test_the_classifier_saying_no_one_was_asked_for_stops_the_handoff_even_if_the_rule_says_so():
    """"agent" es una palabra de la regla de #228, pero la pregunta es por la comisión."""
    client = ScriptedBedrock(classification("solicitud_humano", asks_for_person=False), text("Sure, which area?"))
    result = run(agent_for(client).run(turn("Is the agent fee included?"), FakeCatalog()))
    assert result.intent == "otro" and result.guard_audits == [(HUMAN_REQUEST_UNCONFIRMED, {})]


def test_the_classifiers_answers_reach_the_turn():
    client = ScriptedBedrock(classification("busqueda_propiedad", wants_alert=True, wants_visit=False),
                             text("Sure."))
    result = run(agent_for(client).run(turn("Let me know when one comes up"), FakeCatalog()))
    assert result.flags == {"wants_alert": True, "wants_visit": False}
