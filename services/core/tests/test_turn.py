"""Tests de A-1: una conversación recorre la FSM y cada paso queda en Postgres."""
import asyncio
import json
import uuid

import pytest
from catalog_stub import CHECKED_AT, PROPERTY, stub_call
from sqlalchemy import create_engine, text

from app.fsm import next_state
from app.state_store import ConversationMismatch
from app.turn import advance, process_turn
from db.repo_root import repo_root

# Sin `property_id`: como un mensaje normal de Telegram (solo el `/start` del anuncio lo trae, #122).
INBOUND = {key: value for key, value in json.loads(
    (repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8")).items()
    if key != "property_id"}

# Frases que el fake LLM clasifica en cada intención.
SAY = {
    "saludo": "Hola",
    "busqueda_propiedad": "Busco un departamento",
    "consulta_precio": "¿Cuánto cuesta?",
    "agendar_visita": "Quiero agendar una visita",
    "solicitud_humano": "Quiero hablar con un asesor",
    "otro": "¿Cómo va el clima hoy?",
    "horario_preferido": "Mañana a las 10",  # B-8: respuesta a la agenda, sin intención propia
}


class StubCatalog:
    async def call(self, tool, arguments=None):
        return stub_call(tool, arguments)


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
            ("agendar_visita", "SCHEDULE"), ("horario_preferido", "HANDOFF")]
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


# --- A-9: el score se acumula turno a turno y se persiste junto con el estado ----------------------

def _fsm_state_contract():
    return json.loads((repo_root() / "contracts" / "fsm_state.schema.json").read_text(encoding="utf-8"))


def _score_row(engine, lead_id):
    with engine.connect() as connection:
        return connection.execute(text("SELECT need_fit, budget, authority, timing, eligibility, total, band "
                                       "FROM lead_scores WHERE lead_id = :l"), {"l": lead_id}).one_or_none()


def test_the_score_accumulates_turn_after_turn_and_is_persisted(engine):
    chat = Conversation(engine)
    steps = [
        ("Hola", (0, "COLD")),
        ("Busco un departamento", (10, "COLD")),                       # 1 propiedad del catálogo -> need_fit 10
        ("Mi presupuesto es de 3 millones", (30, "COLD")),
        ("Decido yo, lo necesito urgente", (70, "WARM")),
    ]
    for message, (total, band) in steps:
        event = INBOUND | {"conversation_id": chat.conversation_id, "lead_id": chat.lead_id,
                           "event_id": str(uuid.uuid4()), "text": message}
        asyncio.run(process_turn(engine, event, StubCatalog()))
        row = _score_row(engine, chat.lead_id)
        assert (row.total, row.band) == (total, band), message


def test_there_is_one_score_row_per_lead_however_many_turns(engine):
    chat = Conversation(engine)
    for intent in ("saludo", "busqueda_propiedad", "consulta_precio", "otro"):
        chat.say(intent)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM lead_scores WHERE lead_id = :l"),
                                  {"l": chat.lead_id}).scalar() == 1


def test_the_persisted_signal_and_score_form_a_valid_fsm_state_contract(engine):
    from jsonschema import Draft202012Validator, FormatChecker

    from app.state_store import StateStore
    from db.session import session_scope

    chat = Conversation(engine)
    event = INBOUND | {"conversation_id": chat.conversation_id, "lead_id": chat.lead_id,
                       "text": "Busco casa, mi presupuesto es 2 millones, decido yo y es urgente"}
    asyncio.run(process_turn(engine, event, StubCatalog()))
    with session_scope(engine) as session:
        state = StateStore(session).lock(uuid.UUID(chat.conversation_id), uuid.UUID(chat.lead_id))
    contract = state.to_contract()
    Draft202012Validator(_fsm_state_contract(), format_checker=FormatChecker()).validate(contract)
    assert contract["score_signal"] == {"budget_hint": "presupuesto es 2 millones", "decision_maker": "declarado",
                                        "urgency": "inmediata", "matching_properties": 1,
                                        "meets_requirements": None}
    assert contract["score"]["total"] == 10 + 20 + 20 + 20 and contract["score"]["band"] == "WARM"


def test_the_score_survives_a_restart(engine, database_url):
    chat = Conversation(engine)
    event = INBOUND | {"conversation_id": chat.conversation_id, "lead_id": chat.lead_id,
                       "text": "Busco casa, decido yo"}
    asyncio.run(process_turn(engine, event, StubCatalog()))
    restarted = create_engine(database_url)
    try:
        follow_up = INBOUND | {"conversation_id": chat.conversation_id, "lead_id": chat.lead_id,
                               "event_id": str(uuid.uuid4()), "text": "Lo necesito urgente"}
        asyncio.run(process_turn(restarted, follow_up, StubCatalog()))
        assert _score_row(restarted, chat.lead_id).total == 10 + 20 + 20
    finally:
        restarted.dispose()


def test_tool_results_are_kept_only_for_the_last_turn(engine):
    chat = Conversation(engine)

    def last_results():
        with engine.connect() as connection:
            return connection.execute(text("SELECT last_tool_results FROM fsm_context WHERE conversation_id = :c"),
                                      {"c": chat.conversation_id}).scalar()

    remembered = {"property_of_interest": {"id": PROPERTY["id"], "title": PROPERTY["title"]}}
    chat.say("busqueda_propiedad")
    assert last_results() == {"search_properties": {"properties": [PROPERTY | {"availability_checked_at": CHECKED_AT}],
                                                    "unavailable": []}} | remembered
    chat.say("saludo")
    # B-8: de un turno a otro solo se arrastra la propiedad de interés, sin su `status`.
    assert last_results() == remembered


def test_a_turn_without_a_search_keeps_the_matches_counted_before(engine):
    chat = Conversation(engine)
    chat.say("busqueda_propiedad")
    chat.say("saludo")
    assert _score_row(engine, chat.lead_id).need_fit == 10


def test_the_conversation_is_scored_even_after_it_reaches_a_terminal_state(engine):
    chat = Conversation(engine)
    chat.say("solicitud_humano")
    assert chat.state == "HUMAN_HANDOFF"
    event = INBOUND | {"conversation_id": chat.conversation_id, "lead_id": chat.lead_id,
                       "event_id": str(uuid.uuid4()), "text": "Por cierto, decido yo y es urgente"}
    asyncio.run(process_turn(engine, event, StubCatalog()))
    assert chat.state == "HUMAN_HANDOFF" and _score_row(engine, chat.lead_id).total == 40


def test_state_and_score_are_saved_together_or_not_at_all(engine, monkeypatch):
    import app.turn as turn_module

    chat = Conversation(engine)
    chat.say("saludo")
    before = _score_row(engine, chat.lead_id)

    def broken(*args, **kwargs):
        raise RuntimeError("falla el cálculo del score")

    monkeypatch.setattr(turn_module, "score_turn", broken)
    with pytest.raises(RuntimeError):
        chat.say("busqueda_propiedad")
    assert chat.state == "DISCOVERY" and _score_row(engine, chat.lead_id) == before


def test_advance_reports_the_score(engine):
    event = INBOUND | {"conversation_id": str(uuid.uuid4()), "lead_id": str(uuid.uuid4())}
    moved = advance(engine, event, "saludo")
    assert (moved.score.total, moved.score.band) == (0, "COLD")


# --- #122: la propiedad del anuncio (`?start=prop_<id>`) -------------------------------------------------------

LISTING = {"id": "prop_00231", "title": "Depto 2 rec. en El Poblado", "zone": "El Poblado", "status": "disponible"}


class ListingCatalog:
    """El catálogo con la propiedad del anuncio; guarda qué tools le piden."""

    def __init__(self, missing=False, status="disponible"):
        self.calls, self.missing, self.status = [], missing, status

    async def call(self, tool, arguments=None):
        from app.catalog_client import CatalogToolError

        self.calls.append((tool, dict(arguments or {})))
        if tool == "get_property":
            if self.missing:
                raise CatalogToolError({"code": "NOT_FOUND", "message": "no existe"})
            return LISTING | {"status": self.status}
        if tool == "get_availability":
            return stub_call(tool, arguments) | {"status": self.status}
        return stub_call(tool, arguments)


def _deep_link(chat, catalog, message="", property_id="prop_00231"):
    event = INBOUND | {"conversation_id": chat.conversation_id, "lead_id": chat.lead_id,
                       "event_id": str(uuid.uuid4()), "text": message, "property_id": property_id}
    return asyncio.run(process_turn(chat.engine, event, catalog))


def _lead_zone(engine, lead_id):
    with engine.connect() as connection:
        return connection.execute(text("SELECT zone FROM leads WHERE id = :l"), {"l": lead_id}).scalar()


def test_the_listing_greeting_names_the_property_the_lead_came_from(engine):
    chat = Conversation(engine)
    reply = _deep_link(chat, ListingCatalog())
    assert "Depto 2 rec. en El Poblado" in reply["text"] and "ref. prop_00231" in reply["text"]
    assert chat.state == "DISCOVERY"


def test_the_listing_is_looked_up_by_the_code_and_gives_the_lead_its_zone(engine):
    chat = Conversation(engine)
    catalog = ListingCatalog()
    _deep_link(chat, catalog, message="Hola, me interesa esta propiedad")
    assert ("get_property", {"property_id": "prop_00231", "lead_qualified": False}) in catalog.calls
    assert _lead_zone(engine, chat.lead_id) == "El Poblado"
    with engine.connect() as connection:
        saved = connection.execute(text("SELECT last_tool_results FROM fsm_context WHERE conversation_id = :c"),
                                   {"c": chat.conversation_id}).scalar()
    assert saved["get_property"]["id"] == "prop_00231"


def test_the_greeting_stays_in_the_history_for_the_next_turns(engine):
    chat = Conversation(engine)
    _deep_link(chat, ListingCatalog())
    with engine.connect() as connection:
        history = connection.execute(text("SELECT content FROM messages WHERE conversation_id = :c"),
                                     {"c": chat.conversation_id}).scalars().all()
    assert any("ref. prop_00231" in content for content in history)


def test_a_listing_the_catalog_does_not_know_falls_back_to_the_plain_greeting(engine):
    chat = Conversation(engine)
    reply = _deep_link(chat, ListingCatalog(missing=True), property_id="prop_99999")
    assert "ref." not in reply["text"] and reply["text"]
    assert chat.state == "DISCOVERY" and _lead_zone(engine, chat.lead_id) is None


# --- A-15: la propiedad del anuncio cuenta para `need_fit` ----------------------------------------------------

def test_a_lead_from_the_listing_of_an_available_property_has_need_fit(engine):
    chat = Conversation(engine)
    _deep_link(chat, ListingCatalog())
    assert _score_row(engine, chat.lead_id).need_fit == 10  # 1 propiedad -> need_fit 10, como una búsqueda


def test_a_lead_from_the_listing_of_a_property_already_taken_has_no_need_fit(engine):
    chat = Conversation(engine)
    _deep_link(chat, ListingCatalog(status="apartado"))
    assert _score_row(engine, chat.lead_id).need_fit == 0


def test_the_listing_need_fit_is_kept_in_the_next_turns(engine):
    chat = Conversation(engine)
    _deep_link(chat, ListingCatalog())
    chat.say("saludo")
    assert _score_row(engine, chat.lead_id).need_fit == 10
