"""B-11: botones "Agendar visita" y "Hablar con un asesor" bajo la respuesta del bot.

Tocar un botón es, para el turno, lo mismo que escribir su texto fijo (DT-51): pasa por el guardrail de
entrada, la FSM y el score, y el evento CT-1 no cambia.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from catalog_stub import PROPERTY, stub_call
from jsonschema import Draft202012Validator
from sqlalchemy import select
from telegram import Update
from test_visits_turn import SLOTS, ContractAgenda

from app import scheduling_client
from app.agent import FakeAgent
from app.channels import telegram
from app.channels.telegram import (
    ADVISOR_BUTTON,
    BUTTON_TEXTS,
    VISIT_BUTTON,
    ChannelReply,
    build_application,
    buttons_for,
    keyboard,
    send_reply,
)
from app.turn import TurnReply
from db.models import Conversation, Lead
from db.repo_root import repo_root
from db.session import session_scope

EVENT_SCHEMA = json.loads((repo_root() / "contracts" / "event.schema.json").read_text(encoding="utf-8"))
TO_RECOMMEND = ("Hola", "Busco un departamento", "¿Cuánto cuesta?")


class StubCatalog:
    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return stub_call(tool, arguments)


class PropertyAgent:
    """El agente falso, que además consulta la ficha de la propiedad cuando el lead pregunta el precio:
    así ese turno trata de una sola propiedad, como pasa con el LLM real."""
    name = "fake+get_property"

    async def run(self, turn, catalog):
        result = await FakeAgent().run(turn, catalog)
        if result.intent != "consulta_precio":
            return result
        prop = await catalog.call("get_property", {"property_id": PROPERTY["id"], "lead_qualified": False})
        return replace(result, tool_results={**result.tool_results, "get_property": prop})

    async def summarize(self, transcript, facts):
        return await FakeAgent().summarize(transcript, facts)


@pytest.fixture
def agenda(monkeypatch):
    fake = ContractAgenda()
    monkeypatch.setattr(scheduling_client, "get_scheduling", lambda: fake)
    return fake


# --- qué updates atiende el bot ----------------------------------------------------------------------------

def callback_update(data):
    return Update.de_json({"update_id": 3, "callback_query": {
        "id": "cb1", "chat_instance": "ci", "data": data,
        "from": {"id": 7, "is_bot": False, "first_name": "x"},
        "message": {"message_id": 5, "date": 1700000000, "chat": {"id": 7, "type": "private"}, "text": "hola"}}},
        None)


def handlers_reached(application, update):
    return [h for group in application.handlers.values() for h in group if h.check_update(update)]


@pytest.mark.parametrize("button", [VISIT_BUTTON, ADVISOR_BUTTON])
def test_a_button_tap_reaches_the_button_handler(engine, button):
    application = build_application("123:fake", engine=engine, catalog=StubCatalog())
    (handler,) = handlers_reached(application, callback_update(f"lead:{button}"))
    assert handler.callback is telegram._on_button


@pytest.mark.parametrize("data", ["lead:otro", "visita", "lead:visita:extra"])
def test_an_unknown_button_reaches_no_handler(engine, data):
    application = build_application("123:fake", engine=engine, catalog=StubCatalog())
    assert handlers_reached(application, callback_update(data)) == []


# --- qué botones van debajo ----------------------------------------------------------------------------------

@pytest.mark.parametrize("prop,state,expected", [
    (None, "RECOMMEND", ()),
    (PROPERTY, "DISCOVERY", (ADVISOR_BUTTON,)),
    (PROPERTY, "QUALIFY", (ADVISOR_BUTTON,)),
    (PROPERTY, "RECOMMEND", (VISIT_BUTTON, ADVISOR_BUTTON)),
])
def test_the_visit_button_only_shows_where_asking_for_a_visit_books_it(prop, state, expected):
    assert buttons_for(TurnReply({}, prop, state)) == expected


def test_each_button_carries_its_label_and_callback():
    markup = keyboard((VISIT_BUTTON, ADVISOR_BUTTON))
    rows = [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]
    assert rows == [[("Agendar visita", "lead:visita")], [("Hablar con un asesor", "lead:asesor")]]


class FakeMessage:
    def __init__(self, text=""):
        self.text, self.date = text, None
        self.sent: list[tuple[str, str, Any]] = []

    async def reply_text(self, value, reply_markup=None, parse_mode=None):
        self.sent.append(("text", value, reply_markup))

    async def reply_photo(self, photo, caption=None, reply_markup=None, parse_mode=None):
        self.sent.append(("photo", caption, reply_markup))


@pytest.mark.parametrize("photo", [None, "https://images.unsplash.com/photo-1?w=1024"])
def test_the_buttons_go_under_the_text_or_the_photo(photo):
    message = FakeMessage()
    asyncio.run(send_reply(message, ChannelReply("Hola", photo, (ADVISOR_BUTTON,))))
    ((kind, value, markup),) = message.sent
    assert kind == ("photo" if photo else "text") and value == "Hola"
    assert markup.inline_keyboard[0][0].callback_data == "lead:asesor"


def test_without_buttons_no_keyboard_is_sent():
    message = FakeMessage()
    asyncio.run(send_reply(message, ChannelReply("Hola")))
    assert message.sent == [("text", "Hola", None)]


# --- el flujo completo por los manejadores del bot --------------------------------------------------------------

class FakeChat:
    def __init__(self):
        self.sent: list[tuple[str, Any]] = []

    async def send_message(self, text, reply_markup=None, parse_mode=None):
        self.sent.append((text, reply_markup))

    async def send_photo(self, photo, caption=None, reply_markup=None, parse_mode=None):
        self.sent.append((caption, reply_markup))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answered = 0
        self.markups_removed = 0

    async def answer(self):
        self.answered += 1

    async def edit_message_reply_markup(self, reply_markup=None):
        assert reply_markup is None
        self.markups_removed += 1


class Lead_:
    """Un lead hablando con el bot por los manejadores reales, con el texto o con los botones."""

    def __init__(self, engine, user_id, agent=None):
        self.engine, self.user_id = engine, user_id
        self.context = SimpleNamespace(application=SimpleNamespace(bot_data={
            "engine": engine, "catalog": StubCatalog(), "agent": agent or PropertyAgent()}))
        self.events: list[dict[str, Any]] = []
        self.replies: list[tuple[str, Any]] = []

    def say(self, text):
        message = FakeMessage(text)
        update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=self.user_id))
        asyncio.run(telegram._on_message(update, self.context))
        ((_, value, markup),) = message.sent
        self.replies.append((value, markup))
        return value

    def tap(self, button):
        chat, query = FakeChat(), FakeQuery(f"lead:{button}")
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=self.user_id),
                                 effective_chat=chat)
        asyncio.run(telegram._on_button(update, self.context))
        assert query.answered == 1 and query.markups_removed == 1
        ((value, markup),) = chat.sent
        self.replies.append((value, markup))
        return value

    def buttons(self):
        markup = self.replies[-1][1]
        return [row[0].callback_data for row in markup.inline_keyboard] if markup else []

    @property
    def state(self):
        with session_scope(self.engine) as session:
            return session.execute(select(Conversation.fsm_state).join(Lead, Lead.id == Conversation.lead_id)
                                   .where(Lead.telegram_user_id == str(self.user_id))
                                   .order_by(Conversation.started_at.desc())).scalars().first()


@pytest.fixture
def spy_events(monkeypatch):
    events = []
    real = telegram.process_turn_reply

    async def spy(engine_, event, *args, **kwargs):
        events.append(dict(event))
        return await real(engine_, event, *args, **kwargs)

    monkeypatch.setattr(telegram, "process_turn_reply", spy)
    return events


def test_in_recommend_the_property_reply_carries_both_buttons(engine, agenda):
    lead = Lead_(engine, 960)
    for text in TO_RECOMMEND:
        lead.say(text)
    assert lead.state == "RECOMMEND"
    assert lead.buttons() == ["lead:visita", "lead:asesor"]


def test_before_recommend_only_the_advisor_button_shows(engine, agenda):
    lead = Lead_(engine, 961)
    lead.say("Hola")
    lead.say("¿Cuánto cuesta?")
    assert lead.state == "DISCOVERY" and lead.buttons() == ["lead:asesor"]


def test_a_reply_that_is_not_about_a_property_has_no_buttons(engine, agenda):
    lead = Lead_(engine, 962)
    lead.say("Hola")
    assert lead.buttons() == []


def test_the_visit_button_offers_the_slots_just_like_writing_it(engine, agenda, spy_events):
    lead = Lead_(engine, 963)
    for text in TO_RECOMMEND:
        lead.say(text)
    reply = lead.tap(VISIT_BUTTON)

    assert "Casa de prueba" in reply and "1) mar 06/10 10:00" in reply
    assert lead.state == "SCHEDULE" and lead.buttons() == []
    # El botón llegó al turno como un mensaje del lead con su texto fijo, en un evento CT-1 válido.
    assert spy_events[-1]["text"] == BUTTON_TEXTS[VISIT_BUTTON]
    Draft202012Validator(EVENT_SCHEMA).validate(spy_events[-1])


def test_tapping_the_visit_button_twice_books_a_single_visit(engine, agenda):
    lead = Lead_(engine, 964)
    for text in TO_RECOMMEND:
        lead.say(text)
    lead.tap(VISIT_BUTTON)
    assert "1) mar 06/10 10:00" in lead.tap(VISIT_BUTTON)  # en SCHEDULE vuelve a ofrecer, no reserva
    lead.say("1")
    assert lead.state == "HANDOFF"
    lead.tap(VISIT_BUTTON)  # ya agendada: abre una conversación nueva (#84), que no reserva nada

    assert [b["scheduled_at"] for b in agenda.booked()] == [SLOTS[0]]


def test_the_advisor_button_hands_the_lead_to_a_human(engine, agenda, spy_events):
    lead = Lead_(engine, 965)
    lead.say("Hola")
    lead.say("¿Cuánto cuesta?")
    lead.tap(ADVISOR_BUTTON)

    assert lead.state == "HUMAN_HANDOFF"
    assert spy_events[-1]["text"] == BUTTON_TEXTS[ADVISOR_BUTTON]
    assert lead.buttons() == []


def test_a_failing_turn_after_a_tap_still_answers_the_lead(engine, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("catálogo caído")

    monkeypatch.setattr(telegram, "process_turn_reply", broken)
    assert Lead_(engine, 966).tap(ADVISOR_BUTTON) == telegram.ERROR_REPLY


def test_if_the_button_cannot_be_closed_the_tap_is_still_answered(engine, agenda):
    class StaleQuery(FakeQuery):
        async def edit_message_reply_markup(self, reply_markup=None):
            raise RuntimeError("Message is not modified")

    chat = FakeChat()
    update = SimpleNamespace(callback_query=StaleQuery("lead:asesor"), effective_user=SimpleNamespace(id=967),
                             effective_chat=chat)
    asyncio.run(telegram._on_button(update, Lead_(engine, 967).context))
    assert len(chat.sent) == 1
