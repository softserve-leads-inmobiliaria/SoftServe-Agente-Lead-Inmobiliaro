"""Tests de A-13 (sin base de datos): los tres caminos a HUMAN_HANDOFF y el criterio del cajón de sastre."""
import pytest

from app.fakes.fake_llm import INTENTS, FakeLLM
from app.fsm import HUMAN_INTENTS, HUMAN_REQUESTED, TERMINAL_STATES, next_state, trigger_for
from app.handoff import (
    FRUSTRATION_STREAK,
    UNRESOLVED_INTENT,
    handoff_reason,
    is_frustrated,
    next_streak,
)
from db.models import FSM_STATES

NON_TERMINAL = [s for s in FSM_STATES if s not in TERMINAL_STATES]


# --- camino 1 y 2: petición explícita e intención fuera de alcance -----------------------------

@pytest.mark.parametrize("state", NON_TERMINAL)
@pytest.mark.parametrize("intent", sorted(HUMAN_INTENTS))
def test_human_intents_reach_the_handoff_from_every_non_terminal_state(state, intent):
    assert trigger_for(state, intent) == HUMAN_REQUESTED
    assert next_state(state, HUMAN_REQUESTED) == "HUMAN_HANDOFF"


def test_the_human_intents_are_exactly_the_explicit_request_and_out_of_scope():
    assert HUMAN_INTENTS == {"solicitud_humano", "fuera_de_alcance"}
    assert HUMAN_INTENTS <= set(INTENTS)


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_terminal_states_ignore_every_path(state):
    assert all(trigger_for(state, intent, frustrated=True) is None for intent in INTENTS)


# --- camino 3: racha sin resolver (proxy de frustración) ---------------------------------------

def test_the_streak_grows_with_unresolved_turns_and_resets_when_one_is_resolved():
    streak = 0
    seen = []
    for intent in ("otro", "otro", "saludo", "otro", "otro", "otro"):
        streak = next_streak(streak, intent)
        seen.append(streak)
    assert seen == [1, 2, 0, 1, 2, 3]


def test_frustration_starts_exactly_at_the_threshold():
    assert FRUSTRATION_STREAK == 3
    assert [is_frustrated(n) for n in range(5)] == [False, False, False, True, True]


def test_turns_forced_by_the_guardrail_leave_the_streak_untouched():
    assert next_streak(2, "otro", counts=False) == 2
    assert next_streak(2, "saludo", counts=False) == 2


@pytest.mark.parametrize("state", NON_TERMINAL)
def test_frustration_reaches_the_handoff_from_every_non_terminal_state(state):
    assert trigger_for(state, UNRESOLVED_INTENT, frustrated=True) == HUMAN_REQUESTED


@pytest.mark.parametrize("state", FSM_STATES)
def test_a_single_catch_all_message_never_triggers_a_human_handoff(state):
    """Criterio de A-2: `otro` es ruido (un "ok", un "gracias"); por sí solo no tiene efecto de sistema."""
    assert trigger_for(state, UNRESOLVED_INTENT) != HUMAN_REQUESTED


@pytest.mark.parametrize("state", NON_TERMINAL)
@pytest.mark.parametrize("streak", range(FRUSTRATION_STREAK))
def test_below_the_threshold_nothing_is_triggered_by_the_streak(state, streak):
    assert not is_frustrated(streak)
    assert trigger_for(state, UNRESOLVED_INTENT, frustrated=is_frustrated(streak)) != HUMAN_REQUESTED


# --- el clasificador: qué frases son fuera de alcance y cuáles NO ------------------------------

@pytest.mark.parametrize("text", [
    "Tengo una queja", "Quiero hacer un reclamo", "Ya soy cliente y tengo un problema", "Sobre mi contrato vigente",
    "No me devolvieron mi depósito", "Es sobre mi depósito",
])
def test_out_of_scope_phrases(text):
    assert FakeLLM().classify_intent(text) == "fuera_de_alcance"


@pytest.mark.parametrize("text", [
    "Quiero hablar con un asesor", "Pásame con un supervisor", "Quiero hablar con una persona",
    "Necesito hablar con el gerente", "Quiero un humano",
])
def test_explicit_requests_for_a_person(text):
    assert FakeLLM().classify_intent(text) == "solicitud_humano"


@pytest.mark.parametrize("text", [
    "Busco un departamento para una persona", "¿Incluye mantenimiento?", "¿Dan factura?",
    "¿Cuánto es el cobro de administración?", "Somos una pareja y una persona más", "Busco casa",
])
def test_ordinary_property_questions_do_not_reach_a_person(text):
    assert FakeLLM().classify_intent(text) not in HUMAN_INTENTS


def test_out_of_scope_has_a_reply_that_announces_the_handoff():
    assert "asesor" in FakeLLM().generate_reply("fuera_de_alcance")


# --- A-12: el motivo del traspaso ---------------------------------------------------------------

@pytest.mark.parametrize("intent", sorted(HUMAN_INTENTS))
def test_the_reason_of_an_explicit_handoff_is_the_intent(intent):
    assert handoff_reason("HUMAN_HANDOFF", HUMAN_REQUESTED, intent) == intent


def test_a_human_request_without_a_human_intent_is_frustration():
    assert handoff_reason("HUMAN_HANDOFF", HUMAN_REQUESTED, UNRESOLVED_INTENT) == "frustration"


def test_a_scheduled_visit_hands_the_lead_over_with_its_trigger_as_the_reason():
    assert handoff_reason("HANDOFF", "visit_scheduled", "agendar_visita") == "visit_scheduled"


@pytest.mark.parametrize("state", NON_TERMINAL)
def test_moving_between_ordinary_states_is_not_a_handoff(state):
    assert handoff_reason(state, "greeted", "saludo") is None


def test_a_turn_without_a_trigger_is_not_a_handoff_even_in_a_terminal_state():
    """Ya en HUMAN_HANDOFF los mensajes siguientes no mueven la FSM: no hay un segundo traspaso."""
    assert handoff_reason("HUMAN_HANDOFF", None, "saludo") is None
