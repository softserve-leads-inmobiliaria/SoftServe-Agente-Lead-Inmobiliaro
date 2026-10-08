"""Suite de transiciones de la FSM (OBS-3): cada arista, probada de punta a punta.

Complementa `test_fsm.py` (la tabla pura) con tres garantías contra la divergencia:
1. el diagrama de `docs/DESIGN/design.md` §2, el contrato `FsmState` y el código describen la misma FSM;
2. cada arista de la tabla tiene su prueba recorriendo el turno real y Postgres: si alguien añade una
   arista sin probarla, la suite falla (`test_every_edge_of_the_table_has_a_test`);
3. una rejilla completa estado x intención con el resultado esperado escrito a mano (no calculado con
   el propio código), que cubre también lo que NO debe moverse.
"""
import asyncio
import json
import re
import uuid

import pytest
from catalog_stub import stub_call
from sqlalchemy import text

from app.fakes.fake_llm import INTENTS
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
    next_state,
)
from app.state_store import StateStore
from app.turn import advance, process_turn
from db.models import FSM_STATES
from db.repo_root import repo_root
from db.session import session_scope

ROOT = repo_root()
INBOUND = json.loads((ROOT / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))
NON_TERMINAL = [s for s in FSM_STATES if s not in TERMINAL_STATES]


# --- 1. el diagrama, el contrato y el código describen la misma FSM --------------------------------

def _diagram_edges() -> set[tuple[str, str]]:
    design = (ROOT / "docs" / "DESIGN" / "design.md").read_text(encoding="utf-8")
    block = re.search(r"```mermaid\s+stateDiagram-v2(.*?)```", design, re.S).group(1)
    edges = set()
    for source, target in re.findall(r"^\s*(\[\*\]|\w+)\s*-->\s*(\[\*\]|\w+)", block, re.M):
        if "[*]" not in (source, target):  # inicio y fin del diagrama no son estados
            edges.add((source, target))
    return edges


def test_the_diagram_edges_are_all_in_the_code():
    code = {(source, target) for (source, _), target in TRANSITIONS.items()}
    assert _diagram_edges() <= code


def test_the_code_has_no_edge_the_diagram_does_not_show_except_the_documented_one():
    """design.md dice que HUMAN_HANDOFF sale de *cualquier* estado, pero solo dibuja cinco flechas."""
    code = {(source, target) for (source, _), target in TRANSITIONS.items()}
    assert code - _diagram_edges() == {("NURTURE", "HUMAN_HANDOFF")}


def test_the_enum_of_states_is_the_same_in_the_code_the_contract_and_the_design():
    contract = json.loads((ROOT / "contracts" / "fsm_state.schema.json").read_text(encoding="utf-8"))
    design = (ROOT / "docs" / "DESIGN" / "design.md").read_text(encoding="utf-8")
    enum_line = re.search(r"^Enum:\s*(.+)$", design, re.M).group(1)
    in_design = set(re.findall(r"[A-Z_]{4,}", enum_line))
    assert set(FSM_STATES) == set(contract["$defs"]["FsmStateName"]["enum"]) == in_design


def test_every_state_of_the_diagram_is_a_known_state():
    states = {state for edge in _diagram_edges() for state in edge}
    assert states <= set(FSM_STATES)


# --- 2. una arista, un test --------------------------------------------------------------------------

HUMAN = "I want to talk to an agent"
# (estado, trigger) -> mensaje real del lead que provoca la arista; None si la provoca el scheduler
# (fuera de A-1: se aplica el trigger directamente sobre el estado persistido).
EDGE_MESSAGES = {
    ("GREETING", GREETED): "Hi",
    ("DISCOVERY", DISCOVERY_COMPLETE): "I'm looking for an apartment",
    ("QUALIFY", QUALIFIED): "How much does it cost?",
    # #229: desde cualquier estado activo, con la propiedad del anuncio como la de interés.
    **{(state, VISIT_REQUESTED): "I'd like to schedule a visit"
       for state in ("GREETING", "DISCOVERY", "QUALIFY", "RECOMMEND", "NURTURE")},
    # B-8: en los tests la agenda no responde; el lead deja su horario preferido y la visita queda resuelta.
    ("SCHEDULE", VISIT_SCHEDULED): ("I'd like to schedule a visit", "Tomorrow at 10"),
    ("NURTURE", LEAD_RETURNED): "Hi, I'm still interested",
    ("DISCOVERY", NO_RESPONSE): None,
    ("NURTURE", NO_RESPONSE): None,
    **{(state, HUMAN_REQUESTED): HUMAN for state in NON_TERMINAL},
}


def test_every_edge_of_the_table_has_a_test():
    """Si se añade una arista a `TRANSITIONS` sin su prueba aquí, esta suite falla."""
    assert set(EDGE_MESSAGES) == set(TRANSITIONS)


class Conversation:
    def __init__(self, engine, state="GREETING"):
        self.engine = engine
        self.conversation_id, self.lead_id = str(uuid.uuid4()), str(uuid.uuid4())
        self._create()  # nace en GREETING, como una conversación nueva
        if state != "GREETING":
            self._force(state)

    def _event(self, message):
        return INBOUND | {"conversation_id": self.conversation_id, "lead_id": self.lead_id,
                          "event_id": str(uuid.uuid4()), "text": message}

    def _create(self):
        with session_scope(self.engine) as session:
            StateStore(session).lock(uuid.UUID(self.conversation_id), uuid.UUID(self.lead_id))

    def _force(self, state):
        with session_scope(self.engine) as session:
            StateStore(session).save_state(uuid.UUID(self.conversation_id), state)

    def say(self, message):
        asyncio.run(process_turn(self.engine, self._event(message), StubCatalog()))

    def fire(self, trigger):
        """Lo que hará el scheduler: aplica un trigger que no viene de un mensaje."""
        with session_scope(self.engine) as session:
            store = StateStore(session)
            state = store.lock(uuid.UUID(self.conversation_id), uuid.UUID(self.lead_id))
            store.save_state(uuid.UUID(self.conversation_id), next_state(state.fsm_state, trigger))

    @property
    def state(self):
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT fsm_state FROM conversations WHERE id = :c"),
                                      {"c": self.conversation_id}).scalar()


class StubCatalog:
    async def call(self, tool, arguments=None):
        return stub_call(tool, arguments)


@pytest.mark.parametrize("edge", sorted(TRANSITIONS), ids=lambda e: f"{e[0]}--{e[1]}")
def test_each_edge_moves_the_persisted_state(engine, edge):
    state, trigger = edge
    chat = Conversation(engine, state)
    message = EDGE_MESSAGES[edge]
    if message is None:
        chat.fire(trigger)
    else:
        for said in message if isinstance(message, tuple) else (message,):
            chat.say(said)
    assert chat.state == TRANSITIONS[edge]


# --- 3. rejilla completa estado x intención, con el resultado escrito a mano -------------------------

HUMAN_INTENTS = ("solicitud_humano", "fuera_de_alcance")


def _expected(state, intent):
    """El resultado esperado, del diseño y no del código: qué pasa con `intent` estando en `state`."""
    if state in TERMINAL_STATES:
        return state
    if intent in HUMAN_INTENTS:
        return "HUMAN_HANDOFF"
    if state == "NURTURE":
        return "DISCOVERY"  # el lead vuelve a escribir
    if state == "SCHEDULE":
        return state  # B-8: ninguna intención la agenda; avanza cuando el código resuelve la visita
    if state == "RECOMMEND":
        return state  # #229: tampoco se llega a SCHEDULE por una intención; la agenda la arranca el código
    advance_on = {"GREETING": (None, "DISCOVERY"), "DISCOVERY": ("busqueda_propiedad", "QUALIFY"),
                  "QUALIFY": ("consulta_precio", "RECOMMEND")}[state]
    required, target = advance_on
    return target if required in (None, intent) else state


@pytest.mark.parametrize("state", FSM_STATES)
@pytest.mark.parametrize("intent", INTENTS)
def test_the_whole_state_by_intent_grid(engine, state, intent):
    chat = Conversation(engine, state)
    moved = advance(engine, chat._event("x"), intent)
    assert moved.after == _expected(state, intent) == chat.state


def test_the_grid_covers_every_intent_the_classifier_can_emit():
    assert set(INTENTS) >= set(HUMAN_INTENTS)
    assert len(INTENTS) == len(set(INTENTS))


# --- recorridos completos ----------------------------------------------------------------------------

def test_the_happy_path_visits_every_state_of_the_funnel_in_order(engine):
    chat = Conversation(engine)
    visited = [chat.state]
    for message in ("Hi", "I'm looking for an apartment", "How much does it cost?", "I'd like to schedule a visit",
                    "Tomorrow at 10"):
        chat.say(message)
        visited.append(chat.state)
    assert visited == ["GREETING", "DISCOVERY", "QUALIFY", "RECOMMEND", "SCHEDULE", "HANDOFF"]


def test_a_lead_that_goes_quiet_and_comes_back_resumes_discovery(engine):
    chat = Conversation(engine)
    chat.say("Hi")
    chat.fire(NO_RESPONSE)
    assert chat.state == "NURTURE"
    chat.say("Hi again")
    assert chat.state == "DISCOVERY"


def test_a_lead_that_goes_quiet_twice_is_lost_and_stays_lost(engine):
    chat = Conversation(engine)
    chat.say("Hi")
    chat.fire(NO_RESPONSE)
    chat.fire(NO_RESPONSE)
    assert chat.state == "LOST"
    for message in ("Hi", "I'm looking for a house", HUMAN):
        chat.say(message)
    assert chat.state == "LOST"


def test_every_state_can_be_reached_in_a_real_conversation(engine):
    reached = set()
    chat = Conversation(engine)
    reached.add(chat.state)
    for message in ("Hi", "I'm looking for an apartment", "How much does it cost?", "I'd like to schedule a visit",
                    "Tomorrow at 10"):
        chat.say(message)
        reached.add(chat.state)
    quiet = Conversation(engine)
    quiet.say("Hi")
    quiet.fire(NO_RESPONSE)
    reached.add(quiet.state)
    quiet.fire(NO_RESPONSE)
    reached.add(quiet.state)
    human = Conversation(engine)
    human.say(HUMAN)
    reached.add(human.state)
    assert reached == set(FSM_STATES)
