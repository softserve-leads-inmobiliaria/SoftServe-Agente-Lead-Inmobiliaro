"""Orquestador del turno (A-2): decide la intención, consulta el catálogo y redacta la respuesta.

Hay dos agentes con la misma interfaz: `FakeAgent` (el de siempre, sin credenciales) y `BedrockAgent`
(LLM real, DT-6). Se elige con `LLM_PROVIDER` (`app/llm_config.py`). El agente solo trabaja con el
texto que dejó pasar el guardrail de entrada: sin PII ni menciones de categorías protegidas.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol, Sequence

from app import address_guard, factuality, interest, markers, promise_guard
from app.bedrock_llm import BedrockLLM, Classification, LLMError, LLMFormatError
from app.fakes.fake_llm import FakeLLM
from app.fsm import HUMAN_INTENTS
from app.handoff import asks_for_a_person
from app.input_guard import HANDOFF_OFFER, OMITTED_MESSAGE
from app.skeleton import respond
from app.summary import SummaryFacts
from app.summary import template as summary_template
from app.tracing import content_hidden

logger = logging.getLogger("core.agent")

HISTORY_LIMIT = 20  # mensajes previos que se le dan al LLM

LLM_FALLBACK_REPLY = "Sorry, I had a problem processing your message. Could you send it again?"
# A-5: ni el primer intento ni la regeneración pasaron la factualidad. Nunca se manda el dato dudoso.
# Que no suene a error (#247): en una demo, "I don't have verified information" se leía como una falla.
FACTUALITY_DEGRADE_REPLY = ("That's a detail I'd rather have one of our agents confirm, so you get it right."
                            + HANDOFF_OFFER)
# #200: el guardrail quitó una mención de categoría protegida y lo que queda es una pregunta ("does that
# matter?"). Los dos agentes contestan esto con el mismo texto; si el mensaje pregunta otra cosa, el LLM
# recibe la nota de `_with_notes` y contesta a su modo.
PROTECTED_ANSWER = ("That doesn't affect how we help you or the requirements to buy or rent: we treat "
                    "every client the same way. What kind of property are you looking for?")

# #228: el clasificador dijo `solicitud_humano` pero el mensaje no pide una persona; el turno sigue sin traspaso.
HUMAN_REQUEST_UNCONFIRMED = "intent.human_request_unconfirmed"

# El LLM no decide esto: lo fija el código a partir del estado del lead (A-6, dirección reservada).
CODE_CONTROLLED_ARGUMENTS = ("lead_qualified",)


@dataclass(frozen=True)
class TurnInput:
    event: Mapping[str, Any]
    text: str  # el mensaje ya filtrado por el guardrail de entrada
    history: tuple[tuple[str, str], ...] = ()  # (rol, contenido), del más viejo al más nuevo
    lead_qualified: bool = False
    listing: Mapping[str, Any] | None = None  # la propiedad del anuncio por el que llegó el lead (#122)
    protected_removed: bool = False  # el guardrail quitó una mención de categoría protegida (#200)
    # Lo que las tools dijeron de cada propiedad en turnos anteriores (#217): respalda números y amenidades.
    remembered: Mapping[str, Any] = field(default_factory=dict)
    # Si la respuesta la va a dar el código según la intención y los campos del clasificador (la agenda, #229):
    # entonces solo se clasifica. En vivo, el modelo contestaba igual, copiaba los horarios de la oferta, y la
    # factualidad lo regeneraba para una respuesta que el lead nunca leía.
    answered_by_code: Callable[[Classification], bool] | None = None


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
    # Lo que el clasificador contestó a las preguntas de sí o no (`CLASSIFIER_FLAGS`, DT-60). Vacío con
    # `FakeAgent`, o si el clasificador no las contestó: el código usa entonces sus reglas.
    flags: dict[str, bool] = field(default_factory=dict)
    # Cuándo prefiere el lead la visita (`visits.visit_when`, #247), si el clasificador lo leyó en el mensaje.
    visit_when: dict[str, Any] | None = None
    # De qué propiedad mostrada habla el mensaje (`interest.refers_to`, #247): un id validado, `UNCLEAR` o None.
    refers_to: str | None = None
    # Quién escribió la respuesta, para el registro del turno (`turn.decision`, paso 0 del plan de confiabilidad):
    # "agent" (el modelo), "agenda" (la deja al código), "handoff", "protected", "degraded", "llm_error", "fixed"
    # (texto fijo sin LLM) o "input_guard".
    route: str = "agent"
    # La intención que dio el clasificador, antes de que el código la cambie (p. ej. a "otro" al degradar).
    classified: str | None = None


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
        # Solo si no pregunta otra cosa: "I'm Colombian, how much is it?" sigue siendo una consulta de precio.
        if turn.protected_removed and "?" in turn.text and FakeLLM().classify_intent(turn.text) == "otro":
            return AgentResult(text=PROTECTED_ANSWER, intent="otro")
        reply = await respond({**turn.event, "text": turn.text}, catalog)
        return AgentResult(text=reply.outbound["text"], intent=reply.intent,
                           raw_signal=FakeLLM().extract_signal(turn.text), tool_results=reply.tool_results,
                           tool_calls=[{"name": name, "is_error": False} for name in reply.tool_results])

    async def summarize(self, transcript: str, facts: SummaryFacts) -> dict[str, str]:
        """El resumen del traspaso (A-16) sale de una plantilla determinista, sin leer la conversación."""
        return summary_template(facts)


class BedrockAgent:
    """LLM real en Bedrock: un modelo clasifica y extrae señal, otro conversa con las tools del catálogo."""

    name = "bedrock"

    def __init__(self, llm: BedrockLLM) -> None:
        self._llm = llm

    async def run(self, turn: TurnInput, catalog: ToolCatalog) -> AgentResult:
        with self._llm.turn():  # las llamadas del turno comparten el tiempo de reintentos (A-14)
            return await self._run(turn, catalog)

    async def _run(self, turn: TurnInput, catalog: ToolCatalog) -> AgentResult:
        if not turn.text.strip():  # p. ej. un deep-link sin texto: el lead acaba de llegar
            return AgentResult(text=FakeLLM().generate_reply("saludo"), intent="saludo", route="fixed")
        if turn.text == OMITTED_MESSAGE:  # no quedó nada utilizable: no se gasta una llamada
            return AgentResult(text=FakeLLM().generate_reply("otro"), intent="otro", route="fixed")
        last_assistant = next((content for role, content in reversed(turn.history) if role == "assistant"), None)
        try:
            shown = interest.shown_properties(turn.remembered, turn.listing)
            classification = await self._llm.classify(turn.text, last_assistant, shown)
            intent, flags, when = classification.intent, classification.flags, classification.when
            about = classification.refers_to
            guard_audits: list[tuple[str, dict[str, Any]]] = []
            asks = flags.get("asks_for_person")
            if intent == "solicitud_humano" and not (asks if asks is not None
                                                     else asks_for_a_person(turn.text, last_assistant)):
                # #228: un traspaso es terminal; si el mensaje no lo pide, el turno sigue como uno más.
                intent = "otro"
                guard_audits.append((HUMAN_REQUEST_UNCONFIRMED, {}))
            # Lo que entendió el clasificador, igual en cualquier ruta (`turn.decision`).
            read: dict[str, Any] = {"raw_signal": classification.signal, "classified": classification.intent,
                                    "flags": flags, "visit_when": when, "refers_to": about}
            if intent in HUMAN_INTENTS:
                # Un traspaso a una persona lo dice siempre el mismo texto: no se le deja al modelo.
                return AgentResult(text=FakeLLM().generate_reply(intent), intent=intent, route="handoff", **read)
            if turn.answered_by_code is not None and turn.answered_by_code(replace(classification, intent=intent)):
                return AgentResult(text="", intent=intent, guard_audits=guard_audits, route="agenda", **read)
            if turn.protected_removed and "?" in turn.text and intent == "otro":
                # #200: "does that matter?" se contesta siempre igual. En vivo el modelo ignoraba la nota y
                # seguía con el enganche, o lo degradaba la factualidad.
                return AgentResult(text=PROTECTED_ANSWER, intent="otro", guard_audits=guard_audits, route="protected",
                                   **read)
            specs = await catalog.tool_specs()
            hidden = {s["name"]: [a for a in CODE_CONTROLLED_ARGUMENTS if a in s["input_schema"].get("properties", {})]
                      for s in specs}

            async def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                arguments = {k: v for k, v in arguments.items() if k not in CODE_CONTROLLED_ARGUMENTS}
                for argument in hidden.get(name, ()):
                    arguments[argument] = getattr(turn, argument)
                return await catalog.call(name, arguments)

            visible = [_visible(s) for s in specs]
            current = _with_notes(turn.text, turn.listing, turn.protected_removed, turn.remembered, about,
                                  flags.get("asks_unlisted") is True)
            chat = await self._llm.chat(turn.history, current, visible, run_tool)
            if not chat.text:
                raise LLMFormatError("respuesta vacía")
            violations, reply = await _output_violations(chat.text, chat.tool_results, chat.payloads, turn, catalog)
            _log_violations("borrador", violations, chat.text)
            if violations:
                # Una oportunidad de corregirse con lo que ya sabe, antes de degradar la conversación (A-5).
                retry = await self._llm.regenerate(turn.history, current, chat.text, violations, visible, run_tool,
                                                   chat.messages)
                chat.tool_results.update(retry.tool_results)
                chat.tool_calls += retry.tool_calls
                chat.payloads += retry.payloads
                guard_audits.append(("output.factuality_regenerated", _violation_detail(violations)))
                if retry.text.strip():
                    violations, reply = await _output_violations(retry.text, chat.tool_results, chat.payloads, turn,
                                                                 catalog)
                    _log_violations("regeneración", violations, retry.text)
                if violations:
                    guard_audits.append(("output.factuality_degraded", _violation_detail(violations)))
                    return AgentResult(text=FACTUALITY_DEGRADE_REPLY, intent="otro", tool_results=chat.tool_results,
                                       tool_calls=chat.tool_calls, guard_audits=guard_audits, route="degraded", **read)
            chat.text = promise_guard.without_urgency(reply)
        except LLMError as exc:
            # Sin LLM no se inventa nada: texto seguro, y el turno cuenta como "sin resolver" (A-13).
            return AgentResult(text=LLM_FALLBACK_REPLY, intent="otro", llm_error=type(exc).__name__,
                               route="llm_error")
        return AgentResult(text=chat.text, intent=intent, tool_results=chat.tool_results, tool_calls=chat.tool_calls,
                           guard_audits=guard_audits, **read)

    async def summarize(self, transcript: str, facts: SummaryFacts) -> dict[str, str]:
        """El resumen del traspaso (A-16). Si el LLM falla, lanza `LLMError`: la ficha sale sin resumen."""
        with self._llm.turn():
            return await self._llm.summarize(transcript, "\n".join(facts.lines()))


PROTECTED_NOTE = ("(Part of the lead's message was removed because it mentioned a personal characteristic "
                  "such as origin, religion, gender, nationality or family situation. If they ask whether it "
                  "matters, tell them it doesn't affect how we help them or the requirements.)")
UNCLEAR_NOTE = ("(It's not clear which of the properties already shown the lead means: ask them which one, "
                "naming the options. Don't guess.)")
UNLISTED_NOTE = ("(The lead is asking about something the listings don't record. Don't answer yes or no and don't "
                 "guess or generalize: say you don't have that detail and that an agent can confirm it, and offer to "
                 "connect them. If several properties were shown, answer for all of them at once instead of asking "
                 "which one.)")


def _with_notes(text: str, listing: Mapping[str, Any] | None, protected_removed: bool = False,
                remembered: Mapping[str, Any] | None = None, about: str | None = None,
                unlisted: bool = False) -> str:
    """El mensaje del lead más lo que el LLM tiene que saber del turno: la referencia del anuncio por el que
    llegó (#122), las de las propiedades que ya vio la conversación (#247), de cuál de ellas habla el mensaje según
    el clasificador (`about`, #247), si pregunta algo que el catálogo no tiene (`unlisted`, #247) y que el guardrail
    quitó una mención de categoría protegida (#200), sin el dato quitado.
    Solo lo ve el LLM: no se guarda en el historial ni cuenta como algo que dijo el lead para la factualidad."""
    notes = []
    if listing:
        notes.append(f"(The lead came from the listing for {listing.get('title')}, ref. {listing.get('id')}.)")
    # El historial lleva el texto ya completado, sin las referencias: en la prueba de jurado el modelo las
    # adivinaba ({prop_00455.price}) para escribir los marcadores, y la respuesta se degradaba.
    seen = [f"{ref} ({fact.get('title')})" for ref, fact in (remembered or {}).items()
            if isinstance(fact, Mapping) and fact.get("title") and ref != (listing or {}).get("id")]
    if seen:
        notes.append(f"(Properties already shown in this conversation: {'; '.join(seen)}.)")
    # En la prueba de jurado, "the price of the reserved apartment" se contestó con una referencia inventada (M4).
    focused = interest.focused_property(about, interest.shown_properties(remembered or {}, listing))
    if focused:
        notes.append(f"(The lead's message is about {focused['id']} ({focused['title']}).)")
    elif about == interest.UNCLEAR and not unlisted:
        notes.append(UNCLEAR_NOTE)
    # "Can I share it with a roommate?" -> "Yes, absolutely!" (O3), "No, HOA fees are separate" (M1): el sí o el no
    # de algo que el catálogo no tiene era un dato inventado, y la factualidad no ve números ni estados en él.
    if unlisted:
        notes.append(UNLISTED_NOTE)
    if protected_removed:
        notes.append(PROTECTED_NOTE)
    return "\n\n".join([text, *notes])


async def _output_violations(text: str, tool_results: Mapping[str, Any],
                             payloads: Sequence[tuple[str, Mapping[str, Any]]], turn: TurnInput,
                             catalog: ToolCatalog) -> tuple[list[factuality.Violation], str]:
    """Guardrails de salida combinados, y la respuesta como la va a leer el lead.

    El modelo escribe los datos de la propiedad como marcadores, y el código los completa (DT-60): un marcador que
    no se puede completar, o un número escrito fuera de uno que no dijo el lead ni es de una sola propiedad, es una
    violación. Lo que el modelo escribió fuera de los marcadores pasa además por los estados de la factualidad (A-5)
    y por la regla de #230. La dirección reservada (A-6) y las promesas (A-7), sobre el texto ya completado. Todos
    comparten el mismo *pipeline* de regenerar/degradar (DT-23). Anunciar una búsqueda para después también es una
    promesa (#234).
    """
    sources = markers.build(payloads, turn.remembered)
    listing_id = (turn.listing or {}).get("id")
    if isinstance(listing_id, str):
        sources.records.setdefault(listing_id, {"id": listing_id})
    bare = markers.outside(text)
    # El estado de una propiedad que no vino en este turno lo consulta el código (A-4), sin gastar una llamada al
    # LLM: en vivo, el modelo repetía "it's reserved" o escribía {ref.status} sin volver a llamar la tool (#243).
    wanted = sorted(markers.missing_statuses(text, sources))
    if factuality.check_statuses(bare, tool_results, turn.text):
        # Las que nombra la respuesta primero: "the only available house is the 3-bedroom one (ref. prop_00233)"
        # habla de esa (#247, prueba de jurado); después, la del anuncio y la última recordada.
        named = markers.REFERENCE.findall(bare)
        recent = list(turn.remembered)[-1:]
        wanted += [ref for ref in (*named, listing_id, *recent) if isinstance(ref, str) and ref not in sources.statuses]
    fresh = await _availabilities(catalog, list(dict.fromkeys(wanted))[:MAX_STATUS_LOOKUPS])
    markers.add_statuses(sources, fresh)
    checked = {**tool_results, "verified_statuses": fresh} if fresh else tool_results
    lead_texts = [turn.text, *(content for role, content in turn.history if role == "user")]
    known_refs = [*filter(None, [listing_id]), *(r for t in lead_texts for r in _refs(t))]
    reply = markers.render(text, sources)
    violations = (markers.check(text, sources, markers.lead_numbers(lead_texts), known_refs)
                  + factuality.check_statuses(bare, checked, turn.text)
                  + factuality.check_lead_claims(bare, tool_results, turn.text, turn.remembered)
                  + address_guard.check(reply, turn.lead_qualified, turn.text)
                  + promise_guard.check(reply, {"remembered": dict(turn.remembered), **tool_results}, turn.text)
                  + promise_guard.check_deferred(reply, consulted_tools=bool(tool_results)))
    return list(dict.fromkeys(violations)), reply


MAX_STATUS_LOOKUPS = 3  # estados que el código consulta por respuesta, como mucho


async def _availabilities(catalog: ToolCatalog, refs: Sequence[str]) -> list[dict[str, Any]]:
    from app.catalog_client import CatalogToolError, UnknownTool

    found = []
    for ref in refs:
        try:
            found.append(await catalog.call("get_availability", {"property_id": ref}))
        except (CatalogToolError, UnknownTool):
            continue  # sin el estado, el marcador no se completa y la respuesta se regenera
    return found


def _violation_detail(violations: Sequence[factuality.Violation]) -> dict[str, Any]:
    """Lo que se audita de las violaciones: cuántas y de qué tipo, nunca el valor (OBS-7)."""
    return {"violations": len(violations), "kinds": sorted({v.kind for v in violations})}


def _log_violations(attempt: str, violations: Sequence[factuality.Violation], draft: str) -> None:
    """Qué marcó el guardrail de salida, para ajustar el prompt con la evaluación en vivo (DT-60). El valor solo con
    `TRACE_CONTENT=1`: puede ser un fragmento de la respuesta (OBS-7)."""
    if not violations:
        return
    shown = [(v.kind, v.hint) if content_hidden() else (v.kind, v.value, v.hint) for v in violations]
    logger.info("guardrail de salida (%s): %s%s", attempt, shown, "" if content_hidden() else f" — {draft!r}")


def _refs(text: str) -> list[str]:
    return markers.REFERENCE.findall(text or "")


def _visible(spec: Mapping[str, Any]) -> dict[str, Any]:
    """La tool tal como la ve el LLM: sin los argumentos que controla el código."""
    schema = dict(spec["input_schema"])
    properties = {k: v for k, v in schema.get("properties", {}).items() if k not in CODE_CONTROLLED_ARGUMENTS}
    schema["properties"] = properties
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r not in CODE_CONTROLLED_ARGUMENTS]
    return {"name": spec["name"], "description": spec["description"], "input_schema": schema}
