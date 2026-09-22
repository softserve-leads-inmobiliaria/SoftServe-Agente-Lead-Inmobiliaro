"""LLM en AWS Bedrock (A-2, DT-6): un modelo liviano clasifica y extrae señal, otro conversa con tools.

Usa la API Converse de Bedrock. El cliente de `boto3` es síncrono, así que las llamadas corren en un
hilo. Cada modelo tiene su propio límite de tasa (`app/rate_limit.py`). Nada de lo que devuelve el
LLM se toma como cierto sin validar: la intención debe ser una de `INTENTS` y la señal se acota
después en `merge_signal`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import run_in_threadpool

from app.fakes.fake_llm import INTENTS
from app.rate_limit import RateLimited, RateLimits

MAX_TOOL_ROUNDS = 3
MAX_REPLY_TOKENS = 400

CLASSIFIER_SYSTEM = (
    "Eres un clasificador para el asistente de una inmobiliaria. Lees el MENSAJE ACTUAL de un lead y "
    "respondes SOLO con un objeto JSON, sin texto adicional, con estas claves:\n"
    '- "intent": una de ' + ", ".join(INTENTS) + ".\n"
    '  "solicitud_humano" = pide hablar con una persona; "fuera_de_alcance" = queja, reclamo o asunto '
    'de un contrato o cuenta ya existente; "otro" = no encaja en ninguna de las demás.\n'
    '- "budget_hint": el presupuesto que el lead declara en el mensaje actual, en texto libre, o null.\n'
    '- "decision_maker": "solo", "pareja", "familia" o "no_declarado" según quién decide, SOLO si el '
    "mensaje actual lo dice.\n"
    '- "urgency": "inmediata", "3_meses", "explorando" o "no_declarada", SOLO si el mensaje actual lo dice.\n'
    "Extrae únicamente del MENSAJE ACTUAL. No infieras nada sobre origen, religión, género, nacionalidad "
    "ni situación familiar de la persona."
)

CHAT_SYSTEM = (
    "Eres el asistente virtual de una inmobiliaria. Conversas por mensajes cortos, en español, con "
    "amabilidad y sin rodeos.\n"
    "Reglas:\n"
    "- Todo dato de una propiedad (precio, zona, tamaño, disponibilidad, promociones, requisitos) debe "
    "salir de una herramienta del catálogo. Si no lo tienes, consúltala; si no hay resultado, dilo. No "
    "inventes datos.\n"
    "- En una búsqueda, `properties` son las únicas que puedes ofrecer. Las de `unavailable` no están "
    "disponibles: si el lead pregunta por una, dile su estado (apartada, cerrada o sin verificar) y no la "
    "ofrezcas.\n"
    "- No prometas descuentos, apartados, aprobación de crédito ni cierre de una operación.\n"
    "- No pidas ni comentes origen, religión, género, nacionalidad ni situación familiar.\n"
    "- Si no puedes ayudar con algo, ofrece comunicar al lead con un asesor.\n"
    "- Haz como máximo una pregunta por mensaje."
)


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
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def tool_config(specs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"tools": [{"toolSpec": {"name": s["name"], "description": s["description"] or s["name"],
                                    "inputSchema": {"json": s["input_schema"]}}} for s in specs]}


class BedrockLLM:
    def __init__(self, client: Any, chat_model_id: str, classifier_model_id: str, limits: RateLimits,
                 *, max_wait: float = 15.0) -> None:
        self._client = client
        self.chat_model_id = chat_model_id
        self.classifier_model_id = classifier_model_id
        self._limits = limits
        self._max_wait = max_wait

    async def _converse(self, model_id: str, **request: Any) -> dict[str, Any]:
        try:
            await self._limits.for_model(model_id).acquire(self._max_wait)
            return await run_in_threadpool(self._client.converse, modelId=model_id, **request)
        except RateLimited as exc:
            raise LLMError(str(exc)) from exc
        except (ClientError, BotoCoreError) as exc:
            raise LLMError(f"Bedrock: {type(exc).__name__}") from exc

    async def classify(self, text: str, last_assistant: str | None = None) -> Classification:
        """Intención y señal cruda del mensaje actual. El contexto es solo la última respuesta del asistente."""
        prompt = f"MENSAJE ACTUAL:\n{text}"
        if last_assistant:
            prompt = f"ÚLTIMO MENSAJE DEL ASISTENTE (solo contexto):\n{last_assistant}\n\n{prompt}"
        response = await self._converse(
            self.classifier_model_id, system=[{"text": CLASSIFIER_SYSTEM}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 200, "temperature": 0.0})
        data = parse_json_object(_text_of(response))
        intent = data.get("intent")
        signal = {key: data.get(key) for key in ("budget_hint", "decision_maker", "urgency")}
        return Classification(intent if intent in INTENTS else "otro", signal)

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


def _correction_prompt(violations: Sequence[Any]) -> str:
    detected = "; ".join(f"{v.kind} '{v.value}'" for v in violations)
    return (
        f"Tu respuesta anterior mencionó esto sin que viniera de ninguna herramienta consultada: "
        f"{detected}. No lo repitas: responde de nuevo usando solo los datos que ya tienes de las "
        "herramientas. Si te falta un dato, dilo o vuelve a consultar la herramienta correspondiente "
        "en vez de inventarlo."
    )


def _text_of(response: Mapping[str, Any]) -> str:
    try:
        blocks = response["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise LLMFormatError("respuesta de Bedrock sin mensaje") from exc
    return "".join(block.get("text", "") for block in blocks)
