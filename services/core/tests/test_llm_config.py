"""Tests de A-2: la elección del agente según el entorno."""
import boto3
import pytest
from bedrock_fakes import ScriptedBedrock

from app.agent import BedrockAgent, FakeAgent
from app.llm_config import ConfigError, _bedrock_client, build_agent

BEDROCK = {"LLM_PROVIDER": "bedrock", "BEDROCK_MODEL_ID_CHAT": "modelo-chat",
           "BEDROCK_MODEL_ID_CLASSIFIER": "modelo-rapido"}


def test_the_fake_llm_is_the_default():
    assert isinstance(build_agent({}), FakeAgent)


@pytest.mark.parametrize("value", ["fake", "FAKE", " fake ", ""])
def test_the_fake_llm_can_be_chosen_explicitly_or_left_empty(value):
    assert isinstance(build_agent({"LLM_PROVIDER": value}), FakeAgent)


@pytest.mark.parametrize("missing", ["BEDROCK_MODEL_ID_CHAT", "BEDROCK_MODEL_ID_CLASSIFIER"])
def test_bedrock_needs_both_models(missing):
    env = {k: v for k, v in BEDROCK.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        build_agent(env, client=ScriptedBedrock())


def test_empty_model_ids_count_as_missing():
    """El compose pasa las variables vacías cuando no están definidas."""
    with pytest.raises(ConfigError, match="BEDROCK_MODEL_ID_CHAT"):
        build_agent(BEDROCK | {"BEDROCK_MODEL_ID_CHAT": "  "}, client=ScriptedBedrock())


def test_bedrock_builds_an_agent_with_an_injected_client():
    assert isinstance(build_agent(BEDROCK, client=ScriptedBedrock()), BedrockAgent)


def test_an_unknown_provider_fails_at_startup():
    with pytest.raises(ConfigError, match="openai"):
        build_agent({"LLM_PROVIDER": "openai"})


@pytest.mark.parametrize("bad", [{"LLM_RATE_LIMIT_PER_MINUTE": "diez"}, {"LLM_RATE_LIMIT_PER_MINUTE": "0"},
                                 {"LLM_MAX_QUEUE_WAIT_SECONDS": "mucho"}])
def test_an_invalid_rate_limit_fails_at_startup(bad):
    with pytest.raises(ConfigError):
        build_agent(BEDROCK | bad, client=ScriptedBedrock())


def test_the_rate_limit_defaults_to_ten_per_minute():
    agent = build_agent(BEDROCK, client=ScriptedBedrock())
    limiter = agent._llm._limits.for_model("modelo-chat")
    assert (limiter._max_calls, limiter._per_seconds) == (10, 60.0) and agent._llm._max_wait == 15.0


def test_the_rate_limit_can_be_tuned():
    agent = build_agent(BEDROCK | {"LLM_RATE_LIMIT_PER_MINUTE": "3", "LLM_MAX_QUEUE_WAIT_SECONDS": "5"},
                        client=ScriptedBedrock())
    assert agent._llm._limits.for_model("x")._max_calls == 3 and agent._llm._max_wait == 5.0


def test_the_real_client_is_built_for_the_configured_region_with_short_timeouts(monkeypatch):
    seen = {}

    def fake_client(service, **kwargs):
        seen.update(service=service, **kwargs)
        return object()

    monkeypatch.setattr(boto3, "client", fake_client)
    _bedrock_client({"BEDROCK_REGION": "us-west-2"})
    assert seen["service"] == "bedrock-runtime" and seen["region_name"] == "us-west-2"
    assert seen["config"].read_timeout == 20 and seen["config"].connect_timeout == 5


def test_the_default_region_is_used_when_none_is_given(monkeypatch):
    seen = {}
    monkeypatch.setattr(boto3, "client", lambda service, **kw: seen.update(kw) or object())
    _bedrock_client({"BEDROCK_REGION": ""})
    assert seen["region_name"] == "us-east-1"
