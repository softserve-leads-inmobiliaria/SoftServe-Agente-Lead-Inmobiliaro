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
from app.interest import UNCLEAR, refers_to, shown_lines
from app.llm_retry import RETRY, Retrier, RetryBudget, RetryPolicy, error_code
from app.markers import FIELDS
from app.rate_limit import RateLimited, RateLimits
from app.visits import visit_when

logger = logging.getLogger("core.bedrock_llm")

MAX_TOOL_ROUNDS = 3
MAX_REPLY_TOKENS = 400

# Tiempo de reintentos del turno en curso (A-14): lo abre `BedrockLLM.turn()` y lo comparten todas las
# llamadas del turno. Sin turno abierto, cada llamada tiene el suyo.
_TURN_BUDGET: ContextVar[RetryBudget | None] = ContextVar("llm_turn_retry_budget", default=None)

# Los campos que el modelo puede pedir en un marcador (DT-60), en el orden en que se le enumeran: los requisitos
# sin su prefijo (`markers.ALIASES`), y sin `title`, que el modelo ya escribe bien.
MARKER_FIELDS = sorted((f.removeprefix("requirements.") for f in FIELDS - {"description", "valid_until", "title"}),
                       key=lambda f: (f.startswith(("down_", "income_", "monthly_")), f))
# Los campos booleanos del clasificador con los que decide el código (DT-60). Si falta uno, se usa la regla.
CLASSIFIER_FLAGS = ("asks_for_person", "wants_alert", "wants_visit", "frustrated")

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
    '- "asks_for_person": true if the current message asks to talk to a person (an agent, an advisor, someone '
    "who calls them), or clearly says yes to the offer to put them in touch with one that the LAST ASSISTANT "
    "MESSAGE made (\"yes\", \"please do\", \"ok, connect me\"); giving information or answering another "
    'question is not saying yes. Otherwise false.\n'
    '- "wants_alert": true if the current message asks to be notified when a property like the one they want '
    "comes up, or accepts the offer to notify them that the LAST ASSISTANT MESSAGE made; otherwise false.\n"
    '- "wants_visit": true if the current message asks to visit or see a property in person; otherwise false. '
    'Reserving, holding or buying a property is not a visit.\n'
    '- "frustrated": true if the current message shows the lead is annoyed with the assistant: complains that it '
    "isn't helping or understanding, repeats a request impatiently, or says so (\"this is useless\", \"you're not "
    'answering me"). A neutral or curious question is not frustration. Otherwise false.\n'
    '- "visit_when": only if the current message says when the lead would like to visit, an object with '
    '"days" (a list of "mon", "tue", "wed", "thu", "fri", "sat", "sun"; "this weekend" is ["sat", "sun"]; '
    'empty if no day is named), "part_of_day" ("morning", "afternoon", "evening" or null) and "other_day" '
    "(true if they ask for a different day than the times the LAST ASSISTANT MESSAGE offered). Otherwise null.\n"
    '- "refers_to": only if PROPERTIES SHOWN is given and the current message is about one of them: its id, '
    'whether the message names it or describes it ("the cheaper one", "the house", "the one in Laureles", "it" '
    'when the LAST ASSISTANT MESSAGE talked about only one). "' + UNCLEAR + '" if it is about one of them but '
    "you can't tell which. null if it is about none of them or about a new search. Never an id that is not in "
    "the list.\n"
    "Extract only from the CURRENT MESSAGE. Do not infer anything about the person's origin, religion, "
    "gender, nationality or family situation."
)

CHAT_SYSTEM = (
    "You are the virtual assistant of a real estate agency with properties in Medellin and Bogota, "
    "Colombia. You chat in short messages, in English, in a friendly and straightforward way. Prices are "
    "in US dollars (USD).\n"
    "Rules:\n"
    "- Every property fact (price, area, availability, promotions, requirements) comes from a catalog tool: if "
    "you don't have it, look it up; if there is no result, say so. Never type one yourself: write a marker, the "
    "property's reference and the field in braces, and the system fills in the tool's value: \"It's {prop_00231.price}, with {prop_00231.bedrooms} bedrooms "
    "and {prop_00231.m2} m²\". Fields: " + ", ".join(MARKER_FIELDS) + "; also {search.count}, {zones} and "
    "{promo_001.title}, .description or .valid_until. Type only the lead's own numbers and list numbers.\n"
    "- If the lead asks where you have properties, or about a city or area, call list_zones and give the "
    "areas with {zones}: never name an area from memory. A city that isn't there is one we don't cover; "
    "say so and offer the ones we do.\n"
    "- If the lead asks what you have (for rent, for sale, a kind of property), search right away with what "
    "they gave you; don't ask for more filters first.\n"
    "- Never tell the lead you will look something up or check later: if you need a fact, call the tool now, "
    "in this same reply.\n"
    "- If a search has no `properties` but has `alternatives`, there is nothing exactly like what the lead "
    "asked for: say so, and offer the alternatives, making clear they don't match the filter named in "
    "`alternatives_without` (`zone`: in another area; `kind`: another kind of property; `type`: for sale "
    "instead of for rent, or the other way round). Don't ask the lead "
    "for their area again.\n"
    "- Never write the tools' internal Spanish values (`venta`, `apartado`…): {ref.type}, {ref.kind} and "
    "{ref.status} say them in English.\n"
    "- In a search, `properties` (or `alternatives`, when there are no `properties`) are the only ones you "
    "can offer. The ones in `unavailable` are not "
    "available: if the lead asks about one, tell them its status ({ref.status}) and do not offer it.\n"
    "- If the lead came from a property's listing (you will see its reference, `ref. prop_…`, in the "
    "conversation), when they say \"this one\", \"this apartment\" or something similar they mean that "
    "one: look it up with get_property and do not ask them for the reference. If they ask about a price or "
    "a detail without saying which property and the conversation doesn't make it clear, look up the "
    "listing's property with get_property and ask whether they mean that one, instead of asking for their "
    "area or budget.\n"
    "- If the lead asks what the requirements are, first list that property's requirements as get_property "
    "returns them in `requirements` (minimum down payment with its percentage and amount, minimum monthly "
    "income, references), and then ask for the figure you need to check them.\n"
    "- To check whether the lead meets the requirements, ask how much they have for the down payment (sale "
    "or pre-sale) or how much they earn per month (rent), and whether they can provide references (rent). "
    "Then check with check_requirements. Never tell the lead they meet or don't meet a requirement without "
    "that tool: their budget is not their down payment.\n"
    "- The exact address ({ref.address_exact}) is shared only once the lead is qualified (budget, who decides "
    "and when); until then, give {ref.address_approx} and don't give any other reason.\n"
    "- Do not promise discounts, holds, credit approval or the closing of a deal.\n"
    "- Don't calculate differences, totals or other figures: give each property's own values. Don't give "
    "opinions about a property or an area, or legal advice, and don't urge the lead to hurry.\n"
    "- Do not ask about or comment on origin, religion, gender, nationality or family situation. If the "
    "lead asks whether any of that matters, say clearly that it doesn't affect how we help them or the "
    "requirements, and go on with the conversation.\n"
    "- Visits are booked by the system, never by you: don't say a visit is booked or scheduled, and don't say "
    "you can't arrange one. If the lead wants to visit a property you talked about but haven't looked up "
    "this turn, look it up with get_property; if it isn't clear which one, ask.\n"
    "- Before saying we don't have something (a property, a kind, an area), search for it.\n"
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
    flags: dict[str, bool] = field(default_factory=dict)  # solo los `CLASSIFIER_FLAGS` que vinieron booleanos
    when: dict[str, Any] | None = None  # cuándo prefiere la visita (`visits.visit_when`), si lo dijo (#247)
    # De qué propiedad mostrada habla el mensaje (`interest.refers_to`, #247): un id de la lista, `UNCLEAR` o None.
    refers_to: str | None = None


@dataclass
class ChatResult:
    text: str
    tool_results: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # La conversación tal como la vio el modelo antes de su respuesta final: el historial, el mensaje del lead
    # y las rondas de tools (`toolUse`/`toolResult`). La regeneración parte de acá (#232).
    messages: list[dict[str, Any]] = field(default_factory=list)
    # Cada respuesta de una tool, en orden: `tool_results` guarda solo la última de cada tool, y los marcadores
    # (DT-60) pueden nombrar las dos propiedades que trajeron dos llamadas a `get_property`.
    payloads: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


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

    async def classify(self, text: str, last_assistant: str | None = None,
                       shown: Sequence[Mapping[str, Any]] = ()) -> Classification:
        """Intención y señal cruda del mensaje actual. El contexto es la última respuesta del asistente y las
        propiedades que ya mostró la conversación (`shown`, `interest.shown_properties`), para `refers_to`."""
        prompt = f"CURRENT MESSAGE:\n{text}"
        if last_assistant:
            prompt = f"LAST ASSISTANT MESSAGE (context only):\n{last_assistant}\n\n{prompt}"
        if shown:
            prompt = f"PROPERTIES SHOWN (oldest first):\n{shown_lines(list(shown))}\n\n{prompt}"
        response = await self._converse(
            self.classifier_model_id, system=[{"text": CLASSIFIER_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0.0})
        data = parse_json_object(_text_of(response))
        intent = data.get("intent")
        signal = {key: data.get(key) for key in ("budget_hint", "decision_maker", "urgency")}
        flags = {key: data[key] for key in CLASSIFIER_FLAGS if isinstance(data.get(key), bool)}
        when = visit_when(data.get("visit_when"))
        about = refers_to(data.get("refers_to"), list(shown))
        return Classification(intent if intent in INTENTS else "otro", signal, flags, when, about)

    async def summarize(self, transcript: str, facts: str) -> dict[str, str]:
        """El resumen del traspaso para el asesor (A-16), con el modelo de conversación y sin tools."""
        prompt = f"LEAD DATA:\n{facts or 'no data'}\n\nCONVERSATION:\n{transcript or 'no messages'}"
        response = await self._converse(
            self.chat_model_id, system=[{"text": SUMMARY_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": MAX_REPLY_TOKENS, "temperature": 0.0})
        # El motivo va en el error, sin el texto: en la prueba de jurado (#247) falló una vez y el log no decía si
        # el JSON se cortó por `maxTokens` o si vino una sección vacía.
        how = (f"stop={response.get('stopReason')}, "
               f"tokens={(response.get('usage') or {}).get('outputTokens')}/{MAX_REPLY_TOKENS}")
        try:
            data = parse_json_object(_text_of(response))
        except LLMFormatError:
            raise LLMFormatError(f"el resumen no es un objeto JSON ({how})") from None
        sections = {key: data.get(key) for key in SUMMARY_KEYS}
        missing = [key for key, value in sections.items() if not (isinstance(value, str) and value.strip())]
        if missing:
            raise LLMFormatError(f"al resumen le faltan secciones: {', '.join(missing)} ({how})")
        return {key: value.strip() for key, value in sections.items()}

    async def chat(self, history: Sequence[tuple[str, str]], current: str,
                   specs: Sequence[Mapping[str, Any]], run_tool: ToolRunner) -> ChatResult:
        """Responde al lead. Si el modelo pide tools, las ejecuta y le devuelve los resultados."""
        return await self._converse_loop(to_messages(history, current), specs, run_tool)

    async def regenerate(self, history: Sequence[tuple[str, str]], current: str, previous_reply: str,
                         violations: Sequence[Any], specs: Sequence[Mapping[str, Any]],
                         run_tool: ToolRunner, messages: Sequence[Mapping[str, Any]] = ()) -> ChatResult:
        """Rehace la respuesta al lead sabiendo qué de la candidata no se pudo verificar (A-5).

        Se le muestra al modelo su propia respuesta y qué de ella no se pudo verificar, para que la
        rehaga con datos reales antes de degradar la conversación. Va en el *system*, no como mensaje
        `user`: el modelo creía que el lead lo había corregido, se disculpaba y prometía buscar (#213).
        La conversación termina en el mensaje del lead o en los resultados de las tools que ya consultó.

        `messages` es la conversación del primer intento (`ChatResult.messages`), con sus rondas de tools: así
        el modelo ya tiene los datos y no vuelve a pedirlos (#232). Sin ella, se arma desde el historial.
        """
        system = f"{CHAT_SYSTEM}\n\n{_correction_prompt(previous_reply, violations)}"
        conversation = [dict(message) for message in messages] or to_messages(history, current)
        return await self._converse_loop(conversation, specs, run_tool, system=system)

    async def _converse_loop(self, messages: list[dict[str, Any]], specs: Sequence[Mapping[str, Any]],
                             run_tool: ToolRunner, system: str = CHAT_SYSTEM) -> ChatResult:
        result = ChatResult(text="")
        for _ in range(MAX_TOOL_ROUNDS + 1):
            request: dict[str, Any] = {"system": [{"text": system}], "messages": messages,
                                       "inferenceConfig": {"maxTokens": MAX_REPLY_TOKENS, "temperature": 0.0}}
            if specs:
                request["toolConfig"] = tool_config(specs)
            response = await self._converse(self.chat_model_id, **request)
            message = response["output"]["message"]
            uses = [block["toolUse"] for block in message["content"] if "toolUse" in block]
            if not uses or response.get("stopReason") != "tool_use":
                result.text = _text_of(response).strip()
                if response.get("stopReason") == "max_tokens":
                    result.text = _cut_to_last_sentence(result.text)
                result.messages = list(messages)
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
        result.payloads.append((name, payload))
        result.tool_calls.append({"name": name, "is_error": False})
        return {"toolResult": {"toolUseId": use["toolUseId"], "status": "success", "content": [{"json": payload}]}}


# Sin ":": "Here are the options:" sin la lista que anuncia no es una respuesta completa.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")


def _cut_to_last_sentence(text: str) -> str:
    """Una respuesta que llegó al tope de `MAX_REPLY_TOKENS` (#235), recortada a la última frase o línea completa.

    Sin esto se enviaba cortada a mitad de frase, y sin dejar rastro. Si no hay ninguna frase completa, se
    devuelve vacía: el agente la trata como una respuesta inválida y contesta el texto seguro.
    """
    logger.warning("Bedrock: la respuesta llegó al tope de %d tokens y se recorta", MAX_REPLY_TOKENS,
                   extra={"llm_stop_reason": "max_tokens"})
    trace.get_current_span().add_event("llm.reply_truncated", {"llm.max_tokens": MAX_REPLY_TOKENS})
    ends = [match.end() for match in _SENTENCE_END.finditer(text)]
    return text[:ends[-1]].strip() if ends else ""


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


def _correction_prompt(previous_reply: str, violations: Sequence[Any]) -> str:
    """La instrucción interna de la regeneración (A-5). El lead nunca la ve, y el modelo tiene que saberlo.

    Los números van normalizados ("1150"). Si un número suelto es un único dato de una única propiedad, se le dice
    qué marcador usar (DT-60): así la corrección no depende de que el modelo lo adivine.
    """
    detected = "; ".join(_described(v) for v in violations)
    return (
        "INTERNAL CHECK (the lead never sees this): your draft reply to the lead's last message was:\n"
        f"<draft>{previous_reply}</draft>\n"
        f"This could not be verified: {detected}. Write your reply to the lead again. Write every property fact "
        "as a marker ({prop_00231.price}), never as a number or a status you type yourself. A marker can only "
        "name a property and a field that a tool returned; if the lead needs a fact you don't have, call the "
        "right tool now (get_property, or the same search again for a property you mentioned before). "
        "Do not apologize and do not mention the draft or this check: the lead did not correct you. Never tell "
        "the lead you will look something up later."
    )


def _described(violation: Any) -> str:
    if violation.kind == "number":
        hint = getattr(violation, "hint", None)
        return f"number '{violation.value}'" + (f" (write {hint} instead)" if hint else " (no marker gives it)")
    if violation.kind == "marker":
        hint = getattr(violation, "hint", None)
        return f"marker '{violation.value}' (" + (f"write {hint}" if hint else "no tool returned that property or field") + ")"
    if violation.kind == "reference":
        return f"reference '{violation.value}' (no tool returned it)"
    return f"{violation.kind} '{violation.value}'"


def _text_of(response: Mapping[str, Any]) -> str:
    try:
        blocks = response["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise LLMFormatError("respuesta de Bedrock sin mensaje") from exc
    return "".join(block.get("text", "") for block in blocks)
