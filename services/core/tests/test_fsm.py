"""Tests de A-1: la FSM pura cubre todas las aristas de design.md §2 (sin base de datos)."""
import itertools

import pytest

from app import fsm
from app.fsm import (
    DISCOVERY_COMPLETE,
    GREETED,
    HUMAN_REQUESTED,
    LEAD_RETURNED,
    NO_RESPONSE,
    QUALIFIED,
    TERMINAL_STATES,
    TRANSITIONS,
    VISIT_REQUESTED,
    VISIT_SCHEDULED,
    InvalidTransition,
    next_state,
    trigger_for,
)
from db.models import FSM_STATES

# Las aristas del diagrama de design.md §2, una por una.
DIAGRAM_EDGES = [
    ("GREETING", GREETED, "DISCOVERY"),
    ("DISCOVERY", DISCOVERY_COMPLETE, "QUALIFY"),
    ("QUALIFY", QUALIFIED, "RECOMMEND"),
    ("RECOMMEND", VISIT_REQUESTED, "SCHEDULE"),
    ("SCHEDULE", VISIT_SCHEDULED, "HANDOFF"),
    ("DISCOVERY", NO_RESPONSE, "NURTURE"),
    ("NURTURE", LEAD_RETURNED, "DISCOVERY"),
    ("NURTURE", NO_RESPONSE, "LOST"),
    ("GREETING", HUMAN_REQUESTED, "HUMAN_HANDOFF"),
    ("DISCOVERY", HUMAN_REQUESTED, "HUMAN_HANDOFF"),
    ("QUALIFY", HUMAN_REQUESTED, "HUMAN_HANDOFF"),
    ("RECOMMEND", HUMAN_REQUESTED, "HUMAN_HANDOFF"),
    ("SCHEDULE", HUMAN_REQUESTED, "HUMAN_HANDOFF"),
]


@pytest.mark.parametrize("state,trigger,expected", DIAGRAM_EDGES)
def test_every_diagram_edge(state, trigger, expected):
    assert next_state(state, trigger) == expected


def test_table_has_no_edges_beyond_the_diagram_except_human_from_nurture():
    extra = set(TRANSITIONS) - {(s, t) for s, t, _ in DIAGRAM_EDGES}
    assert extra == {("NURTURE", HUMAN_REQUESTED)}


@pytest.mark.parametrize("state", [s for s in FSM_STATES if s not in TERMINAL_STATES])
def test_human_handoff_is_reachable_from_every_non_terminal_state(state):
    assert next_state(state, HUMAN_REQUESTED) == "HUMAN_HANDOFF"


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_terminal_states_have_no_way_out(state):
    for trigger in fsm.TRIGGERS:
        with pytest.raises(InvalidTransition):
            next_state(state, trigger)


@pytest.mark.parametrize("state,trigger", [
    pair for pair in itertools.product(FSM_STATES, sorted(fsm.TRIGGERS)) if pair not in TRANSITIONS
])
def test_every_missing_edge_is_rejected(state, trigger):
    with pytest.raises(InvalidTransition) as exc:
        next_state(state, trigger)
    assert exc.value.state == state and exc.value.trigger == trigger


def test_all_states_are_known_and_reachable_from_greeting():
    assert {s for s, _ in TRANSITIONS} | set(TRANSITIONS.values()) <= set(FSM_STATES)
    seen, frontier = {"GREETING"}, ["GREETING"]
    while frontier:
        current = frontier.pop()
        for (source, _), target in TRANSITIONS.items():
            if source == current and target not in seen:
                seen.add(target)
                frontier.append(target)
    assert seen == set(FSM_STATES)


# --- qué mensajes mueven la FSM ----------------------------------------------------------------

@pytest.mark.parametrize("state,intent,expected", [
    ("GREETING", "saludo", GREETED),
    ("GREETING", "otro", GREETED),
    ("DISCOVERY", "busqueda_propiedad", DISCOVERY_COMPLETE),
    ("DISCOVERY", "saludo", None),
    ("QUALIFY", "consulta_precio", QUALIFIED),
    ("QUALIFY", "otro", None),
    ("RECOMMEND", "agendar_visita", VISIT_REQUESTED),
    ("RECOMMEND", "consulta_precio", None),
    ("SCHEDULE", "agendar_visita", None),  # B-8: la agenda la resuelve el código, no una intención
    ("SCHEDULE", "saludo", None),
    ("NURTURE", "saludo", LEAD_RETURNED),
    ("NURTURE", "busqueda_propiedad", LEAD_RETURNED),
])
def test_trigger_for(state, intent, expected):
    assert trigger_for(state, intent) == expected


@pytest.mark.parametrize("intent", ["agendar_visita", "otro", "saludo"])
def test_schedule_moves_only_when_the_code_settles_the_visit(intent):
    """B-8: en SCHEDULE la respuesta del lead ("la 2") no tiene intención propia; decide la agenda."""
    assert trigger_for("SCHEDULE", intent, visit_settled=True) == VISIT_SCHEDULED
    assert trigger_for("SCHEDULE", intent) is None


@pytest.mark.parametrize("state", [s for s in FSM_STATES if s != "SCHEDULE"])
def test_a_settled_visit_means_nothing_outside_schedule(state):
    assert trigger_for(state, "saludo", visit_settled=True) == trigger_for(state, "saludo")


def test_a_human_request_wins_over_a_settled_visit():
    assert trigger_for("SCHEDULE", "solicitud_humano", visit_settled=True) == HUMAN_REQUESTED


@pytest.mark.parametrize("state", [s for s in FSM_STATES if s not in TERMINAL_STATES])
def test_explicit_human_request_wins_in_every_state(state):
    assert trigger_for(state, "solicitud_humano") == HUMAN_REQUESTED


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_terminal_states_ignore_every_intent(state):
    from app.fakes.fake_llm import INTENTS

    assert all(trigger_for(state, intent) is None for intent in INTENTS)


@pytest.mark.parametrize("state", FSM_STATES)
def test_catch_all_intent_never_triggers_a_human_handoff(state):
    # A-2/A-13: el cajón de sastre no debe tener un efecto de sistema real sin revisarlo.
    assert trigger_for(state, "otro") != HUMAN_REQUESTED


def test_every_trigger_produced_by_trigger_for_is_a_valid_edge():
    from app.fakes.fake_llm import INTENTS

    for state in FSM_STATES:
        for intent in INTENTS:
            trigger = trigger_for(state, intent)
            if trigger:
                next_state(state, trigger)  # no debe lanzar
