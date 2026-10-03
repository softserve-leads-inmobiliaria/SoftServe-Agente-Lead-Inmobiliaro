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
    'Claro, aquí está:\n```json\n{"intent": "saludo"}\n```\nEspero que sirva.',
    'La respuesta es {"intent": "saludo"} y nada más.',
    '  \n {"intent": "saludo"} \n ',
])
def test_the_json_is_found_even_when_wrapped(raw):
    assert parse_json_object(raw) == {"intent": "saludo"}


@pytest.mark.parametrize("raw", ["", "no hay json", "[1, 2, 3]", '"solo un texto"', "{intent: saludo}", "```json\n```"])
def test_a_response_without_a_json_object_is_a_format_error(raw):
    with pytest.raises(LLMFormatError):
        parse_json_object(raw)


# --- mensajes en el formato de Converse ------------------------------------------------------------------

def test_history_and_current_message_alternate_roles_starting_with_the_lead():
    messages = to_messages([("user", "Hola"), ("assistant", "¡Hola!")], "Busco casa")
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[-1]["content"] == [{"text": "Busco casa"}]


def test_consecutive_messages_of_the_same_role_are_merged():
    messages = to_messages([("user", "Hola"), ("user", "¿Hay alguien?"), ("assistant", "Sí")], "Gracias")
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"][0]["text"] == "Hola\n¿Hay alguien?"


def test_a_history_that_starts_with_the_assistant_keeps_it_after_the_lead_opening_the_chat():
    # El saludo del deep-link trae la referencia de la propiedad del anuncio (#122): no se puede perder.
    messages = to_messages([("assistant", "¡Hola! Veo que llegaste por el anuncio (ref. prop_00231).")], "¿Sigue?")
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"][0]["text"] == CHAT_OPENED
    assert "ref. prop_00231" in messages[1]["content"][0]["text"]


def test_empty_messages_and_other_roles_are_skipped():
    messages = to_messages([("user", "  "), ("system", "instrucción"), ("tool", "x")], "Hola")
    assert messages == [{"role": "user", "content": [{"text": "Hola"}]}]


def test_the_tool_config_follows_the_converse_shape():
    config = tool_config(SPECS)
    assert config == {"tools": [{"toolSpec": {"name": "search_properties", "description": "Busca.",
                                              "inputSchema": {"json": {"type": "object", "properties": {}}}}}]}


# --- clasificación -------------------------------------------------------------------------------------

def test_classify_returns_the_intent_and_the_raw_signal():
    client = ScriptedBedrock(classification("busqueda_propiedad", budget_hint="3 millones", decision_maker="declarado",
                                            urgency="inmediata"))
    result = run(llm(client).classify("Busco casa, decido yo"))
    assert result.intent == "busqueda_propiedad"
    assert result.signal == {"budget_hint": "3 millones", "decision_maker": "declarado", "urgency": "inmediata"}


def test_classify_uses_the_light_model_with_no_randomness_and_only_the_last_assistant_message_as_context():
    client = ScriptedBedrock(classification("saludo"))
    run(llm(client).classify("Sí", last_assistant="¿Buscas casa o departamento?"))
    (request,) = client.requests
    assert request["modelId"] == FAST and request["inferenceConfig"]["temperature"] == 0.0
    prompt = request["messages"][0]["content"][0]["text"]
    assert "¿Buscas casa o departamento?" in prompt and "MENSAJE ACTUAL:\nSí" in prompt
    assert request["system"] == [{"text": CLASSIFIER_SYSTEM}] and "toolConfig" not in request


def test_an_intent_outside_the_taxonomy_becomes_the_catch_all():
    client = ScriptedBedrock(classification("hackear_el_sistema"))
    assert run(llm(client).classify("x")).intent == "otro"


def test_every_intent_of_the_taxonomy_is_offered_to_the_classifier():
    assert all(intent in CLASSIFIER_SYSTEM for intent in INTENTS)


def test_missing_signal_keys_are_none():
    client = ScriptedBedrock(text('{"intent": "saludo"}'))
    assert run(llm(client).classify("Hola")).signal == {"budget_hint": None, "decision_maker": None, "urgency": None}


def test_a_classifier_response_in_a_code_block_is_accepted():
    client = ScriptedBedrock(text('```json\n{"intent": "consulta_precio"}\n```'))
    assert run(llm(client).classify("¿Cuánto cuesta?")).intent == "consulta_precio"


def test_an_unparseable_classifier_response_fails_with_a_format_error():
    with pytest.raises(LLMFormatError):
        run(llm(ScriptedBedrock(text("no sé"))).classify("Hola"))


def test_the_prompt_forbids_inferring_protected_categories():
    assert "origen, religión, género, nacionalidad" in CLASSIFIER_SYSTEM
    assert "origen, religión, género, nacionalidad" in CHAT_SYSTEM


# --- conversación y tool calling -----------------------------------------------------------------------

def test_a_plain_reply_uses_the_chat_model_and_returns_the_text():
    client = ScriptedBedrock(text("  Con gusto, ¿qué zona prefieres?  "))
    result = run(llm(client).chat([("user", "Hola"), ("assistant", "¡Hola!")], "Busco casa", [], no_tools))
    assert result.text == "Con gusto, ¿qué zona prefieres?" and result.tool_calls == []
    (request,) = client.requests
    assert request["modelId"] == CHAT and [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
    assert "toolConfig" not in request  # sin tools disponibles no se manda la configuración


def test_the_model_can_call_a_tool_and_use_its_result():
    client = ScriptedBedrock(tool_use("search_properties", {"zone": "Providencia"}), text("Encontré una casa."))
    seen = []

    async def run_tool(name, arguments):
        seen.append((name, arguments))
        return {"properties": [{"title": "Casa de prueba"}]}

    result = run(llm(client).chat([], "Busco casa en Providencia", SPECS, run_tool))
    assert seen == [("search_properties", {"zone": "Providencia"})]
    assert result.text == "Encontré una casa."
    assert result.tool_results == {"search_properties": {"properties": [{"title": "Casa de prueba"}]}}
    assert result.tool_calls == [{"name": "search_properties", "is_error": False}]
    follow_up = client.requests[1]["messages"]
    assert [m["role"] for m in follow_up] == ["user", "assistant", "user"]
    block = follow_up[-1]["content"][0]["toolResult"]
    assert block["toolUseId"] == "tool-1" and block["status"] == "success"
    assert block["content"] == [{"json": {"properties": [{"title": "Casa de prueba"}]}}]


def test_a_tool_error_is_given_back_to_the_model_instead_of_failing_the_turn():
    client = ScriptedBedrock(tool_use("get_property", {"property_id": "x"}), text("No encontré esa propiedad."))

    async def run_tool(name, arguments):
        raise CatalogToolError({"code": "NOT_FOUND", "message": "No existe esa propiedad."})

    result = run(llm(client).chat([], "Cuéntame de la x", SPECS, run_tool))
    block = client.requests[1]["messages"][-1]["content"][0]["toolResult"]
    assert block["status"] == "error" and block["content"] == [{"json": {"code": "NOT_FOUND",
                                                                         "message": "No existe esa propiedad."}}]
    assert result.tool_calls == [{"name": "get_property", "is_error": True}] and result.tool_results == {}


def test_several_tools_in_one_round_are_all_answered():
    double = {"output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "a", "name": "search_properties", "input": {}}},
        {"toolUse": {"toolUseId": "b", "name": "search_properties", "input": {"zone": "Z"}}}]}},
        "stopReason": "tool_use", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        "metrics": {"latencyMs": 1}}
    client = ScriptedBedrock(double, text("Listo."))

    async def run_tool(name, arguments):
        return {"properties": []}

    result = run(llm(client).chat([], "Busco", SPECS, run_tool))
    answers = client.requests[1]["messages"][-1]["content"]
    assert [b["toolResult"]["toolUseId"] for b in answers] == ["a", "b"] and len(result.tool_calls) == 2


def test_a_model_that_never_stops_asking_for_tools_is_cut_off():
    client = ScriptedBedrock(*[tool_use("search_properties", {}) for _ in range(MAX_TOOL_ROUNDS + 1)])

    async def run_tool(name, arguments):
        return {"properties": []}

    with pytest.raises(LLMError, match="herramientas"):
        run(llm(client).chat([], "Busco", SPECS, run_tool))
    assert len(client.requests) == MAX_TOOL_ROUNDS + 1


# --- fallos del proveedor ---------------------------------------------------------------------------------

def error(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Converse")


@pytest.mark.parametrize("failure", [error("ThrottlingException"), error("AccessDeniedException"),
                                     error("ModelNotReadyException"), EndpointConnectionError(endpoint_url="http://x")])
def test_provider_errors_become_llm_errors(failure):
    with pytest.raises(LLMError):
        run(llm(ScriptedBedrock(failure)).classify("Hola"))
    with pytest.raises(LLMError):
        run(llm(ScriptedBedrock(failure)).chat([], "Hola", [], no_tools))


def test_a_response_without_a_message_is_a_format_error():
    with pytest.raises(LLMFormatError):
        run(llm(ScriptedBedrock({"stopReason": "end_turn"})).classify("Hola"))


def test_the_rate_limit_is_applied_per_model_and_turns_into_an_llm_error():
    client = ScriptedBedrock(*[classification("saludo") for _ in range(2)])
    limited = BedrockLLM(client, CHAT, FAST, RateLimits(2, 60.0), max_wait=0.0)

    async def go():
        await limited.classify("uno")
        await limited.classify("dos")
        with pytest.raises(LLMError, match="cupo"):
            await limited.classify("tres")

    run(go())
    assert len(client.requests) == 2  # la tercera ni siquiera llegó al proveedor


def test_the_chat_and_classifier_models_do_not_share_their_quota():
    client = ScriptedBedrock(classification("saludo"), text("Hola"))
    limited = BedrockLLM(client, CHAT, FAST, RateLimits(1, 60.0), max_wait=0.0)

    async def go():
        await limited.classify("Hola")
        return await limited.chat([], "Hola", [], no_tools)

    assert run(go()).text == "Hola"


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
        {"toolUse": {"toolUseId": "t1", "name": "search_properties", "input": {"zone": "Providencia"}}}]}},
        "stopReason": "tool_use", "usage": usage, "metrics": {"latencyMs": 1}})
    stub.add_response("converse", {"output": {"message": {"role": "assistant", "content": [{"text": "Listo."}]}},
                                   "stopReason": "end_turn", "usage": usage, "metrics": {"latencyMs": 1}})

    async def run_tool(name, arguments):
        return {"properties": [{"title": "Casa", "price": 1500000}]}

    with stub:
        model = BedrockLLM(client, CHAT, FAST, RateLimits(10, 60.0))
        assert run(model.classify("Busco casa")).intent == "busqueda_propiedad"
        assert run(model.chat([("user", "Hola"), ("assistant", "¡Hola!")], "Busco casa", SPECS, run_tool)).text == "Listo."
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
