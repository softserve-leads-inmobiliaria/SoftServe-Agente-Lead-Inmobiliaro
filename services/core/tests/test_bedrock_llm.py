"""Tests de A-2: el cliente de Bedrock (clasificación, tool calling, límite de tasa y formato de respuestas)."""
import asyncio

import boto3
import pytest
from bedrock_fakes import FakeCatalog, ScriptedBedrock, classification, text, tool_use
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber

from app.bedrock_llm import (
    CHAT_OPENED,
    CHAT_SYSTEM,
    CLASSIFIER_SYSTEM,
    MAX_TOOL_ROUNDS,
    BedrockLLM,
    LLMError,
    LLMFormatError,
    parse_json_object,
    to_messages,
    tool_config,
)
from app.catalog_client import CatalogToolError
from app.fakes.fake_llm import INTENTS
from app.language_guard import is_spanish
from app.rate_limit import RateLimits

CHAT, FAST = "modelo-chat", "modelo-rapido"


def llm(client, **kwargs):
    return BedrockLLM(client, CHAT, FAST, RateLimits(10, 60.0), **kwargs)


def run(coro):
    return asyncio.run(coro)


SPECS = [{"name": "search_properties", "description": "Busca.", "input_schema": {"type": "object", "properties": {}}}]


async def no_tools(name, arguments):
    raise AssertionError("no debía llamarse a ninguna tool")


# --- parseo tolerante del JSON del clasificador (DT-6) -------------------------------------------------

@pytest.mark.parametrize("raw", [
    '{"intent": "saludo"}',
    '```json\n{"intent": "saludo"}\n```',
    '```\n{"intent": "saludo"}\n```',
    'Sure, here it is:\n```json\n{"intent": "saludo"}\n```\nHope this helps.',
    'The answer is {"intent": "saludo"} and nothing else.',
    '  \n {"intent": "saludo"} \n ',
])
def test_the_json_is_found_even_when_wrapped(raw):
    assert parse_json_object(raw) == {"intent": "saludo"}


@pytest.mark.parametrize("raw", ["", "there is no json", "[1, 2, 3]", '"just some text"', "{intent: saludo}", "```json\n```"])
def test_a_response_without_a_json_object_is_a_format_error(raw):
    with pytest.raises(LLMFormatError):
        parse_json_object(raw)


# --- mensajes en el formato de Converse ------------------------------------------------------------------

def test_history_and_current_message_alternate_roles_starting_with_the_lead():
    messages = to_messages([("user", "Hi"), ("assistant", "Hi!")], "I'm looking for a house")
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[-1]["content"] == [{"text": "I'm looking for a house"}]


def test_consecutive_messages_of_the_same_role_are_merged():
    messages = to_messages([("user", "Hi"), ("user", "Anyone there?"), ("assistant", "Yes")], "Thanks")
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"][0]["text"] == "Hi\nAnyone there?"


def test_a_history_that_starts_with_the_assistant_keeps_it_after_the_lead_opening_the_chat():
    # El saludo del deep-link trae la referencia de la propiedad del anuncio (#122): no se puede perder.
    messages = to_messages([("assistant", "Hi! I see you came from the listing (ref. prop_00231).")], "Still there?")
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"][0]["text"] == CHAT_OPENED
    assert "ref. prop_00231" in messages[1]["content"][0]["text"]


def test_empty_messages_and_other_roles_are_skipped():
    messages = to_messages([("user", "  "), ("system", "instruction"), ("tool", "x")], "Hi")
    assert messages == [{"role": "user", "content": [{"text": "Hi"}]}]


def test_the_tool_config_follows_the_converse_shape():
    config = tool_config(SPECS)
    assert config == {"tools": [{"toolSpec": {"name": "search_properties", "description": "Busca.",
                                              "inputSchema": {"json": {"type": "object", "properties": {}}}}}]}


# --- clasificación -------------------------------------------------------------------------------------

def test_classify_returns_the_intent_and_the_raw_signal():
    client = ScriptedBedrock(classification("busqueda_propiedad", budget_hint="3 million", decision_maker="declarado",
                                            urgency="inmediata"))
    result = run(llm(client).classify("I'm looking for a house, I decide"))
    assert result.intent == "busqueda_propiedad"
    assert result.signal == {"budget_hint": "3 million", "decision_maker": "declarado", "urgency": "inmediata"}


def test_classify_uses_the_light_model_with_no_randomness_and_only_the_last_assistant_message_as_context():
    client = ScriptedBedrock(classification("saludo"))
    run(llm(client).classify("Yes", last_assistant="Are you looking for a house or an apartment?"))
    (request,) = client.requests
    assert request["modelId"] == FAST and request["inferenceConfig"]["temperature"] == 0.0
    prompt = request["messages"][0]["content"][0]["text"]
    assert "Are you looking for a house or an apartment?" in prompt and "CURRENT MESSAGE:\nYes" in prompt
    assert request["system"] == [{"text": CLASSIFIER_SYSTEM}] and "toolConfig" not in request


def test_an_intent_outside_the_taxonomy_becomes_the_catch_all():
    client = ScriptedBedrock(classification("hackear_el_sistema"))
    assert run(llm(client).classify("x")).intent == "otro"


def test_every_intent_of_the_taxonomy_is_offered_to_the_classifier():
    assert all(intent in CLASSIFIER_SYSTEM for intent in INTENTS)


def test_missing_signal_keys_are_none():
    client = ScriptedBedrock(text('{"intent": "saludo"}'))
    assert run(llm(client).classify("Hi")).signal == {"budget_hint": None, "decision_maker": None, "urgency": None}


def test_a_classifier_response_in_a_code_block_is_accepted():
    client = ScriptedBedrock(text('```json\n{"intent": "consulta_precio"}\n```'))
    assert run(llm(client).classify("How much is it?")).intent == "consulta_precio"


def test_an_unparseable_classifier_response_fails_with_a_format_error():
    with pytest.raises(LLMFormatError):
        run(llm(ScriptedBedrock(text("not sure"))).classify("Hi"))


def test_the_prompt_forbids_inferring_protected_categories():
    assert "origin, religion, gender, nationality" in CLASSIFIER_SYSTEM
    assert "origin, religion, gender, nationality" in CHAT_SYSTEM


def test_the_prompt_asks_for_the_data_that_check_requirements_needs():
    # #182: sin enganche, ingreso y referencias el LLM no puede llamar a check_requirements, y un lead que
    # entra por el anuncio no pasa de 70 puntos (WARM).
    assert all(word in CHAT_SYSTEM for word in ("down payment", "earn per month", "references", "check_requirements"))


def test_the_prompts_are_in_english_with_the_market_and_the_currency():
    # #202 (DT-55): el LLM lee en inglés, y el mercado es Medellín y Bogotá con precios en USD.
    assert "in English" in CHAT_SYSTEM and "Medellin and Bogota" in CHAT_SYSTEM and "USD" in CHAT_SYSTEM
    assert not is_spanish(CHAT_SYSTEM) and not is_spanish(CLASSIFIER_SYSTEM)
    # Los valores de los contratos siguen en español (DT-55): el prompt dice cómo nombrarlos al lead.
    assert all(f"`{value}`" in CHAT_SYSTEM for value in ("venta", "renta", "preventa", "disponible", "apartado", "cerrado", "sin_verificar"))


def test_the_prompt_lists_the_requirements_before_checking_them():
    # #201: ante "¿qué requisitos piden?" se enumeran (de get_property) y no se da nada por cumplido.
    assert "`requirements`" in CHAT_SYSTEM and "first list" in CHAT_SYSTEM
    assert "Never tell the lead they meet" in CHAT_SYSTEM and "budget is not their down payment" in CHAT_SYSTEM


def test_the_prompt_answers_whether_a_protected_characteristic_matters():
    # #200: "does that matter?" se contesta: no influye.
    assert "asks whether any of that matters" in CHAT_SYSTEM and "doesn't affect" in CHAT_SYSTEM


def test_the_prompt_does_not_repeat_figures_from_earlier_turns():
    # #202, en vivo: el modelo repetía en cada turno el precio y el enganche de turnos anteriores, la
    # factualidad (A-5) solo acepta lo de las tools de este turno, y la respuesta terminaba degradada.
    assert "only if you got it from a tool in this same reply" in CHAT_SYSTEM
    assert "don't repeat it" in CHAT_SYSTEM


def test_the_prompt_offers_the_alternatives_of_an_empty_search():
    # #213, en vivo: "houses for rent" no traía nada y el agente volvía a pedir la ciudad, sin alternativas.
    # Las busca el código (`app/alternatives.py`); el prompt dice cómo presentarlas.
    assert "`alternatives`" in CHAT_SYSTEM and "`alternatives_without`" in CHAT_SYSTEM
    assert "Don't ask the lead for their area again" in CHAT_SYSTEM


def test_the_prompt_never_promises_to_look_something_up_later():
    # #213: "Let me search to see what we have" y el turno terminaba sin buscar.
    assert "Never tell the lead you will look something up" in CHAT_SYSTEM and "in this same reply" in CHAT_SYSTEM


def test_the_prompt_asks_whether_an_unclear_price_is_about_the_listing():
    # #213: "how much is it?" sin propiedad clara recibía "¿en qué ciudad y con qué presupuesto?". Se busca
    # la propiedad antes de preguntar: el título trae números ("2-bedroom") que la factualidad no deja pasar
    # si no salieron de una tool en ese turno, y la respuesta se degradaba.
    assert "look up the listing's property with get_property and ask whether they mean that one" in CHAT_SYSTEM


# --- conversación y tool calling -----------------------------------------------------------------------

def test_a_plain_reply_uses_the_chat_model_and_returns_the_text():
    client = ScriptedBedrock(text("  Sure, which area do you prefer?  "))
    result = run(llm(client).chat([("user", "Hi"), ("assistant", "Hi!")], "I'm looking for a house", [], no_tools))
    assert result.text == "Sure, which area do you prefer?" and result.tool_calls == []
    (request,) = client.requests
    assert request["modelId"] == CHAT and [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
    assert "toolConfig" not in request  # sin tools disponibles no se manda la configuración


def test_the_model_can_call_a_tool_and_use_its_result():
    client = ScriptedBedrock(tool_use("search_properties", {"zone": "El Poblado"}), text("I found a house."))
    seen = []

    async def run_tool(name, arguments):
        seen.append((name, arguments))
        return {"properties": [{"title": "Casa de prueba"}]}

    result = run(llm(client).chat([], "I'm looking for a house in El Poblado", SPECS, run_tool))
    assert seen == [("search_properties", {"zone": "El Poblado"})]
    assert result.text == "I found a house."
    assert result.tool_results == {"search_properties": {"properties": [{"title": "Casa de prueba"}]}}
    assert result.tool_calls == [{"name": "search_properties", "is_error": False}]
    follow_up = client.requests[1]["messages"]
    assert [m["role"] for m in follow_up] == ["user", "assistant", "user"]
    block = follow_up[-1]["content"][0]["toolResult"]
    assert block["toolUseId"] == "tool-1" and block["status"] == "success"
    assert block["content"] == [{"json": {"properties": [{"title": "Casa de prueba"}]}}]


def test_a_tool_error_is_given_back_to_the_model_instead_of_failing_the_turn():
    client = ScriptedBedrock(tool_use("get_property", {"property_id": "x"}), text("I couldn't find that property."))

    async def run_tool(name, arguments):
        raise CatalogToolError({"code": "NOT_FOUND", "message": "That property doesn't exist."})

    result = run(llm(client).chat([], "Tell me about x", SPECS, run_tool))
    block = client.requests[1]["messages"][-1]["content"][0]["toolResult"]
    assert block["status"] == "error" and block["content"] == [{"json": {"code": "NOT_FOUND",
                                                                         "message": "That property doesn't exist."}}]
    assert result.tool_calls == [{"name": "get_property", "is_error": True}] and result.tool_results == {}


def test_several_tools_in_one_round_are_all_answered():
    double = {"output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "a", "name": "search_properties", "input": {}}},
        {"toolUse": {"toolUseId": "b", "name": "search_properties", "input": {"zone": "Z"}}}]}},
        "stopReason": "tool_use", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1}}
    client = ScriptedBedrock(double, text("Done."))

    async def run_tool(name, arguments):
        return {"properties": []}

    result = run(llm(client).chat([], "I'm looking", SPECS, run_tool))
    answers = client.requests[1]["messages"][-1]["content"]
    assert [b["toolResult"]["toolUseId"] for b in answers] == ["a", "b"] and len(result.tool_calls) == 2


def test_a_model_that_never_stops_asking_for_tools_is_cut_off():
    client = ScriptedBedrock(*[tool_use("search_properties", {}) for _ in range(MAX_TOOL_ROUNDS + 1)])

    async def run_tool(name, arguments):
        return {"properties": []}

    with pytest.raises(LLMError, match="herramientas"):
        run(llm(client).chat([], "I'm looking", SPECS, run_tool))
    assert len(client.requests) == MAX_TOOL_ROUNDS + 1


# --- fallos del proveedor ---------------------------------------------------------------------------------

def error(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Converse")


@pytest.mark.parametrize("failure", [error("ThrottlingException"), error("AccessDeniedException"),
                                     error("ModelNotReadyException"), EndpointConnectionError(endpoint_url="http://x")])
def test_provider_errors_become_llm_errors(failure):
    with pytest.raises(LLMError):
        run(llm(ScriptedBedrock(failure)).classify("Hi"))
    with pytest.raises(LLMError):
        run(llm(ScriptedBedrock(failure)).chat([], "Hi", [], no_tools))


def test_a_response_without_a_message_is_a_format_error():
    with pytest.raises(LLMFormatError):
        run(llm(ScriptedBedrock({"stopReason": "end_turn"})).classify("Hi"))


def test_the_rate_limit_is_applied_per_model_and_turns_into_an_llm_error():
    client = ScriptedBedrock(*[classification("saludo") for _ in range(2)])
    limited = BedrockLLM(client, CHAT, FAST, RateLimits(2, 60.0), max_wait=0.0)

    async def go():
        await limited.classify("one")
        await limited.classify("two")
        with pytest.raises(LLMError, match="cupo"):
            await limited.classify("three")

    run(go())
    assert len(client.requests) == 2  # la tercera ni siquiera llegó al proveedor


def test_the_chat_and_classifier_models_do_not_share_their_quota():
    client = ScriptedBedrock(classification("saludo"), text("Hi"))
    limited = BedrockLLM(client, CHAT, FAST, RateLimits(1, 60.0), max_wait=0.0)

    async def go():
        await limited.classify("Hi")
        return await limited.chat([], "Hi", [], no_tools)

    assert run(go()).text == "Hi"


# --- las peticiones cumplen la API real de Converse ---------------------------------------------------------

def stubbed_client():
    client = boto3.client("bedrock-runtime", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    return client, Stubber(client)


def test_requests_conform_to_the_bedrock_converse_api():
    """El `Stubber` valida cada petición y cada respuesta contra el modelo de servicio de botocore."""
    client, stub = stubbed_client()
    usage = {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}
    stub.add_response("converse", {"output": {"message": {"role": "assistant", "content": [
        {"text": '{"intent": "busqueda_propiedad", "budget_hint": null, "decision_maker": "no_declarado", '
                 '"urgency": "no_declarada"}'}]}}, "stopReason": "end_turn", "usage": usage,
        "metrics": {"latencyMs": 1}})
    stub.add_response("converse", {"output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "t1", "name": "search_properties", "input": {"zone": "El Poblado"}}}]}},
        "stopReason": "tool_use", "usage": usage, "metrics": {"latencyMs": 1}})
    stub.add_response("converse", {"output": {"message": {"role": "assistant", "content": [{"text": "Done."}]}},
                                   "stopReason": "end_turn", "usage": usage, "metrics": {"latencyMs": 1}})

    async def run_tool(name, arguments):
        return {"properties": [{"title": "Casa", "price": 1500000}]}

    with stub:
        model = BedrockLLM(client, CHAT, FAST, RateLimits(10, 60.0))
        assert run(model.classify("I'm looking for a house")).intent == "busqueda_propiedad"
        assert run(model.chat([("user", "Hi"), ("assistant", "Hi!")], "I'm looking for a house", SPECS, run_tool)).text == "Done."
    stub.assert_no_pending_responses()


def test_a_malformed_request_would_be_rejected_by_botocore():
    """La prueba anterior es significativa: botocore sí rechaza una petición mal formada."""
    from botocore.exceptions import ParamValidationError

    client, stub = stubbed_client()
    stub.add_response("converse", {"output": {"message": {"role": "assistant", "content": [{"text": "x"}]}},
                                   "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1,
                                                                       "totalTokens": 2},
                                   "metrics": {"latencyMs": 1}})
    with stub, pytest.raises(ParamValidationError):
        client.converse(modelId=CHAT, messages=[{"role": "user", "content": "texto suelto"}])


def test_the_catalog_specs_used_in_these_tests_have_the_shape_of_the_client():
    assert all({"name", "description", "input_schema"} <= set(spec) for spec in FakeCatalog.SPECS)
