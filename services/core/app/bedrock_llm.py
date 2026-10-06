"""LLM en AWS Bedrock (A-2, DT-6): un modelo liviano clasifica y extrae señal, otro conversa con tools.

Usa la API Converse de Bedrock. El cliente de `boto3` es síncrono, así que las llamadas corren en un
hilo. Cada modelo tiene su propio límite de tasa (`app/rate_limit.py`) y los errores transitorios del
proveedor se reintentan con espera exponencial (`app/llm_retry.py`, A-14). Nada de lo que devuelve el
LLM se toma como cierto sin validar: la intención debe ser una de `INTENTS` y la señal se acota
después en `merge_signal`.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

from botocore.exceptions import BotoCoreError, ClientError
from opentelemetry import trace
from starlette.concurrency import run_in_threadpool

from app.fakes.fake_llm import INTENTS
from app.llm_retry import RETRY, Retrier, RetryBudget, RetryPolicy, error_code
from app.rate_limit import RateLimited, RateLimits

logger = logging.getLogger("core.bedrock_llm")

MAX_TOOL_ROUNDS = 3
MAX_REPLY_TOKENS = 400

# Tiempo de reintentos del turno en curso (A-14): lo abre `BedrockLLM.turn()` y lo comparten todas las
# llamadas del turno. Sin turno abierto, cada llamada tiene el suyo.
_TURN_BUDGET: ContextVar[RetryBudget | None] = ContextVar("llm_turn_retry_budget", default=None)

# Lo que lee el LLM va en inglés (#202, DT-55); los valores de las claves son los del contrato y no cambian.
CLASSIFIER_SYSTEM = (
    "You are a classifier for a real estate agency's assistant. You read a lead's CURRENT MESSAGE and "
    "reply ONLY with a JSON object, with no extra text, with these keys:\n"
    '- "intent": one of ' + ", ".join(INTENTS) + ".\n"
    '  "solicitud_humano" = asks to talk to a person; "fuera_de_alcance" = a complaint or a matter about an '
    'existing contract or account; "agendar_visita" = wants to visit a property; "consulta_precio" = asks '
    'about a price; "busqueda_propiedad" = looking for a property; "saludo" = a greeting; "otro" = none of '
    'the others.\n'
    '- "budget_hint": the budget the lead states in the current message, as free text, or null.\n'
    '- "decision_maker": "declarado" if the current message says how the purchase decision is made (on '
    'their own or with other people), or "no_declarado". Do not distinguish who they decide with.\n'
    '- "urgency": "inmediata", "3_meses", "explorando" or "no_declarada", ONLY if the current message says so.\n'
    "Extract only from the CURRENT MESSAGE. Do not infer anything about the person's origin, religion, "
    "gender, nationality or family situation."
)

CHAT_SYSTEM = (
    "You are the virtual assistant of a real estate agency with properties in Medellin and Bogota, "
    "Colombia. You chat in short messages, in English, in a friendly and straightforward way. Prices are "
    "in US dollars (USD).\n"
    "Rules:\n"
    "- Every property fact (price, area, size, availability, promotions, requirements) must come from a "
    "catalog tool. If you don't have it, look it up; if there is no result, say so. Do not make up facts.\n"
    "- Mention a price, amount or percentage only if you got it from a tool in this same reply. The lead "
    "already has what you told them before: don't repeat it, and if you need it again, look it up again.\n"
    "- The tools use internal values in Spanish (`venta`, `renta`, `preventa`, `disponible`, `apartado`, "
    "`cerrado`, `sin_verificar`, `departamento`, `casa`, `terreno`, `local`, `estudio`). Never show them to "
    "the lead as they are: say them in English (for sale, for rent, pre-sale, available, reserved, off the "
    "market, unverified, apartment, house, land, commercial space, studio).\n"
    "- In a search, `properties` are the only ones you can offer. The ones in `unavailable` are not "
    "available: if the lead asks about one, tell them its status (reserved, off the market or unverified) "
    "and do not offer it.\n"
    "- If the lead came from a property's listing (you will see its reference, `ref. prop_…`, in the "
    "conversation), when they say \"this one\", \"this apartment\" or something similar they mean that "
    "one: look it up with get_property and do not ask them for the reference.\n"
    "- If the lead asks what the requirements are, first list that property's requirements as get_property "
    "returns them in `requirements` (minimum down payment with its percentage and amount, minimum monthly "
    "income, references), and then ask for the figure you need to check them.\n"
    "- To check whether the lead meets the requirements, ask how much they have for the down payment (sale "
    "or pre-sale) or how much they earn per month (rent), and whether they can provide references (rent). "
    "Then check with check_requirements. Never tell the lead they meet or don't meet a requirement without "
    "that tool: their budget is not their down payment.\n"
    "- Do not promise discounts, holds, credit approval or the closing of a deal.\n"
    "- Do not ask about or comment on origin, religion, gender, nationality or family situation. If the "
    "lead asks whether any of that matters, say clearly that it doesn't affect how we help them or the "
    "requirements, and go on with the conversation.\n"
    "- If you can't help with something, offer to put the lead in touch with an agent.\n"
    "- Ask at most one question per message."
)


SUMMARY_SYSTEM = (
    "You summarize, for a real estate agent, the conversation between a lead and the agency's virtual "
    "assistant. Reply ONLY with a JSON object, with no extra text, with these keys, each with one or two "
    "short sentences in English:\n"
    '- "busca": what the lead is looking for (property type, area, budget, timeline), only with what the '
    "conversation says.\n"
    '- "objeciones": the lead\'s doubts or concerns (price, location, timing, requirements). If there are '
    'none, "None".\n'
    '- "siguiente_paso": what the agent should do now, based on the handoff reason.\n'
    "Rules:\n"
    "- Every number or fact must appear in the conversation or in the LEAD DATA. Don't make anything up.\n"
    "- Prices are in USD. Don't include exact addresses.\n"
    "- Don't promise discounts, reservations, loan approval or closing a deal.\n"
    "- Don't mention origin, religion, gender, nationality, family situation or marital status."
)
SUMMARY_KEYS = ("busca", "objeciones", "siguiente_paso")


class LLMError(Exception):
    """Falló la llamada al LLM (red, credenciales, límite del proveedor, respuesta inválida)."""


class LLMFormatError(LLMError):
    """El LLM respondió pero no en el formato pedido."""


@dataclass(frozen=True)
class Classification:
    intent: str
    signal: dict[str, Any]


@dataclass
class ChatResult:
    text: str
    tool_results: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


ToolRunner = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def parse_json_object(raw: str) -> dict[str, Any]:
    """Extrae el objeto JSON de la respuesta, aunque venga en un bloque de código Markdown (DT-6)."""
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S | re.I)
    if fenced:
        candidates.insert(0, fenced.group(1))
    braces = re.search(r"\{.*\}", raw, re.S)
    if braces:
        candidates.append(braces.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate.strip())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise LLMFormatError("la respuesta del clasificador no contiene un objeto JSON")


CHAT_OPENED = "(The lead opened the conversation.)"


def to_messages(history: Sequence[tuple[str, str]], current: str) -> list[dict[str, Any]]:
    """Historial + mensaje actual en el formato de Converse: empieza con `user` y alterna roles."""
    messages: list[dict[str, Any]] = []
    for role, content in [*history, ("user", current)]:
        if role not in ("user", "assistant") or not content.strip():
            continue
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"][0]["text"] += "\n" + content
        else:
            messages.append({"role": role, "content": [{"text": content}]})
    if messages and messages[0]["role"] != "user":
        # El lead abrió el chat sin escribir (p. ej. `/start prop_<id>`, #122): el saludo del asistente se
        # conserva, porque puede traer la referencia de la propiedad del anuncio.
        messages.insert(0, {"role": "user", "content": [{"text": CHAT_OPENED}]})
    return messages


def tool_config(specs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"tools": [{"toolSpec": {"name": s["name"], "description": s["description"] or s["name"],
                                    "inputSchema": {"json": s["input_schema"]}}} for s in specs]}


class BedrockLLM:
    def __init__(self, client: Any, chat_model_id: str, classifier_model_id: str, limits: RateLimits,
                 *, max_wait: float = 15.0, retrier: Retrier | None = None) -> None:
        self._client = client
        self.chat_model_id = chat_model_id
        self.classifier_model_id = classifier_model_id
        self._limits = limits
        self._max_wait = max_wait
        # Sin `retrier` no se reintenta: los límites reales los pone `llm_config.build_agent` desde el entorno.
        self._retrier = retrier or Retrier()

    @property
    def retry_policy(self) -> RetryPolicy:
        return self._retrier.policy

    @contextmanager
    def turn(self) -> Iterator[None]:
        """Abre el tiempo de reintentos de un turno: las llamadas que se hagan dentro lo comparten."""
        token = _TURN_BUDGET.set(RetryBudget())
        try:
            yield
        finally:
            _TURN_BUDGET.reset(token)

    async def _converse(self, model_id: str, **request: Any) -> dict[str, Any]:
        """Una llamada a Converse. Cada intento pasa por el limitador (A-2): un reintento no se salta el cupo."""
        budget = _TURN_BUDGET.get() or RetryBudget()
        attempt = 0
        while True:
            attempt += 1
            try:
                # Un reintento no espera cupo más de lo que le queda al turno para reintentar.
                wait = self._max_wait if attempt == 1 else min(self._max_wait, self._retrier.remaining(budget))
                await self._limits.for_model(model_id).acquire(wait)
                return await run_in_threadpool(self._client.converse, modelId=model_id, **request)
            except RateLimited as exc:
                # El cupo local (DT-19) no se liberó a tiempo: sin esta línea, el turno caía al texto seguro
                # sin dejar rastro en el log (lo encontró la corrida de OBS-6 con Bedrock).
                _report_failure("LocalRateLimit", attempt, "local_rate_limit")
                raise LLMError(str(exc)) from exc
            except (ClientError, BotoCoreError) as exc:
                decision = self._retrier.decide(exc, attempt, budget)
                code = error_code(exc)
                if decision.reason != RETRY:
                    _report_failure(code, attempt, decision.reason)
                    raise LLMError(f"Bedrock: {type(exc).__name__}") from exc
                _report_retry(code, attempt, decision.delay or 0.0, decision.provider_delay)
                await self._retrier.sleep(decision.delay or 0.0)

    async def classify(self, text: str, last_assistant: str | None = None) -> Classification:
        """Intención y señal cruda del mensaje actual. El contexto es solo la última respuesta del asistente."""
        prompt = f"CURRENT MESSAGE:\n{text}"
        if last_assistant:
            prompt = f"LAST ASSISTANT MESSAGE (context only):\n{last_assistant}\n\n{prompt}"
        response = await self._converse(
            self.classifier_model_id, system=[{"text": CLASSIFIER_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 200, "temperature": 0.0})
        data = parse_json_object(_text_of(response))
        intent = data.get("intent")
        signal = {key: data.get(key) for key in ("budget_hint", "decision_maker", "urgency")}
        return Classification(intent if intent in INTENTS else "otro", signal)

    async def summarize(self, transcript: str, facts: str) -> dict[str, str]:
        """El resumen del traspaso para el asesor (A-16), con el modelo de conversación y sin tools."""
        prompt = f"LEAD DATA:\n{facts or 'no data'}\n\nCONVERSATION:\n{transcript or 'no messages'}"
        response = await self._converse(
            self.chat_model_id, system=[{"text": SUMMARY_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": MAX_REPLY_TOKENS, "temperature": 0.0})
        data = parse_json_object(_text_of(response))
        sections = {key: data.get(key) for key in SUMMARY_KEYS}
        if not all(isinstance(value, str) and value.strip() for value in sections.values()):
            raise LLMFormatError("el resumen no trae las tres secciones")
        return {key: value.strip() for key, value in sections.items()}

    async def chat(self, history: Sequence[tuple[str, str]], current: str,
                   specs: Sequence[Mapping[str, Any]], run_tool: ToolRunner) -> ChatResult:
        """Responde al lead. Si el modelo pide tools, las ejecuta y le devuelve los resultados."""
        return await self._converse_loop(to_messages(history, current), specs, run_tool)

    async def regenerate(self, history: Sequence[tuple[str, str]], current: str, previous_reply: str,
                         violations: Sequence[Any], specs: Sequence[Mapping[str, Any]],
                         run_tool: ToolRunner) -> ChatResult:
        """Repite la conversación con la respuesta candidata y una instrucción de corregirla (A-5).

        Se le muestra al modelo su propia respuesta y qué de ella no se pudo verificar, para que la
        rehaga con lo que ya sabe de las herramientas — no para regañarlo, para darle la oportunidad
        de arreglarlo con datos reales antes de degradar la conversación.
        """
        messages = to_messages(history, current)
        messages.append({"role": "assistant", "content": [{"text": previous_reply}]})
        messages.append({"role": "user", "content": [{"text": _correction_prompt(violations)}]})
        return await self._converse_loop(messages, specs, run_tool)

    async def _converse_loop(self, messages: list[dict[str, Any]], specs: Sequence[Mapping[str, Any]],
                             run_tool: ToolRunner) -> ChatResult:
        result = ChatResult(text="")
        for _ in range(MAX_TOOL_ROUNDS + 1):
            request: dict[str, Any] = {"system": [{"text": CHAT_SYSTEM}], "messages": messages,
                                       "inferenceConfig": {"maxTokens": MAX_REPLY_TOKENS, "temperature": 0.3}}
            if specs:
                request["toolConfig"] = tool_config(specs)
            response = await self._converse(self.chat_model_id, **request)
            message = response["output"]["message"]
            uses = [block["toolUse"] for block in message["content"] if "toolUse" in block]
            if not uses or response.get("stopReason") != "tool_use":
                result.text = _text_of(response).strip()
                return result
            messages.append(message)
            messages.append({"role": "user", "content": [await self._run(use, run_tool, result) for use in uses]})
        raise LLMError("el modelo siguió pidiendo herramientas sin responder")

    async def _run(self, use: Mapping[str, Any], run_tool: ToolRunner, result: ChatResult) -> dict[str, Any]:
        from app.catalog_client import CatalogToolError, UnknownTool

        name = use["name"]
        try:
            payload = await run_tool(name, dict(use.get("input") or {}))
        except (CatalogToolError, UnknownTool) as exc:
            error = exc.error if isinstance(exc, CatalogToolError) else {"code": "UNKNOWN_TOOL", "message": str(exc)}
            result.tool_calls.append({"name": name, "is_error": True})
            return {"toolResult": {"toolUseId": use["toolUseId"], "status": "error", "content": [{"json": error}]}}
        result.tool_results[name] = payload
        result.tool_calls.append({"name": name, "is_error": False})
        return {"toolResult": {"toolUseId": use["toolUseId"], "status": "success", "content": [{"json": payload}]}}


def _report_retry(code: str, attempt: int, delay: float, provider_delay: bool) -> None:
    """Un reintento queda en el log y en la traza del turno: solo el código del error, nunca el texto del lead."""
    logger.warning("Bedrock: %s en el intento %d, se reintenta en %.2fs", code, attempt, delay,
                   extra={"llm_error_code": code, "llm_attempt": attempt, "llm_retry_delay_s": round(delay, 3),
                          "llm_retry_after_from_provider": provider_delay})
    trace.get_current_span().add_event("llm.retry", {"llm.error_code": code, "llm.attempt": attempt,
                                                     "llm.retry_delay_s": round(delay, 3)})


def _report_failure(code: str, attempt: int, reason: str) -> None:
    logger.warning("Bedrock: %s en el intento %d, fallo definitivo (%s)", code, attempt, reason,
                   extra={"llm_error_code": code, "llm_attempt": attempt, "llm_give_up_reason": reason})
    span = trace.get_current_span()
    span.set_attributes({"llm.error_code": code, "llm.attempts": attempt, "llm.give_up_reason": reason})
    span.add_event("llm.failed", {"llm.error_code": code, "llm.attempt": attempt, "llm.give_up_reason": reason})


def _correction_prompt(violations: Sequence[Any]) -> str:
    detected = "; ".join(f"{v.kind} '{v.value}'" for v in violations)
    return (
        f"Your previous reply mentioned this without it coming from any tool you called: "
        f"{detected}. Do not repeat it: reply again using only the data you already have from the "
        "tools. If you are missing a fact, say so or call the right tool again instead of making it up."
    )


def _text_of(response: Mapping[str, Any]) -> str:
    try:
        blocks = response["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise LLMFormatError("respuesta de Bedrock sin mensaje") from exc
    return "".join(block.get("text", "") for block in blocks)
