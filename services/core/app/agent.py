"""Orquestador del turno (A-2): decide la intención, consulta el catálogo y redacta la respuesta.

Hay dos agentes con la misma interfaz: `FakeAgent` (el de siempre, sin credenciales) y `BedrockAgent`
(LLM real, DT-6). Se elige con `LLM_PROVIDER` (`app/llm_config.py`). El agente solo trabaja con el
texto que dejó pasar el guardrail de entrada: sin PII ni menciones de categorías protegidas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from app import factuality
from app.bedrock_llm import BedrockLLM, LLMError, LLMFormatError
from app.fakes.fake_llm import FakeLLM
from app.fsm import HUMAN_INTENTS
from app.input_guard import HANDOFF_OFFER, OMITTED_MESSAGE
from app.skeleton import respond

HISTORY_LIMIT = 20  # mensajes previos que se le dan al LLM

LLM_FALLBACK_REPLY = "Disculpa, tuve un problema para procesar tu mensaje. ¿Me lo puedes repetir?"
# A-5: ni el primer intento ni la regeneración pasaron la factualidad. Nunca se manda el dato dudoso.
FACTUALITY_DEGRADE_REPLY = "No tengo un dato verificado para responder eso con seguridad." + HANDOFF_OFFER

# El LLM no decide esto: lo fija el código a partir del estado del lead (A-6, dirección reservada).
CODE_CONTROLLED_ARGUMENTS = ("lead_qualified",)


@dataclass(frozen=True)
class TurnInput:
    event: Mapping[str, Any]
    text: str  # el mensaje ya filtrado por el guardrail de entrada
    history: tuple[tuple[str, str], ...] = ()  # (rol, contenido), del más viejo al más nuevo
    lead_qualified: bool = False


@dataclass(frozen=True)
class AgentResult:
    text: str
    intent: str
    raw_signal: dict[str, Any] = field(default_factory=dict)
    tool_results: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # solo nombre y si falló
    llm_error: str | None = None
    # (acción, detalle) del guardrail de salida (A-5); nunca lleva el número/estado inventado, solo el conteo.
    guard_audits: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


class ToolCatalog(Protocol):
    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...

    async def tool_specs(self) -> list[dict[str, Any]]: ...


class Agent(Protocol):
    name: str

    async def run(self, turn: TurnInput, catalog: Any) -> AgentResult: ...


class FakeAgent:
    """LLM falso (CT-7): intención por palabras clave y respuesta fija. No necesita credenciales."""

    name = "fake"

    async def run(self, turn: TurnInput, catalog: Any) -> AgentResult:
        reply = await respond({**turn.event, "text": turn.text}, catalog)
        return AgentResult(text=reply.outbound["text"], intent=reply.intent,
                           raw_signal=FakeLLM().extract_signal(turn.text), tool_results=reply.tool_results,
                           tool_calls=[{"name": name, "is_error": False} for name in reply.tool_results])


class BedrockAgent:
    """LLM real en Bedrock: un modelo clasifica y extrae señal, otro conversa con las tools del catálogo."""

    name = "bedrock"

    def __init__(self, llm: BedrockLLM) -> None:
        self._llm = llm

    async def run(self, turn: TurnInput, catalog: ToolCatalog) -> AgentResult:
        if not turn.text.strip():  # p. ej. un deep-link sin texto: el lead acaba de llegar
            return AgentResult(text=FakeLLM().generate_reply("saludo"), intent="saludo")
        if turn.text == OMITTED_MESSAGE:  # no quedó nada utilizable: no se gasta una llamada
            return AgentResult(text=FakeLLM().generate_reply("otro"), intent="otro")
        last_assistant = next((content for role, content in reversed(turn.history) if role == "assistant"), None)
        try:
            classification = await self._llm.classify(turn.text, last_assistant)
            if classification.intent in HUMAN_INTENTS:
                # Un traspaso a una persona lo dice siempre el mismo texto: no se le deja al modelo.
                return AgentResult(text=FakeLLM().generate_reply(classification.intent),
                                   intent=classification.intent, raw_signal=classification.signal)
            specs = await catalog.tool_specs()
            hidden = {s["name"]: [a for a in CODE_CONTROLLED_ARGUMENTS if a in s["input_schema"].get("properties", {})]
                      for s in specs}

            async def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                arguments = {k: v for k, v in arguments.items() if k not in CODE_CONTROLLED_ARGUMENTS}
                for argument in hidden.get(name, ()):
                    arguments[argument] = getattr(turn, argument)
                return await catalog.call(name, arguments)

            visible = [_visible(s) for s in specs]
            chat = await self._llm.chat(turn.history, turn.text, visible, run_tool)
            if not chat.text:
                raise LLMFormatError("respuesta vacía")
            guard_audits: list[tuple[str, dict[str, Any]]] = []
            violations = factuality.check(chat.text, chat.tool_results, turn.text)
            if violations:
                # Una oportunidad de corregirse con lo que ya sabe, antes de degradar la conversación (A-5).
                retry = await self._llm.regenerate(turn.history, turn.text, chat.text, violations, visible, run_tool)
                chat.tool_results.update(retry.tool_results)
                chat.tool_calls += retry.tool_calls
                guard_audits.append(("output.factuality_regenerated", {"violations": len(violations)}))
                violations = (factuality.check(retry.text, chat.tool_results, turn.text) if retry.text.strip()
                             else violations)
                if violations:
                    guard_audits.append(("output.factuality_degraded", {"violations": len(violations)}))
                    return AgentResult(text=FACTUALITY_DEGRADE_REPLY, intent="otro",
                                       raw_signal=classification.signal, tool_results=chat.tool_results,
                                       tool_calls=chat.tool_calls, guard_audits=guard_audits)
                chat.text = retry.text
        except LLMError as exc:
            # Sin LLM no se inventa nada: texto seguro, y el turno cuenta como "sin resolver" (A-13).
            return AgentResult(text=LLM_FALLBACK_REPLY, intent="otro", llm_error=type(exc).__name__)
        return AgentResult(text=chat.text, intent=classification.intent, raw_signal=classification.signal,
                           tool_results=chat.tool_results, tool_calls=chat.tool_calls, guard_audits=guard_audits)


def _visible(spec: Mapping[str, Any]) -> dict[str, Any]:
    """La tool tal como la ve el LLM: sin los argumentos que controla el código."""
    schema = dict(spec["input_schema"])
    properties = {k: v for k, v in schema.get("properties", {}).items() if k not in CODE_CONTROLLED_ARGUMENTS}
    schema["properties"] = properties
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r not in CODE_CONTROLLED_ARGUMENTS]
    return {"name": spec["name"], "description": spec["description"], "input_schema": schema}
