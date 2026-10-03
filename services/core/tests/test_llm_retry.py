"""Tests de A-14: reintentos con espera exponencial ante los límites transitorios de Bedrock.

El cliente es el de `boto3` con `botocore.stub.Stubber` (valida cada petición y respuesta contra el
modelo de servicio real); los fallos de conexión, que el `Stubber` no puede simular, usan un cliente
guiado. El tiempo lo controla un reloj falso: ningún test espera de verdad.
"""
import asyncio
import json
import logging

import boto3
import pytest
from bedrock_fakes import FakeCatalog, ScriptedBedrock, classification, text
from botocore.exceptions import ClientError as BotoClientError
from botocore.exceptions import (
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
    SSLError,
)
from botocore.stub import Stubber
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import tracing
from app.agent import LLM_FALLBACK_REPLY, BedrockAgent, TurnInput
from app.bedrock_llm import BedrockLLM, LLMError
from app.llm_config import ConfigError, build_agent
from app.llm_retry import Retrier, RetryPolicy, retry_after
from app.rate_limit import RateLimits
from db.repo_root import repo_root

CHAT, FAST = "modelo-chat", "modelo-rapido"
EVENT = json.loads((repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))
SIGNAL = '{"intent": "saludo", "budget_hint": null, "decision_maker": "no_declarado", "urgency": "no_declarada"}'
LEAD_TEXT = "Mi presupuesto secreto es 7 millones y mi tarjeta es 4111"

TRANSIENT = [("ThrottlingException", 429), ("ServiceUnavailableException", 503),
             ("ModelTimeoutException", 408), ("InternalServerException", 500)]
NOT_TRANSIENT = [("AccessDeniedException", 403), ("ValidationException", 400), ("ResourceNotFoundException", 404)]


class FakeClock:
    """Reloj que solo avanza cuando alguien duerme: registra cada espera y no espera nada."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def top(low: float, high: float) -> float:
    """Sin azar: siempre el extremo alto del *jitter*, o sea la espera exponencial completa."""
    return high


def bottom(low: float, high: float) -> float:
    return low


def make(client, clock, policy=None, *, uniform=top, max_wait=15.0, per_minute=10):
    retrier = Retrier(policy or RetryPolicy(), clock=clock.time, sleep=clock.sleep, uniform=uniform)
    limits = RateLimits(per_minute, 60.0, clock=clock.time, sleep=clock.sleep)
    return BedrockLLM(client, CHAT, FAST, limits, max_wait=max_wait, retrier=retrier)


def run(coro):
    return asyncio.run(coro)


# --- cliente de boto3 con Stubber ----------------------------------------------------------------------------

def stubbed():
    client = boto3.client("bedrock-runtime", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    stub = Stubber(client)
    stub.activate()
    return client, stub


def fail(stub, code, status=429, retry_after_header=None):
    meta = {"HTTPHeaders": {"retry-after": str(retry_after_header)}} if retry_after_header is not None else None
    stub.add_client_error("converse", service_error_code=code, service_message="x", http_status_code=status,
                          response_meta=meta)


def succeed(stub, body=SIGNAL):
    stub.add_response("converse", {
        "output": {"message": {"role": "assistant", "content": [{"text": body}]}}, "stopReason": "end_turn",
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}})


def throttle():
    return BotoClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "Converse")


def connection_errors():
    return [EndpointConnectionError(endpoint_url="http://x"), ConnectTimeoutError(endpoint_url="http://x"),
            ConnectionClosedError(endpoint_url="http://x"), ReadTimeoutError(endpoint_url="http://x")]


# --- éxito tras N reintentos ---------------------------------------------------------------------------------

def test_a_throttled_call_succeeds_after_two_retries_with_exponential_waits():
    client, stub = stubbed()
    fail(stub, "ThrottlingException")
    fail(stub, "ThrottlingException")
    succeed(stub)
    clock = FakeClock()

    result = run(make(client, clock).classify("Hola"))

    assert result.intent == "saludo"
    assert clock.sleeps == [0.5, 1.0]  # base * 2^(intento-1)
    stub.assert_no_pending_responses()


def test_the_wait_doubles_until_the_cap_and_stays_there():
    client, stub = stubbed()
    for _ in range(5):
        fail(stub, "ThrottlingException")
    succeed(stub)
    clock = FakeClock()

    run(make(client, clock, RetryPolicy(max_attempts=6, base_delay=0.5, max_delay=2.0,
                                        max_total_seconds=100)).classify("Hola"))

    assert clock.sleeps == [0.5, 1.0, 2.0, 2.0, 2.0]


@pytest.mark.parametrize("code,status", TRANSIENT)
def test_every_transient_bedrock_error_is_retried(code, status):
    client, stub = stubbed()
    fail(stub, code, status)
    succeed(stub)
    clock = FakeClock()

    assert run(make(client, clock).classify("Hola")).intent == "saludo"
    assert len(clock.sleeps) == 1
    stub.assert_no_pending_responses()


@pytest.mark.parametrize("failure", connection_errors(), ids=lambda e: type(e).__name__)
def test_connection_errors_are_retried(failure):
    client = ScriptedBedrock(failure, text(SIGNAL))
    clock = FakeClock()

    assert run(make(client, clock).classify("Hola")).intent == "saludo"
    assert len(clock.sleeps) == 1 and len(client.requests) == 2


def test_the_jitter_keeps_each_wait_between_half_and_all_of_the_exponential_value():
    for uniform, expected in ((bottom, [0.25, 0.5]), (top, [0.5, 1.0])):
        client, stub = stubbed()
        fail(stub, "ThrottlingException")
        fail(stub, "ThrottlingException")
        succeed(stub)
        clock = FakeClock()
        run(make(client, clock, uniform=uniform).classify("Hola"))
        assert clock.sleeps == expected


def test_with_the_real_random_source_the_waits_stay_inside_the_bounds():
    for _ in range(20):
        client, stub = stubbed()
        fail(stub, "ThrottlingException")
        fail(stub, "ThrottlingException")
        succeed(stub)
        clock = FakeClock()
        limits = RateLimits(10, 60.0, clock=clock.time, sleep=clock.sleep)
        llm = BedrockLLM(client, CHAT, FAST, limits, retrier=Retrier(RetryPolicy(), clock=clock.time,
                                                                     sleep=clock.sleep))
        run(llm.classify("Hola"))
        assert 0.25 <= clock.sleeps[0] <= 0.5 and 0.5 <= clock.sleeps[1] <= 1.0


# --- agotamiento ---------------------------------------------------------------------------------------------

def test_after_the_last_attempt_it_fails_and_does_not_wait_again():
    client, stub = stubbed()
    for _ in range(3):
        fail(stub, "ThrottlingException")
    clock = FakeClock()

    with pytest.raises(LLMError):
        run(make(client, clock, RetryPolicy(max_attempts=3)).classify("Hola"))

    assert clock.sleeps == [0.5, 1.0]  # 3 intentos = 2 esperas
    stub.assert_no_pending_responses()


def test_without_a_retrier_nothing_is_retried():
    client = ScriptedBedrock(BotoClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "Converse"))

    with pytest.raises(LLMError):
        run(BedrockLLM(client, CHAT, FAST, RateLimits(10, 60.0)).classify("Hola"))

    assert len(client.requests) == 1


# --- errores no transitorios ---------------------------------------------------------------------------------

@pytest.mark.parametrize("code,status", NOT_TRANSIENT)
def test_a_non_transient_error_fails_at_the_first_attempt(code, status):
    client, stub = stubbed()
    fail(stub, code, status)
    clock = FakeClock()

    with pytest.raises(LLMError):
        run(make(client, clock).classify("Hola"))

    assert clock.sleeps == []
    stub.assert_no_pending_responses()


@pytest.mark.parametrize("failure", [NoCredentialsError(), SSLError(endpoint_url="http://x", error="x")],
                         ids=lambda e: type(e).__name__)
def test_credential_and_tls_errors_are_not_retried(failure):
    client = ScriptedBedrock(failure)
    clock = FakeClock()

    with pytest.raises(LLMError):
        run(make(client, clock).classify("Hola"))

    assert clock.sleeps == [] and len(client.requests) == 1


# --- el tiempo que pide el proveedor -------------------------------------------------------------------------

def test_the_time_bedrock_asks_for_replaces_the_calculated_wait():
    client, stub = stubbed()
    fail(stub, "ThrottlingException", retry_after_header=7)
    succeed(stub)
    clock = FakeClock()

    run(make(client, clock).classify("Hola"))

    assert clock.sleeps == [7.0]  # no 0.5


def test_the_header_name_is_case_insensitive_and_a_bad_value_falls_back_to_the_calculation():
    def error_with(headers):
        return BotoClientError({"Error": {"Code": "ThrottlingException"},
                                "ResponseMetadata": {"HTTPHeaders": headers}}, "Converse")

    assert retry_after(error_with({"Retry-After": "3"})) == 3.0
    assert retry_after(error_with({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
    assert retry_after(error_with({"retry-after": "-5"})) is None
    assert retry_after(error_with({})) is None
    assert retry_after(EndpointConnectionError(endpoint_url="http://x")) is None


def test_a_wait_longer_than_the_turn_can_afford_gives_up_without_sleeping():
    client, stub = stubbed()
    fail(stub, "ThrottlingException", retry_after_header=30)  # el tope total es 20 s
    clock = FakeClock()

    with pytest.raises(LLMError):
        run(make(client, clock).classify("Hola"))

    assert clock.sleeps == []


# --- tope de tiempo total ------------------------------------------------------------------------------------

def test_the_total_time_cap_stops_the_retries_before_the_attempts_run_out():
    client, stub = stubbed()
    for _ in range(4):
        fail(stub, "ThrottlingException")
    clock = FakeClock()
    policy = RetryPolicy(max_attempts=10, base_delay=0.5, max_delay=8.0, max_total_seconds=5.0)

    with pytest.raises(LLMError):
        run(make(client, clock, policy).classify("Hola"))

    # 0.5 + 1 + 2 = 3.5 s esperados; la siguiente espera (4 s) ya no cabe en los 5 s del turno.
    assert clock.sleeps == [0.5, 1.0, 2.0] and clock.now <= 5.0
    stub.assert_no_pending_responses()


def test_the_calls_of_one_turn_share_the_retry_time():
    policy = RetryPolicy(max_attempts=10, max_total_seconds=2.0)

    def scenario(in_turn: bool):
        client, stub = stubbed()
        for _ in range(2):
            fail(stub, "ThrottlingException")
        succeed(stub)  # el clasificador contesta tras 1.5 s de reintentos
        for _ in range(2):
            fail(stub, "ThrottlingException")
        succeed(stub, "Hola, ¿en qué te ayudo?")
        clock = FakeClock()
        llm = make(client, clock, policy)

        async def go():
            await llm.classify("Hola")
            return await llm.chat([], "Hola", [], no_tools)

        def call():
            if not in_turn:
                return run(go())
            with llm.turn():
                return run(go())

        return call, clock

    call, clock = scenario(in_turn=False)
    assert call().text == "Hola, ¿en qué te ayudo?"  # cada llamada con su propio tope: alcanza

    call, clock = scenario(in_turn=True)
    with pytest.raises(LLMError):  # el chat solo tenía 0.5 s de los 2 s del turno
        call()
    assert clock.sleeps == [0.5, 1.0, 0.5]


async def no_tools(name, arguments):
    raise AssertionError("no debía llamarse a ninguna tool")


# --- los reintentos pasan por el limitador -------------------------------------------------------------------

def test_every_retry_counts_against_the_rate_limiter():
    client = ScriptedBedrock(throttle(), throttle(), throttle())
    clock = FakeClock()
    llm = make(client, clock, per_minute=2, max_wait=0.0)  # 2 llamadas por minuto y no se espera cupo

    with pytest.raises(LLMError, match="cupo"):
        run(llm.classify("Hola"))

    assert len(client.requests) == 2  # el tercer intento no consiguió cupo y ni siquiera llegó al proveedor
    assert clock.sleeps == [0.5, 1.0]


def test_a_retry_waits_for_a_free_slot_instead_of_skipping_the_limit():
    client, stub = stubbed()
    fail(stub, "ThrottlingException")
    succeed(stub)
    clock = FakeClock()
    llm = make(client, clock, RetryPolicy(max_total_seconds=100), per_minute=1, max_wait=100.0)

    run(llm.classify("Hola"))

    # 0.5 s de espera exponencial y luego lo que falte para que la ventana de 60 s libere el cupo.
    assert clock.sleeps[0] == 0.5 and sum(clock.sleeps) >= 60.0


def test_a_retry_never_waits_for_a_slot_longer_than_the_turn_has_left():
    client, stub = stubbed()
    fail(stub, "ThrottlingException")
    succeed(stub)
    clock = FakeClock()
    # El cupo tardaría 59.5 s en liberarse; al turno solo le quedan 4.5 s de los 5 s de reintentos.
    llm = make(client, clock, RetryPolicy(max_total_seconds=5.0), per_minute=1, max_wait=100.0)

    with pytest.raises(LLMError, match="cupo"):
        run(llm.classify("Hola"))

    assert clock.sleeps == [0.5] and clock.now <= 5.0


# --- log y traza ---------------------------------------------------------------------------------------------

def test_each_retry_and_the_final_failure_are_logged_as_warnings_without_the_lead_text(caplog):
    client, stub = stubbed()
    for _ in range(3):
        fail(stub, "ThrottlingException")
    clock = FakeClock()

    with caplog.at_level(logging.WARNING, logger="core.bedrock_llm"):
        with pytest.raises(LLMError):
            run(make(client, clock, RetryPolicy(max_attempts=3)).classify(LEAD_TEXT))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3  # 2 reintentos + el fallo definitivo
    assert all("ThrottlingException" in r.getMessage() for r in warnings)
    assert [r.llm_attempt for r in warnings] == [1, 2, 3]
    assert "definitivo" in warnings[-1].getMessage() and warnings[-1].llm_give_up_reason == "attempts_exhausted"
    assert "secreto" not in caplog.text and "4111" not in caplog.text and "7 millones" not in caplog.text


def test_running_out_of_the_local_quota_is_logged_too(caplog):
    """La corrida de OBS-6 con Bedrock: el cupo local (DT-19) se agotaba y el turno caía al texto seguro sin
    dejar nada en el log."""
    client, stub = stubbed()
    fail(stub, "ValidationException", 400)  # la primera llamada gasta el único cupo del minuto
    llm = make(client, FakeClock(), max_wait=0.0, per_minute=1)

    with caplog.at_level(logging.WARNING, logger="core.bedrock_llm"):
        for _ in range(2):
            with pytest.raises(LLMError):
                run(llm.classify(LEAD_TEXT))

    last = [r for r in caplog.records if r.levelno == logging.WARNING][-1]
    assert (last.llm_error_code, last.llm_give_up_reason) == ("LocalRateLimit", "local_rate_limit")
    assert "secreto" not in caplog.text


def test_the_final_failure_is_recorded_in_the_turn_trace():
    exporter = InMemorySpanExporter()
    tracing.configure(exporter, processor=SimpleSpanProcessor, instrument=False)
    try:
        client, stub = stubbed()
        for _ in range(2):
            fail(stub, "ThrottlingException")
        clock = FakeClock()
        llm = make(client, clock, RetryPolicy(max_attempts=2))
        with tracing.get_tracer().start_as_current_span("turn"):
            with pytest.raises(LLMError):
                run(llm.classify("Hola"))
        span = next(s for s in exporter.get_finished_spans() if s.name == "turn")
        assert span.attributes["llm.error_code"] == "ThrottlingException" and span.attributes["llm.attempts"] == 2
        assert [e.name for e in span.events] == ["llm.retry", "llm.failed"]
    finally:
        tracing.reset()


# --- dentro del agente: el turno cae al texto seguro sin colgarse ---------------------------------------------

def agent(client, clock, policy=None):
    return BedrockAgent(make(client, clock, policy))


def turn_input(message="Hola"):
    return TurnInput(event=EVENT, text=message, history=(), lead_qualified=False)


def test_a_turn_that_gets_throttled_twice_still_answers_normally():
    client = ScriptedBedrock(throttle(), throttle(), classification("saludo"), text("¡Hola! ¿En qué te ayudo?"))
    clock = FakeClock()

    result = run(agent(client, clock).run(turn_input(), FakeCatalog()))

    assert result.text == "¡Hola! ¿En qué te ayudo?" and result.llm_error is None
    assert clock.sleeps == [0.5, 1.0]


def test_a_turn_that_exhausts_its_retries_falls_back_to_the_safe_text():
    client = ScriptedBedrock(*[throttle() for _ in range(4)])
    clock = FakeClock()

    result = run(agent(client, clock).run(turn_input(), FakeCatalog()))

    assert (result.text, result.intent, result.llm_error) == (LLM_FALLBACK_REPLY, "otro", "LLMError")
    assert clock.sleeps == [0.5, 1.0, 2.0]  # 4 intentos: el turno no se cuelga


def test_a_turn_that_runs_out_of_time_falls_back_to_the_safe_text():
    client = ScriptedBedrock(*[throttle() for _ in range(3)])
    clock = FakeClock()
    policy = RetryPolicy(max_attempts=10, max_total_seconds=1.0)

    result = run(agent(client, clock, policy).run(turn_input(), FakeCatalog()))

    assert result.text == LLM_FALLBACK_REPLY
    assert clock.sleeps == [0.5] and clock.now <= 1.0  # la segunda espera (1 s) ya no cabe


# --- configuración -------------------------------------------------------------------------------------------

BEDROCK = {"LLM_PROVIDER": "bedrock", "BEDROCK_MODEL_ID_CHAT": "modelo-chat",
           "BEDROCK_MODEL_ID_CLASSIFIER": "modelo-rapido"}


def test_the_documented_defaults_apply_when_the_environment_says_nothing():
    policy = build_agent(BEDROCK, client=ScriptedBedrock())._llm.retry_policy
    assert policy == RetryPolicy(max_attempts=4, base_delay=0.5, max_delay=8.0, max_total_seconds=20.0)


def test_the_retry_limits_are_read_from_the_environment():
    env = BEDROCK | {"LLM_RETRY_MAX_ATTEMPTS": "6", "LLM_RETRY_BASE_DELAY_SECONDS": "1",
                     "LLM_RETRY_MAX_DELAY_SECONDS": "10", "LLM_RETRY_MAX_TOTAL_SECONDS": "30"}
    policy = build_agent(env, client=ScriptedBedrock())._llm.retry_policy
    assert policy == RetryPolicy(max_attempts=6, base_delay=1.0, max_delay=10.0, max_total_seconds=30.0)


def test_one_attempt_turns_the_retries_off():
    policy = build_agent(BEDROCK | {"LLM_RETRY_MAX_ATTEMPTS": "1"}, client=ScriptedBedrock())._llm.retry_policy
    assert policy.max_attempts == 1


@pytest.mark.parametrize("bad", [
    {"LLM_RETRY_MAX_ATTEMPTS": "0"}, {"LLM_RETRY_MAX_ATTEMPTS": "muchos"}, {"LLM_RETRY_MAX_ATTEMPTS": "2.5"},
    {"LLM_RETRY_BASE_DELAY_SECONDS": "0"}, {"LLM_RETRY_BASE_DELAY_SECONDS": "-1"},
    {"LLM_RETRY_BASE_DELAY_SECONDS": "rapido"},
    {"LLM_RETRY_BASE_DELAY_SECONDS": "5", "LLM_RETRY_MAX_DELAY_SECONDS": "1"},  # el tope no puede ser menor que la base
    {"LLM_RETRY_MAX_TOTAL_SECONDS": "-1"}, {"LLM_RETRY_MAX_TOTAL_SECONDS": "mucho"},
])
def test_an_invalid_retry_limit_fails_at_startup(bad):
    with pytest.raises(ConfigError, match="reintentos"):
        build_agent(BEDROCK | bad, client=ScriptedBedrock())


def test_the_fake_provider_ignores_the_retry_settings():
    build_agent({"LLM_PROVIDER": "fake", "LLM_RETRY_MAX_ATTEMPTS": "0"})  # no hay Bedrock: no se valida
