"""Un turno completo: guardrail de entrada, respuesta, FSM y score, persistidos en Postgres.

El orden es: validar el evento -> guardrail de entrada (A-8) -> responder -> mover la FSM, puntuar y
auditar en una sola transacción (A-1, A-9). Un mensaje en español (#202, DT-55) pasa por la baja, la PII y
fair housing, y después recibe el aviso de idioma sin llegar al LLM, a la FSM ni al score.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Mapping, Sequence

from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool

from app import alerts, facts, reminders, scheduling_client, summary, visits
from app.agent import Agent, AgentResult, FakeAgent, TurnInput
from app.alternatives import Alternatives
from app.availability import AvailabilityGuard
from app.bedrock_llm import Classification
from app.demand import SearchLog, UnmetSearch
from app.fakes.fake_core import FakeCore
from app.fakes.fake_llm import FakeLLM
from app.fsm import (
    HUMAN_INTENTS,
    HUMAN_REQUESTED,
    TERMINAL_STATES,
    VISIT_STATES,
    next_state,
    trigger_for,
)
from app.handoff import FRUSTRATION_REPLY, handoff_reason, is_frustrated, next_streak
from app.input_guard import (
    HANDOFF_OFFER,
    OMITTED_MESSAGE,
    OPT_OUT_REPLY,
    OUT_OF_DOMAIN_OFFER_AFTER,
    OUT_OF_DOMAIN_REPLY,
    PII_REMINDER,
    REDACTED,
    WITHDRAWN_REPLY,
    InputVerdict,
    inspect_input,
)
from app.interest import (
    UNCLEAR,
    focused_property,
    interactions,
    listing_greeting,
    property_of_interest,
    remember_property,
    shown_properties,
    zone_of_interest,
)
from app.language_guard import NOT_ENGLISH_REPLY
from app.lead_scoring import TurnEvidence, qualification_known, score_turn
from app.qualification import just_qualified
from app.scheduler import restart_silence
from app.scoring import Score
from app.skeleton import Catalog, respond
from app.state_store import GuardContext, StateStore
from app.tracing import SPAN_KIND, get_tracer
from app.visits import OFFER_KEY, VISIT_KEY, VisitStep
from db.session import session_scope

logger = logging.getLogger("core.turn")

Audit = tuple[str, Mapping[str, Any]]
SavedMessage = tuple[str, str, list[dict[str, Any]] | None]  # (rol, contenido, tools usadas)

DEFAULT_AGENT = FakeAgent()


@dataclass(frozen=True)
class Transition:
    before: str
    after: str
    trigger: str | None
    score: Score
    unresolved_streak: int = 0
    qualified: bool = False  # True solo en el turno en que el lead pasó a calificado (B-7)
    handoff_reason: str | None = None  # solo en el turno en que llegó a HANDOFF/HUMAN_HANDOFF (A-12)


@dataclass(frozen=True)
class TurnReply:
    """El turno tal como lo ve el canal (B-10, DT-50): el evento CT-1 de salida y lo que no viaja en él.

    `property` es la propiedad de la que trata el turno cuando es una sola (para la foto); `fsm_state`,
    el estado en que quedó la conversación. El `POST /events` solo devuelve `outbound`: CT-1 no cambia.

    `after_reply` es lo que queda por hacer después de responderle al lead (#231): el aviso de calificación
    (B-7), el resumen del traspaso (A-16) y su ficha (A-12). Quien envía la respuesta lo espera después.
    """
    outbound: dict[str, Any]
    property: Mapping[str, Any] | None = None
    fsm_state: str | None = None
    after_reply: Callable[[], Awaitable[None]] | None = None


PHOTO_KEY = "photo_shown"  # en `last_tool_results`: id de la última propiedad cuya foto vio el lead (#210)

OnQualified = Callable[[Mapping[str, Any], "Transition"], Awaitable[None]]
OnHandoff = Callable[[Mapping[str, Any], "Transition"], Awaitable[None]]


def _describe(span: Any, transition: "Transition") -> None:
    """Estado de la FSM y score de un turno, como atributos del span (sin ningún dato del lead)."""
    span.set_attributes({
        "fsm.state_before": transition.before, "fsm.state_after": transition.after,
        "fsm.transitioned": transition.before != transition.after, "fsm.trigger": transition.trigger or "",
        "fsm.unresolved_streak": transition.unresolved_streak,
        "score.total": transition.score.total, "score.band": transition.score.band,
    })


def _ids(event: Mapping[str, Any]) -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.UUID(str(event["conversation_id"])), uuid.UUID(str(event["lead_id"]))


def _write_audits(store: StateStore, event: Mapping[str, Any], audits: Sequence[Audit]) -> None:
    for action, detail in audits:
        store.audit(action, {"conversation_id": str(event["conversation_id"]),
                             "lead_id": str(event["lead_id"]), "event_id": str(event["event_id"]), **detail},
                    actor="turn" if action == DECISION else "input_guard")


# Por qué contestó así el turno (paso 0 del plan de confiabilidad): lo que entendió el clasificador y quién escribió
# la respuesta. No es una detección del guardrail: va con otro `actor`, y sin nada que haya escrito el lead.
DECISION = "turn.decision"


def _decision(result: AgentResult, visit: VisitStep | None, frustrated: bool, alerted: bool) -> dict[str, Any]:
    """`route`: quién escribió lo que leyó el lead. `agent_route`: qué hizo el agente antes; si difieren, el código
    reemplazó su respuesta (p. ej. `agent` -> `agenda:offer`)."""
    route = ("frustration" if frustrated else f"agenda:{visit.kind or '?'}" if visit is not None
             else "alert" if alerted else result.route)
    return {"route": route, "agent_route": result.route, "intent": result.intent, "classified": result.classified,
            "flags": dict(result.flags),
            "refers_to": result.refers_to, "visit_when": result.visit_when}


def read_guard_context(engine: Engine, event: Mapping[str, Any]) -> GuardContext:
    conversation_id, lead_id = _ids(event)
    with session_scope(engine) as session:
        return StateStore(session).guard_context(conversation_id, lead_id)


def advance(engine: Engine, event: Mapping[str, Any], intent: str,
            evidence: TurnEvidence | None = None, audits: Sequence[Audit] = (),
            counts_as_unresolved: bool = True, messages: Sequence[SavedMessage] = (),
            visit: VisitStep | None = None, unmet_searches: Sequence[UnmetSearch] = (),
            alert: Mapping[str, Any] | None = None, annoyed: bool | None = None) -> Transition:
    """Carga y bloquea el estado, aplica el trigger, puntúa, audita y guarda, en una transacción.

    `messages` son los mensajes del turno (lead y asistente): van en la misma transacción, así el
    historial nunca queda desfasado del estado.

    `counts_as_unresolved=False` para los turnos que fuerza el guardrail de entrada: no suman a la
    racha de frustración. `visit` es el paso de agenda del turno (B-8), si lo hubo. `unmet_searches` son
    las búsquedas del turno que no encontraron nada (N-5). `alert`, los filtros de la alerta que pidió el
    lead (A-18). `annoyed` es el campo `frustrated` del clasificador: si dice que no, la racha sola no traspasa (#247).
    """
    conversation_id, lead_id = _ids(event)
    with get_tracer().start_as_current_span("fsm.advance") as span, session_scope(engine) as session:
        span.set_attribute(SPAN_KIND, "CHAIN")
        store = StateStore(session)
        state = store.lock(conversation_id, lead_id)
        evidence = evidence or TurnEvidence()
        settled = visit is not None and visit.settled
        booked = visit is not None and visit.booked
        streak = next_streak(state.unresolved_streak, intent, counts_as_unresolved, evidence.raw_signal,
                             resolved=settled)
        signal, score = score_turn(state.score_signal, evidence)
        # La FSM avanza por lo que pasó en el turno (#229, DT-61): la agenda arrancó, se buscó o se miró una
        # propiedad, o la señal ya tiene datos de calificación.
        explored = evidence.listing is not None or any(
            tool in evidence.tool_results for tool in ("search_properties", "get_property"))
        trigger = trigger_for(state.fsm_state, intent, frustrated=is_frustrated(streak, annoyed),
                              visit_settled=settled, visit_started=visit is not None and state.fsm_state in VISIT_STATES,
                              explored=explored, qualifying=qualification_known(signal))
        after = next_state(state.fsm_state, trigger) if trigger else state.fsm_state
        if after != state.fsm_state:
            store.save_state(conversation_id, after)
        if trigger == HUMAN_REQUESTED:
            streak = 0  # la racha ya cumplió su función
        store.save_streak(conversation_id, streak)
        if booked:
            store.mark_visit_scheduled(lead_id)
            reminders.schedule(session, lead_id, conversation_id, visit.tool_results[VISIT_KEY], datetime.now(UTC))
        store.save_scoring(conversation_id, lead_id, signal, dict(evidence.tool_results), score)
        zone = zone_of_interest(evidence.tool_results)
        if zone:
            store.set_zone_if_unknown(lead_id, zone)
        booked_property = visit.tool_results[VISIT_KEY].get("property") if booked else None
        store.record_interest(lead_id, interactions(evidence.tool_results, evidence.listing, booked_property))
        if event.get("utm_campaign"):
            store.set_campaign_if_unknown(lead_id, str(event["utm_campaign"]))
        store.record_unmet_searches(unmet_searches)
        if alert:
            alerts.create(session, lead_id, conversation_id, alert)
        restart_silence(session, conversation_id, lead_id, after)  # A-17: el lead escribió
        previous_band = state.score["band"] if state.score else None
        qualified = just_qualified(previous_band, score.band) and store.qualify(conversation_id, lead_id)
        _write_audits(store, event, audits)
        store.add_messages(conversation_id, messages)
        store.touch(conversation_id)
        reason = handoff_reason(after, trigger, intent, visit_booked=booked)
        transition = Transition(before=state.fsm_state, after=after, trigger=trigger, score=score,
                                unresolved_streak=streak, qualified=qualified, handoff_reason=reason)
        _describe(span, transition)
        if reason:
            span.set_attribute("fsm.handoff_reason", reason)
        span.set_attribute("guardrail.audits", [action for action, _ in audits if action != DECISION])
        return transition


def withdraw(engine: Engine, event: Mapping[str, Any], audits: Sequence[Audit]) -> None:
    """Baja del lead (`/stop`): marca `consent_status=withdrawn` y lo audita. Sin FSM ni score."""
    conversation_id, lead_id = _ids(event)
    with session_scope(engine) as session:
        store = StateStore(session)
        store.lock(conversation_id, lead_id)
        store.withdraw_consent(lead_id)
        _write_audits(store, event, audits)
        store.touch(conversation_id)


def _out_of_domain_reply(context: GuardContext) -> str:
    """Respuesta acotada; si el lead insiste, ofrece un asesor (la oferta no mueve la FSM)."""
    insisting = context.out_of_domain_count + 1 >= OUT_OF_DOMAIN_OFFER_AFTER
    return OUT_OF_DOMAIN_REPLY + (HANDOFF_OFFER if insisting else "")


def _raw_signal(verdict: InputVerdict, from_agent: Mapping[str, Any]) -> dict[str, Any]:
    if verdict.jailbreak:
        return {}  # un mensaje adversarial no aporta señal al score
    raw = dict(from_agent)
    if REDACTED in str(raw.get("budget_hint") or ""):
        raw["budget_hint"] = None  # un dato sensible omitido no es un presupuesto
    return raw


async def _listing(event: Mapping[str, Any], catalog: Any, lead_qualified: bool) -> dict[str, Any] | None:
    """La propiedad del anuncio por el que llegó el lead (`?start=prop_<id>`, #122), o None.

    La consulta el código, no el LLM: así la zona y el `need_fit` no dependen de que el modelo decida
    buscarla. Si no existe o el catálogo falla, el turno sigue como si el evento no la trajera.
    """
    property_id = event.get("property_id")
    if not property_id:
        return None
    try:
        listing = await catalog.call("get_property", {"property_id": property_id, "lead_qualified": lead_qualified})
    except Exception:
        logger.warning("no se pudo consultar la propiedad del anuncio", exc_info=True)
        return None
    return listing if listing and listing.get("id") and listing.get("title") else None


def _with_listing(result: AgentResult, listing: dict[str, Any], greet: bool) -> AgentResult:
    """Suma la propiedad del anuncio a los resultados del turno; la que haya consultado el agente manda.
    En el *deep-link* sin texto, el saludo nombra la propiedad."""
    tool_results = {"get_property": listing, **result.tool_results}
    return replace(result, tool_results=tool_results, text=listing_greeting(listing) if greet else result.text)


def _featured_property(result: AgentResult, listing: Mapping[str, Any] | None, visit: VisitStep | None,
                       replaced: bool) -> Mapping[str, Any] | None:
    """La propiedad de la que trata la respuesta, si es una sola (B-10), o None.

    Cuenta la del anuncio (*deep-link*) o la que consultó el agente con `get_property`, salvo que en
    el mismo turno también haya buscado: entonces la respuesta habla de varias. No cuenta si el código
    reemplazó la respuesta (agenda, alerta, traspaso a humano, frustración): ya no habla de la propiedad.
    """
    prop = result.tool_results.get("get_property")
    if visit is not None or replaced or result.intent in HUMAN_INTENTS:
        return None
    if listing is None and "search_properties" in result.tool_results:
        return None
    return prop if isinstance(prop, Mapping) and prop.get("id") else None


def _first_sight(featured: Mapping[str, Any] | None, tool_results: Mapping[str, Any],
                 previous: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    """La propiedad del turno solo si el lead todavía no vio su foto (#210), y `tool_results` con la
    última que vio (`PHOTO_KEY`), para el turno siguiente.

    El agente vuelve a llamar a `get_property` cada vez que cita una cifra (A-5), así que la foto se
    repetía en cada respuesta sobre la misma propiedad. Vuelve a salir cuando la respuesta pasa a otra.
    No sirve la propiedad recordada (`REMEMBERED_KEY`): también la fija una búsqueda, que no lleva foto.
    """
    shown = previous.get(PHOTO_KEY)
    if featured is not None and featured.get("id") == shown:
        featured = None
    elif featured is not None:
        shown = featured["id"]
    return featured, ({**tool_results, PHOTO_KEY: shown} if shown else dict(tool_results))


async def _visit_step(event: Mapping[str, Any], context: GuardContext, result: AgentResult,
                      text: str, catalog: Catalog, shown: list[dict[str, Any]]) -> VisitStep | None:
    """El paso de agenda de este turno (B-8), o None si el turno no tiene que ver con la visita.

    Pedir la visita la arranca desde cualquier estado activo (#229, DT-61): se ofrecen horarios de la propiedad
    de interés. Si no hay una, contesta el agente, que pregunta cuál. En `SCHEDULE` se lee la respuesta del lead:
    si no contesta la oferta, contesta el agente y la oferta sigue abierta (#247). Si la oferta se perdió (p. ej.
    un turno forzado por el guardrail no la arrastró), se vuelve a ofrecer. Si el lead nombra otra de las
    propiedades mostradas (`refers_to`), la oferta es de esa.

    La propiedad elegida por `refers_to` y la recordada vienen de la memoria, sin estado: se consulta antes de
    ofrecer horarios, y una apartada o cerrada se contesta con texto fijo.
    """
    state, previous = context.fsm_state, context.last_tool_results
    focus = focused_property(result.refers_to, shown)
    prop = _visit_property(result.tool_results, previous, result.refers_to, shown)
    if not _schedules(state, result.intent, result.flags, prop, previous.get(OFFER_KEY), text, result.visit_when):
        return None
    conversation_id, lead_id = (str(value) for value in _ids(event))
    agenda, today = scheduling_client.get_scheduling(), visits.today_local()
    offer = previous.get(OFFER_KEY) if state == "SCHEDULE" else None
    offered_id = ((offer or {}).get("property") or {}).get("id") if isinstance(offer, Mapping) else None
    if isinstance(offer, Mapping) and (focus is None or focus["id"] == offered_id):
        return await visits.follow_up(agenda, conversation_id, lead_id, offer, text, today, result.visit_when)
    # Sin estado vienen la elegida por `refers_to` y la recordada: las dos salen de la memoria. En la prueba de
    # jurado (O6), desde el anuncio de una casa apartada, "something similar that I can visit" ofrecía esa casa.
    if prop is not None and prop.get("id") and prop.get("title") and not prop.get("status"):
        status = await _current_status(catalog, prop["id"])
        if status not in (None, "disponible"):
            return VisitStep(visits.not_visitable(prop, status), {}, kind="not_visitable")
    return await visits.start(agenda, lead_id, prop, today, result.visit_when)


async def _current_status(catalog: Catalog, prop_id: str) -> str | None:
    """El estado de la propiedad según el catálogo, o None si no se pudo consultar (se sigue como hasta ahora)."""
    try:
        found = await catalog.call("get_availability", {"property_id": prop_id})
    except Exception:
        logger.warning("no se pudo consultar el estado de la propiedad antes de ofrecer la visita")
        return None
    status = found.get("status") if isinstance(found, Mapping) else None
    return status if isinstance(status, str) else None


def _visit_property(tool_results: Mapping[str, Any], previous: Mapping[str, Any], about: str | None = None,
                    shown: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """La propiedad que se agenda: la mostrada de la que habla el mensaje según el clasificador (`about`), la que
    se miró en el turno (o la del anuncio), la de interés de los turnos anteriores o, si el turno solo buscó, el
    único resultado. Con varios resultados no se adivina cuál (#247: "the cheapest one" agendaba el primero de la
    búsqueda), tampoco turnos después ("ok i wan to see it"); contesta el agente, que puede mirar la propiedad y
    agendarla en el mismo turno. Si el clasificador no sabe cuál (`UNCLEAR`), tampoco: el agente pregunta."""
    if about == UNCLEAR:
        return None
    looked = tool_results.get("get_property")
    focus = focused_property(about, shown or [])
    if focus is not None:
        same = isinstance(looked, Mapping) and looked.get("id") == focus["id"]
        return dict(looked) if same else focus
    if isinstance(looked, Mapping) and looked.get("id"):
        return dict(looked)
    remembered = property_of_interest(previous, sure=True)
    if remembered:
        return remembered
    search = tool_results.get("search_properties")
    found = search.get("properties") if isinstance(search, Mapping) else None
    if isinstance(found, list) and len(found) == 1 and isinstance(found[0], Mapping):
        return dict(found[0])
    return None


def _schedules(state: str, intent: str, flags: Mapping[str, bool], prop: Mapping[str, Any] | None,
               offer: Any, text: str, when: Mapping[str, Any] | None = None) -> bool:
    """El turno lo contesta la agenda.

    - En `SCHEDULE`: si el mensaje contesta la oferta (también diciendo cuándo prefiere, `when`) o vuelve a pedir
      la visita, o si la oferta se perdió.
    - En los demás estados activos: si el lead pidió la visita y hay una propiedad de interés que se puede
      visitar (una apartada o cerrada no: contesta el agente, #247).

    El pedido lo dice el campo `wants_visit` del clasificador (#243) y, si no lo contestó, la intención. En la
    prueba de jurado, "Can I reserve the 4-bedroom one?" salía `agendar_visita` y ofrecía horarios de otra casa.
    """
    wants = flags.get("wants_visit")
    asks = wants if isinstance(wants, bool) else intent == "agendar_visita"
    if state == "SCHEDULE":
        return asks or not isinstance(offer, Mapping) or visits.answers(offer, text, when)
    visitable = prop is not None and prop.get("status") in (None, "disponible")
    return state in VISIT_STATES and asks and visitable


def _answered_by_code(context: GuardContext, listing: Mapping[str, Any] | None, text: str,
                      shown: list[dict[str, Any]]) -> Callable[[Classification], bool]:
    """Para el agente: si la respuesta de este turno la va a dar la agenda, solo hace falta clasificar (#229).
    Es la misma regla que `_visit_step`, con la propiedad que este tendrá: la mostrada de la que habla el mensaje,
    la del anuncio o la recordada."""
    previous = context.last_tool_results
    looked = {"get_property": listing} if listing else {}

    def answered(classification: Classification) -> bool:
        prop = _visit_property(looked, previous, classification.refers_to, shown)
        return _schedules(context.fsm_state, classification.intent, classification.flags, prop,
                          previous.get(OFFER_KEY), text, classification.when)
    return answered


def _alert_step(context: GuardContext, result: AgentResult, searches: SearchLog | None,
                text: str) -> tuple[AgentResult, dict[str, Any] | None]:
    """La alerta de propiedades nuevas (A-18): la respuesta del turno y los filtros a guardar, si el lead la pidió.

    Si la pide, se usan los filtros de la búsqueda de este turno o, si no buscó, los de la oferta del turno
    anterior. Si no la pide y la búsqueda trajo pocas propiedades, se guardan sus filtros y se le ofrece,
    solo si no se le ofreció antes en la conversación (#213).
    """
    current = searches.asked_search if searches else None
    if alerts.requested(text, result.flags):
        filters = (current[0] if current and alerts.has_filters(current[0])
                   else context.last_tool_results.get(alerts.ALERT_KEY))
        if alerts.has_filters(filters):
            return replace(result, text=alerts.CREATED_TEXT), dict(filters)
        return replace(result, text=alerts.NO_SEARCH_TEXT), None
    if current and current[1] <= alerts.FEW_RESULTS and alerts.has_filters(current[0]):
        offered = context.last_tool_results.get(alerts.OFFERED_KEY)
        return replace(result, text=result.text if offered else f"{result.text}\n\n{alerts.OFFER_TEXT}",
                       tool_results={**result.tool_results, alerts.ALERT_KEY: current[0],
                                     alerts.OFFERED_KEY: True}), None
    return result, None


def _lead_message(verdict: InputVerdict, original: str) -> list[SavedMessage]:
    """El mensaje del lead tal como se guarda: nunca el original. Sin PII ni categorías protegidas, y un
    intento de *jailbreak* no se guarda para que no vuelva al LLM como historial."""
    if not original.strip():
        return []  # p. ej. un deep-link sin texto
    if verdict.jailbreak or not verdict.signal_text.strip():
        return [("user", OMITTED_MESSAGE, None)]
    return [("user", verdict.signal_text, None)]


async def _run_turn(engine: Engine, event: Mapping[str, Any], catalog: Catalog, span: Any,
                    agent: Agent, on_qualified: OnQualified | None = None,
                    on_handoff: OnHandoff | None = None) -> TurnReply:
    FakeCore().validate(event)  # evento incompleto o no inbound -> ValueError (422)
    with get_tracer().start_as_current_span("guardrail.input") as guard:
        verdict = inspect_input(event["text"])
        guard.set_attributes({
            SPAN_KIND: "GUARDRAIL", "guardrail.opt_out": verdict.opt_out, "guardrail.jailbreak": verdict.jailbreak,
            "guardrail.pii_types": list(verdict.pii), "guardrail.out_of_domain": verdict.out_of_domain,
            "guardrail.protected_category": verdict.protected_category,
            "guardrail.not_english": verdict.not_english,
        })
    context = await run_in_threadpool(read_guard_context, engine, event)
    audits = verdict.audit_kinds()
    now = datetime.now(UTC)  # la hora con que vencen los hechos verificados (#217)

    # /stop: determinista, sin LLM. Un lead ya dado de baja tampoco recibe más flujo.
    if verdict.opt_out:
        span.set_attribute("turn.outcome", "opt_out")
        await run_in_threadpool(withdraw, engine, event, audits)
        return TurnReply((await respond(event, catalog, intent="otro", text=OPT_OUT_REPLY)).outbound)
    if context.consent_status == "withdrawn":
        span.set_attribute("turn.outcome", "withdrawn")
        return TurnReply((await respond(event, catalog, intent="otro", text=WITHDRAWN_REPLY)).outbound)

    span.set_attribute("turn.outcome", "not_english" if verdict.not_english
                       else "out_of_domain" if verdict.out_of_domain
                       else "jailbreak" if verdict.jailbreak else "normal")
    listing, searches, shown = None, None, []
    if verdict.not_english:
        # Se pide repetirlo en inglés; como fuera de dominio, no mueve la FSM ni cuenta como frustración.
        result = AgentResult(text=NOT_ENGLISH_REPLY, intent="otro", route="input_guard")
    elif verdict.out_of_domain:
        result = AgentResult(text=_out_of_domain_reply(context), intent="otro", route="input_guard")
    elif verdict.jailbreak:
        # Se contesta como una pregunta de dominio cualquiera: no se refleja la instrucción, no se
        # llama al LLM y no dispara efectos (traspaso a humano, búsquedas).
        result = AgentResult(text=FakeLLM().generate_reply("otro"), intent="otro", route="input_guard")
    else:
        # El agente solo ve el texto filtrado y el historial que guardó este mismo servicio.
        text = verdict.signal_text if verdict.signal_text.strip() or not event["text"].strip() else OMITTED_MESSAGE
        # Por debajo del guardrail: cuenta lo que contestó el catálogo a lo que se buscó (N-5), sin las
        # búsquedas con un filtro menos que traen las alternativas (#213).
        searches = SearchLog(Alternatives(catalog))
        guarded = AvailabilityGuard(searches)
        listing = await _listing(event, guarded, context.lead_qualified)
        shown = shown_properties(facts.evidence(context.last_tool_results, now), listing)
        result = await agent.run(TurnInput(event=event, text=text, history=context.history,
                                           lead_qualified=context.lead_qualified, listing=listing,
                                           protected_removed=verdict.protected_category,
                                           remembered=facts.evidence(context.last_tool_results, now),
                                           answered_by_code=_answered_by_code(context, listing,
                                                                              verdict.signal_text, shown)),
                                 guarded)
        if listing:
            result = _with_listing(result, listing, greet=not event["text"].strip())
    counts = not (verdict.jailbreak or verdict.out_of_domain or verdict.not_english)
    visit = None
    if counts and result.intent not in HUMAN_INTENTS:
        try:
            visit = await _visit_step(event, context, result, verdict.signal_text, catalog, shown)
        except Exception:
            logger.exception("fallo el paso de agenda; se pide el horario preferido")
            visit = visits.ask_preferred(property_of_interest(context.last_tool_results), "fallback_error")
        if visit is None and not result.text:
            # El agente dejó la respuesta a la agenda (#229) y la agenda no contestó: no se manda un mensaje vacío.
            visit = visits.ask_preferred(property_of_interest(context.last_tool_results), "fallback_empty")
        if visit is not None:
            # La agenda la maneja el código (DT-32): su respuesta reemplaza la del agente.
            result = replace(result, text=visit.reply, tool_results={**result.tool_results, **visit.tool_results})
    offer = context.last_tool_results.get(OFFER_KEY)
    if visit is None and isinstance(offer, Mapping):
        # Un turno que no responde a la oferta (forzado por el guardrail, o una pregunta que contesta el agente,
        # #247) tampoco la borra; si contesta el agente, se le recuerda al lead cómo elegir.
        text = result.text
        if counts and context.fsm_state == "SCHEDULE" and result.intent not in HUMAN_INTENTS:
            text = f"{text}\n\n{visits.reminder(offer)}"
        result = replace(result, text=text, tool_results={**result.tool_results, OFFER_KEY: offer})
    alert, before_alert = None, result.text
    if visit is None and counts and result.intent not in HUMAN_INTENTS:
        result, alert = _alert_step(context, result, searches, verdict.signal_text)
    alerted = result.text != before_alert and result.text in (alerts.CREATED_TEXT, alerts.NO_SEARCH_TEXT)
    outbound = FakeCore().handle_event(event, intent=result.intent, text=result.text)
    if verdict.pii:
        outbound["text"] += " " + PII_REMINDER
    remembered = facts.carry(remember_property(result.tool_results, context.last_tool_results,
                                                focused_property(result.refers_to, shown)),
                             result.tool_results, context.last_tool_results, now)
    evidence = TurnEvidence(raw_signal=_raw_signal(verdict, result.raw_signal), listing=listing,
                            tool_results=alerts.carry_offered(remembered, context.last_tool_results))
    streak = next_streak(context.unresolved_streak, result.intent, raw_signal=evidence.raw_signal,
                         resolved=visit is not None and visit.settled)
    frustrated = (counts and context.fsm_state not in TERMINAL_STATES and result.intent not in HUMAN_INTENTS
                  and is_frustrated(streak, result.flags.get("frustrated")))
    if frustrated:
        outbound["text"] = FRUSTRATION_REPLY  # esta racha dispara el traspaso: se le avisa al lead
    replaced = frustrated or alerts.requested(verdict.signal_text, result.flags)  # la alerta (A-18) cambia la respuesta
    featured, shown = _first_sight(_featured_property(result, listing, visit, replaced),
                                   evidence.tool_results, context.last_tool_results)
    evidence = replace(evidence, tool_results=shown)

    decision = _decision(result, visit, frustrated, alerted)
    span.set_attributes({"turn.intent": result.intent, "turn.tools": sorted(result.tool_results),
                         "turn.agent": agent.name, "turn.llm_error": result.llm_error or "",
                         "turn.output_guard": [action for action, _ in result.guard_audits],
                         "turn.route": decision["route"], "turn.agent_route": decision["agent_route"],
                         "turn.refers_to": result.refers_to or "",
                         "turn.flags": sorted(name for name, value in result.flags.items() if value)})
    saved = [*_lead_message(verdict, event["text"]), ("assistant", outbound["text"], result.tool_calls or None)]
    # A-5: la factualidad de salida se audita igual que la entrada; después, por qué contestó así el turno.
    audits = [*audits, *result.guard_audits, (DECISION, decision)]
    transition = await run_in_threadpool(advance, engine, event, result.intent, evidence, audits, counts, saved,
                                         visit, searches.unmet if searches else (), alert,
                                         result.flags.get("frustrated"))
    _describe(span, transition)
    span.set_attributes({"lead.qualified_now": transition.qualified,
                         "lead.handoff_now": transition.handoff_reason is not None})
    featured = None if transition.after in TERMINAL_STATES else featured
    pending = (transition.qualified and on_qualified is not None) or transition.handoff_reason is not None

    async def after_reply() -> None:
        await _after_reply(engine, event, transition, agent, on_qualified, on_handoff)

    return TurnReply(outbound, featured, transition.after, after_reply if pending else None)


async def _after_reply(engine: Engine, event: Mapping[str, Any], transition: Transition, agent: Agent,
                       on_qualified: OnQualified | None, on_handoff: OnHandoff | None) -> None:
    """Lo que el turno deja para después de responder (#231). El resumen es una llamada más al LLM, que puede
    esperar cupo y reintentos: antes, el lead esperaba todo esto justo en el turno del traspaso.

    El turno ya está commiteado (`advance` cerró su transacción): lo que falle acá no lo toca.
    """
    with get_tracer().start_as_current_span("turn.after_reply") as span:
        span.set_attributes({SPAN_KIND: "CHAIN", "conversation.id": str(event["conversation_id"]),
                             "lead.qualified_now": transition.qualified,
                             "lead.handoff_now": transition.handoff_reason is not None})
        if transition.qualified and on_qualified is not None:
            try:
                await on_qualified(event, transition)
            except Exception:
                logger.exception("fallo al notificar la calificación del lead")
        if transition.handoff_reason:
            # A-16: el resumen para el asesor, antes de la ficha del traspaso que lo lleva.
            try:
                conversation_id, lead_id = _ids(event)
                written = await summary.generate(engine, lead_id, conversation_id, transition.handoff_reason, agent)
                span.set_attribute("handoff.summary", written is not None)
            except Exception:
                logger.exception("fallo al generar el resumen del traspaso")
        if transition.handoff_reason and on_handoff is not None:
            try:
                await on_handoff(event, transition)
            except Exception:
                logger.exception("fallo al enviar la ficha del traspaso")


async def process_turn(engine: Engine, event: Mapping[str, Any], catalog: Catalog,
                       agent: Agent | None = None, on_qualified: OnQualified | None = None,
                       on_handoff: OnHandoff | None = None) -> dict[str, Any]:
    """Un turno completo; devuelve el evento CT-1 de salida. Ver `process_turn_reply`.

    Sin canal que envíe la respuesta antes (el `POST /events` del arnés), lo de después se hace acá mismo.
    """
    turn = await process_turn_reply(engine, event, catalog, agent, on_qualified, on_handoff)
    if turn.after_reply is not None:
        await turn.after_reply()
    return turn.outbound


async def process_turn_reply(engine: Engine, event: Mapping[str, Any], catalog: Catalog,
                             agent: Agent | None = None, on_qualified: OnQualified | None = None,
                             on_handoff: OnHandoff | None = None) -> TurnReply:
    """Un turno completo dentro de un span `turn`. El span no lleva el texto del mensaje.

    `on_qualified` se invoca, después del commit, solo en el turno en que el lead pasa a calificado
    (B-7); `on_handoff`, solo en el turno en que la FSM llega a `HANDOFF`/`HUMAN_HANDOFF` (A-12).
    Los pasa el canal; el `POST /events` del arnés no, así los E2E nunca notifican a nadie. No se invocan
    acá: van en `TurnReply.after_reply`, que el canal espera después de enviar la respuesta (#231).
    """
    with get_tracer().start_as_current_span("turn") as span:
        span.set_attributes({
            SPAN_KIND: "CHAIN", "conversation.id": str(event.get("conversation_id", "")),
            "lead.id": str(event.get("lead_id", "")), "event.id": str(event.get("event_id", "")),
            "channel": str(event.get("channel", "")),
        })
        started = time.perf_counter()
        try:
            return await _run_turn(engine, event, catalog, span, agent or DEFAULT_AGENT, on_qualified,
                                   on_handoff)
        finally:
            span.set_attribute("turn.latency_ms", round((time.perf_counter() - started) * 1000, 1))
