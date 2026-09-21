"""Tests de A-2: el orquestador. Qué ve el LLM, qué decide el código y qué pasa cuando el LLM falla."""
import asyncio
import json

import pytest
from bedrock_fakes import FakeCatalog, ScriptedBedrock, classification, text, tool_use
from botocore.exceptions import ClientError

from app.agent import LLM_FALLBACK_REPLY, BedrockAgent, FakeAgent, TurnInput
from app.bedrock_llm import BedrockLLM
from app.fakes.fake_llm import REPLIES
from app.input_guard import OMITTED_MESSAGE
from app.rate_limit import RateLimits
from db.repo_root import repo_root

EVENT = json.loads((repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))
CHAT, FAST = "modelo-chat", "modelo-rapido"


def agent_for(client):
    return BedrockAgent(BedrockLLM(client, CHAT, FAST, RateLimits(10, 60.0)))


def turn(message="Busco casa en Providencia", history=(), lead_qualified=False):
    return TurnInput(event=EVENT, text=message, history=tuple(history), lead_qualified=lead_qualified)


def run(coro):
    return asyncio.run(coro)


def throttled():
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "Converse")


# --- un turno normal -------------------------------------------------------------------------------------

def test_a_normal_turn_classifies_calls_the_catalog_and_answers():
    client = ScriptedBedrock(classification("busqueda_propiedad", budget_hint="3 millones"),
                             tool_use("search_properties", {"zone": "Providencia"}),
                             text("Encontré una casa en Providencia."))
    catalog = FakeCatalog()
    result = run(agent_for(client).run(turn(), catalog))
    assert (result.text, result.intent) == ("Encontré una casa en Providencia.", "busqueda_propiedad")
    assert result.raw_signal["budget_hint"] == "3 millones"
    assert result.tool_results == {"search_properties": {"properties": [{"id": "p1", "title": "Casa de prueba"}]}}
    assert result.tool_calls == [{"name": "search_properties", "is_error": False}] and result.llm_error is None
    assert catalog.calls == [("search_properties", {"zone": "Providencia"})]


def test_the_classification_and_the_reply_are_two_separate_calls_to_two_models():
    client = ScriptedBedrock(classification("saludo"), text("¡Hola!"))
    run(agent_for(client).run(turn("Hola"), FakeCatalog()))
    assert [r["modelId"] for r in client.requests] == [FAST, CHAT]


def test_the_model_sees_the_history_the_service_gives_it():
    history = [("user", "Hola"), ("assistant", "¡Hola! ¿Qué buscas?")]
    client = ScriptedBedrock(classification("busqueda_propiedad"), text("Claro."))
    run(agent_for(client).run(turn("Un departamento", history), FakeCatalog()))
    classify_request, chat_request = client.requests
    assert "¡Hola! ¿Qué buscas?" in classify_request["messages"][0]["content"][0]["text"]
    assert [m["content"][0]["text"] for m in chat_request["messages"]] == ["Hola", "¡Hola! ¿Qué buscas?",
                                                                            "Un departamento"]


def test_the_classifier_only_gets_the_last_assistant_message_never_earlier_lead_messages():
    history = [("user", "mensaje viejo del lead"), ("assistant", "respuesta vieja"), ("user", "otro del lead"),
               ("assistant", "última respuesta")]
    client = ScriptedBedrock(classification("saludo"), text("Hola"))
    run(agent_for(client).run(turn("Sí", history), FakeCatalog()))
    prompt = client.requests[0]["messages"][0]["content"][0]["text"]
    assert "última respuesta" in prompt
    assert "mensaje viejo del lead" not in prompt and "otro del lead" not in prompt
    assert "respuesta vieja" not in prompt


# --- lo que decide el código, no el LLM (A-6) -------------------------------------------------------------

def test_lead_qualified_is_set_by_the_code_even_if_the_model_asks_for_the_address():
    client = ScriptedBedrock(classification("consulta_precio"),
                             tool_use("get_property", {"property_id": "p1", "lead_qualified": True}), text("Listo."))
    catalog = FakeCatalog()
    run(agent_for(client).run(turn("¿Cuánto cuesta?", lead_qualified=False), catalog))
    assert catalog.calls == [("get_property", {"property_id": "p1", "lead_qualified": False})]


def test_a_qualified_lead_is_passed_as_qualified():
    client = ScriptedBedrock(classification("consulta_precio"), tool_use("get_property", {"property_id": "p1"}),
                             text("Listo."))
    catalog = FakeCatalog()
    run(agent_for(client).run(turn("Más datos", lead_qualified=True), catalog))
    assert catalog.calls == [("get_property", {"property_id": "p1", "lead_qualified": True})]


def test_the_model_is_never_offered_the_argument_the_code_controls():
    client = ScriptedBedrock(classification("saludo"), text("Hola"))
    run(agent_for(client).run(turn("Hola"), FakeCatalog()))
    tools = {t["toolSpec"]["name"]: t["toolSpec"]["inputSchema"]["json"]
             for t in client.requests[1]["toolConfig"]["tools"]}
    assert "lead_qualified" not in tools["get_property"]["properties"]
    assert tools["get_property"]["required"] == ["property_id"]


def test_the_original_specs_are_not_mutated():
    run(agent_for(ScriptedBedrock(classification("saludo"), text("Hola"))).run(turn("Hola"), FakeCatalog()))
    assert "lead_qualified" in FakeCatalog.SPECS[1]["input_schema"]["properties"]


def test_a_tool_the_catalog_does_not_publish_is_answered_as_an_error_to_the_model():
    client = ScriptedBedrock(classification("saludo"), tool_use("borrar_todo", {}), text("No puedo hacer eso."))

    class Strict(FakeCatalog):
        async def call(self, tool, arguments=None):
            from app.catalog_client import UnknownTool

            raise UnknownTool(tool, ["search_properties"])

    result = run(agent_for(client).run(turn("Hola"), Strict()))
    assert result.text == "No puedo hacer eso." and result.tool_calls == [{"name": "borrar_todo", "is_error": True}]


# --- una persona, siempre con el mismo texto -----------------------------------------------------------------

@pytest.mark.parametrize("intent", ["solicitud_humano", "fuera_de_alcance"])
def test_a_handoff_intent_gets_the_fixed_text_and_no_reply_call(intent):
    client = ScriptedBedrock(classification(intent))
    catalog = FakeCatalog()
    result = run(agent_for(client).run(turn("Quiero hablar con alguien"), catalog))
    assert result.text == REPLIES[intent] and result.intent == intent
    assert len(client.requests) == 1 and catalog.calls == []


# --- cuando el LLM falla, no se inventa nada -------------------------------------------------------------------

def test_a_failing_classifier_gives_the_safe_reply_and_counts_as_unresolved():
    result = run(agent_for(ScriptedBedrock(throttled())).run(turn(), FakeCatalog()))
    assert (result.text, result.intent, result.llm_error) == (LLM_FALLBACK_REPLY, "otro", "LLMError")
    assert result.tool_results == {} and result.raw_signal == {}


def test_a_failing_reply_gives_the_safe_reply_and_drops_the_partial_work():
    client = ScriptedBedrock(classification("busqueda_propiedad", budget_hint="3 millones"), throttled())
    result = run(agent_for(client).run(turn(), FakeCatalog()))
    assert result.text == LLM_FALLBACK_REPLY and result.intent == "otro" and result.raw_signal == {}


def test_an_unparseable_classification_gives_the_safe_reply():
    result = run(agent_for(ScriptedBedrock(text("no sé"))).run(turn(), FakeCatalog()))
    assert result.text == LLM_FALLBACK_REPLY and result.llm_error == "LLMFormatError"


def test_an_empty_reply_gives_the_safe_reply():
    client = ScriptedBedrock(classification("saludo"), text("   "))
    assert run(agent_for(client).run(turn("Hola"), FakeCatalog())).text == LLM_FALLBACK_REPLY


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
    result = run(FakeAgent().run(turn("Busco un departamento"), catalog))
    assert result.intent == "busqueda_propiedad" and result.text.endswith("Por ejemplo: Casa de prueba.")
    assert list(result.tool_results) == ["search_properties"]


def test_the_fake_agent_extracts_the_raw_signal_from_the_filtered_text():
    result = run(FakeAgent().run(turn("Decido yo y es urgente"), FakeCatalog()))
    assert result.raw_signal["decision_maker"] == "solo" and result.raw_signal["urgency"] == "inmediata"


def test_the_agents_have_names_for_the_traces():
    assert FakeAgent.name == "fake" and BedrockAgent.name == "bedrock"
