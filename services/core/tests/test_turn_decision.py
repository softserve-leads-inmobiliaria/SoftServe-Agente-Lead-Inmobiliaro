"""Paso 0 del plan de confiabilidad (#247): cada turno deja por qué contestó así (`turn.decision`), sin nada de lo
que escribió el lead. En la prueba de jurado, O9 no se pudo explicar: los logs se perdieron al reconstruir el core."""
import asyncio
import json
import uuid

from sqlalchemy import text
from test_input_guard_turn import Chat
from test_refers_to import Catalog, Scripted
from test_visits_turn import INBOUND, agenda  # noqa: F401 (fixture)

from app.turn import DECISION, process_turn

STATUSES = {"prop_00233": "disponible", "prop_00234": "apartado"}


def _decisions(engine, conversation_id):
    with engine.connect() as connection:
        return [payload for actor, payload in connection.execute(text(
            "SELECT actor, payload FROM audit_log WHERE action = :a AND payload->>'conversation_id' = :c ORDER BY id"),
            {"a": DECISION, "c": conversation_id}) if actor == "turn"]


def _say_all(engine, about, messages, first=None):
    agent, catalog = Scripted(about, first), Catalog(STATUSES)
    ids = {"conversation_id": str(uuid.uuid4()), "lead_id": str(uuid.uuid4())}
    for message in messages:
        event = INBOUND | ids | {"event_id": str(uuid.uuid4()), "text": message}
        asyncio.run(process_turn(engine, event, catalog, agent=agent))
    return _decisions(engine, ids["conversation_id"])


def test_each_turn_says_who_wrote_the_reply_and_what_the_classifier_read(engine, agenda):  # noqa: F811
    first, second = _say_all(engine, "prop_00233", ["Houses in Usaquen?", "I'd like to see the cheaper one"])
    assert (first["route"], first["agent_route"], first["intent"]) == ("agent", "agent", "busqueda_propiedad")
    assert (second["route"], second["agent_route"]) == ("agenda:offer", "agenda")
    assert second["flags"] == {"wants_visit": True} and second["refers_to"] == "prop_00233"


def test_a_property_that_cannot_be_visited_shows_as_its_own_agenda_reply(engine, agenda):  # noqa: F811
    *_, last = _say_all(engine, "prop_00234", ["Houses in Usaquen?", "Can I visit the 4-bedroom one?"])
    assert last["route"] == "agenda:not_visitable"


def test_the_decision_carries_nothing_the_lead_wrote(engine, agenda):  # noqa: F811
    messages = ["Houses in Usaquen?", "I'd like to see the cheaper one"]
    dump = json.dumps(_say_all(engine, "prop_00233", messages))
    assert not any(word in dump for word in ("Usaquen", "cheaper"))


def test_a_reply_of_the_input_guard_is_recorded_too_but_not_as_a_guardrail_detection(engine):
    chat = Chat(engine)
    chat.say("Hola, busco un apartamento")
    assert [r.action for r in chat.audits()] == ["input.not_english"]
    (decision,) = _decisions(engine, chat.conversation_id)
    assert decision["route"] == "input_guard"
