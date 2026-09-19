"""Tests de A-1: una conversación recorre la FSM y cada paso queda en Postgres."""
import asyncio
import json
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.fsm import next_state
from app.state_store import ConversationMismatch
from app.turn import advance, process_turn
from db.repo_root import repo_root

INBOUND = json.loads((repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))

# Frases que el fake LLM clasifica en cada intención.
SAY = {
    "saludo": "Hola",
    "busqueda_propiedad": "Busco un departamento",
    "consulta_precio": "¿Cuánto cuesta?",
    "agendar_visita": "Quiero agendar una visita",
    "solicitud_humano": "Quiero hablar con un asesor",
    "otro": "¿Cómo va el clima hoy?",
}


class StubCatalog:
    async def call(self, tool, arguments=None):
        return {"properties": [{"title": "Casa de prueba"}]}


class Conversation:
    """Un lead conversando: cada mensaje pasa por el turno completo y luego se lee el estado."""

    def __init__(self, engine):
        self.engine = engine
        self.conversation_id, self.lead_id = str(uuid.uuid4()), str(uuid.uuid4())

    def say(self, intent):
        event = INBOUND | {"conversation_id": self.conversation_id, "lead_id": self.lead_id,
                           "event_id": str(uuid.uuid4()), "text": SAY[intent]}
        return asyncio.run(process_turn(self.engine, event, StubCatalog()))

    @property
    def state(self):
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT fsm_state FROM conversations WHERE id = :c"),
                                      {"c": self.conversation_id}).scalar()


def test_a_conversation_walks_the_whole_happy_path_and_persists_each_step(engine):
    chat = Conversation(engine)
    path = [("saludo", "DISCOVERY"), ("busqueda_propiedad", "QUALIFY"), ("consulta_precio", "RECOMMEND"),
            ("agendar_visita", "SCHEDULE"), ("agendar_visita", "HANDOFF")]
    for intent, expected in path:
        reply = chat.say(intent)
        assert reply["direction"] == "outbound"
        assert chat.state == expected


def test_messages_that_do_not_advance_keep_the_state_but_are_answered(engine):
    chat = Conversation(engine)
    chat.say("saludo")
    assert chat.say("otro")["text"] and chat.state == "DISCOVERY"
    assert chat.say("saludo")["text"] and chat.state == "DISCOVERY"


@pytest.mark.parametrize("steps", [
    [],
    ["saludo"],
    ["saludo", "busqueda_propiedad"],
    ["saludo", "busqueda_propiedad", "consulta_precio"],
    ["saludo", "busqueda_propiedad", "consulta_precio", "agendar_visita"],
])
def test_a_human_can_be_requested_from_every_stage(engine, steps):
    chat = Conversation(engine)
    for intent in steps:
        chat.say(intent)
    chat.say("solicitud_humano")
    assert chat.state == "HUMAN_HANDOFF"


def test_terminal_states_are_final(engine):
    chat = Conversation(engine)
    chat.say("solicitud_humano")
    for intent in ("saludo", "busqueda_propiedad", "agendar_visita", "solicitud_humano"):
        chat.say(intent)
        assert chat.state == "HUMAN_HANDOFF"


def test_nurture_and_lost_edges_persist(engine):
    """NO_RESPONSE lo emite el scheduler (fuera de A-1): se prueba la arista sobre el estado real."""
    from app.state_store import StateStore
    from db.session import session_scope

    chat = Conversation(engine)
    chat.say("saludo")
    conversation_id = uuid.UUID(chat.conversation_id)
    for trigger, expected in (("no_response", "NURTURE"), ("no_response", "LOST")):
        with session_scope(engine) as session:
            store = StateStore(session)
            state = store.lock(conversation_id, uuid.UUID(chat.lead_id))
            store.save_state(conversation_id, next_state(state.fsm_state, trigger))
        assert chat.state == expected


def test_lead_returning_from_nurture_goes_back_to_discovery(engine):
    from app.state_store import StateStore
    from db.session import session_scope

    chat = Conversation(engine)
    chat.say("saludo")
    with session_scope(engine) as session:
        StateStore(session).save_state(uuid.UUID(chat.conversation_id), "NURTURE")
    chat.say("saludo")
    assert chat.state == "DISCOVERY"


def test_state_survives_a_restart(engine, database_url):
    chat = Conversation(engine)
    chat.say("saludo")
    chat.say("busqueda_propiedad")
    restarted = create_engine(database_url)
    try:
        resumed = Conversation(restarted)
        resumed.conversation_id, resumed.lead_id = chat.conversation_id, chat.lead_id
        assert resumed.state == "QUALIFY"
        resumed.say("consulta_precio")
        assert chat.state == "RECOMMEND"
    finally:
        restarted.dispose()


def test_advance_reports_the_transition(engine):
    event = INBOUND | {"conversation_id": str(uuid.uuid4()), "lead_id": str(uuid.uuid4())}
    moved = advance(engine, event, "saludo")
    assert (moved.before, moved.after, moved.trigger) == ("GREETING", "DISCOVERY", "greeted")
    stayed = advance(engine, event, "saludo")
    assert (stayed.before, stayed.after, stayed.trigger) == ("DISCOVERY", "DISCOVERY", None)


def test_conversation_of_another_lead_is_rejected(engine):
    chat = Conversation(engine)
    chat.say("saludo")
    event = INBOUND | {"conversation_id": chat.conversation_id, "lead_id": str(uuid.uuid4())}
    with pytest.raises(ConversationMismatch):
        advance(engine, event, "saludo")
