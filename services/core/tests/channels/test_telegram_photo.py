"""B-10: la foto de la propiedad en Telegram, con el texto del turno como *caption*.

La foto no viaja en el evento CT-1: el turno se la pasa al canal aparte (`TurnReply`, DT-50).
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from catalog_stub import stub_call
from jsonschema import Draft202012Validator
from telegram.error import BadRequest, TimedOut

from app.agent import AgentResult
from app.channels import telegram
from app.channels.consent import PRIVACY_NOTICE
from app.channels.telegram import CAPTION_MAX_CHARS, ChannelReply, reply_to, reply_turn, send_reply
from db.repo_root import repo_root

PHOTO = "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?w=1024&q=80"
LISTING = {"id": "prop_00231", "title": "Depto 2 rec. en Providencia", "zone": "Providencia",
           "status": "disponible", "address_approx": "Providencia", "photo_url": PHOTO}
EVENT_SCHEMA = json.loads((repo_root() / "contracts" / "event.schema.json").read_text(encoding="utf-8"))


class PhotoCatalog:
    """El catálogo con la propiedad del anuncio; `photo` permite quitarle la foto."""

    def __init__(self, photo: str | None = PHOTO):
        self.photo = photo

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if tool == "get_property":
            return LISTING | {"photo_url": self.photo}
        return stub_call(tool, arguments)


class ToolAgent:
    """Un agente que consulta las tools que se le pidan y contesta con un texto fijo."""
    name = "tools"

    def __init__(self, *tools: str, intent: str = "consulta_precio"):
        self.tools, self.intent = tools, intent

    async def run(self, turn, catalog):
        results = {}
        for tool in self.tools:
            arguments = {"property_id": "prop_00231", "lead_qualified": False} if tool == "get_property" else {}
            results[tool] = await catalog.call(tool, arguments)
        return AgentResult(text="Cuesta 2.35 millones.", intent=self.intent, tool_results=results)


def turn_of(engine, monkeypatch, user_id, **kwargs):
    """Corre un turno por el canal y devuelve lo que el canal arma y lo que devolvió el turno."""
    seen = []
    real = telegram.process_turn_reply

    async def spy(*args, **kw):
        seen.append(await real(*args, **kw))
        return seen[-1]

    monkeypatch.setattr(telegram, "process_turn_reply", spy)
    kwargs.setdefault("text", "")
    reply = asyncio.run(reply_turn(engine, kwargs.pop("catalog", PhotoCatalog()), telegram_user_id=user_id,
                                   **kwargs))
    return reply, seen[0]


# --- qué turno trae foto -----------------------------------------------------------------------------------

def test_the_deep_link_turn_carries_the_photo_but_the_ct1_event_does_not(engine, monkeypatch):
    reply, turn = turn_of(engine, monkeypatch, "tg-photo-1", deep_link="prop_00231")

    assert reply.photo_url == PHOTO
    assert turn.property["id"] == "prop_00231"
    Draft202012Validator(EVENT_SCHEMA).validate(turn.outbound)
    assert "photo_url" not in turn.outbound


def test_the_caption_is_the_same_text_the_lead_would_get_without_photo(engine, monkeypatch):
    reply, _ = turn_of(engine, monkeypatch, "tg-photo-2", deep_link="prop_00231")
    plain = asyncio.run(reply_to(engine, PhotoCatalog(photo=None), telegram_user_id="tg-photo-2b",
                                 text="", deep_link="prop_00231"))

    assert reply.text == plain
    assert reply.text.startswith(PRIVACY_NOTICE) and "Depto 2 rec. en Providencia" in reply.text


def test_a_property_without_photo_sends_only_text(engine, monkeypatch):
    reply, turn = turn_of(engine, monkeypatch, "tg-photo-3", deep_link="prop_00231", catalog=PhotoCatalog(photo=None))
    assert reply.photo_url is None and turn.property["id"] == "prop_00231"


def test_the_agent_asking_for_one_property_brings_its_photo(engine, monkeypatch):
    reply, _ = turn_of(engine, monkeypatch, "tg-photo-4", text="¿Cuánto cuesta?", agent=ToolAgent("get_property"))
    assert reply.photo_url == PHOTO


def test_a_turn_that_also_searched_talks_about_several_properties_and_has_no_photo(engine, monkeypatch):
    reply, turn = turn_of(engine, monkeypatch, "tg-photo-5", text="¿Qué más hay?",
                          agent=ToolAgent("get_property", "search_properties"))
    assert reply.photo_url is None and turn.property is None


def test_a_turn_without_property_has_no_photo(engine, monkeypatch):
    reply, turn = turn_of(engine, monkeypatch, "tg-photo-6", text="hola")
    assert reply.photo_url is None and turn.property is None


def test_asking_for_a_human_replaces_the_reply_so_there_is_no_photo(engine, monkeypatch):
    reply, turn = turn_of(engine, monkeypatch, "tg-photo-7", text="Quiero hablar con un asesor",
                          agent=ToolAgent("get_property", intent="solicitud_humano"))
    assert reply.photo_url is None and turn.fsm_state == "HUMAN_HANDOFF"


def test_asking_for_an_alert_replaces_the_reply_so_there_is_no_photo(engine, monkeypatch):
    reply, turn = turn_of(engine, monkeypatch, "tg-photo-9", text="Avísame si sale algo así",
                          agent=ToolAgent("get_property"))
    assert reply.photo_url is None and turn.property is None


def test_the_opt_out_has_no_photo(engine):
    reply = asyncio.run(reply_turn(engine, PhotoCatalog(), telegram_user_id="tg-photo-8", text="STOP"))
    assert reply == ChannelReply(telegram.consent.OPT_OUT_CONFIRMATION)


# --- cómo se manda ---------------------------------------------------------------------------------------------

class FakeMessage:
    def __init__(self, photo_error: Exception | None = None, text: str = ""):
        self.text, self.date = text, None
        self.photo_error = photo_error
        self.photos: list[tuple[str, str]] = []
        self.replies: list[str] = []
        self.markups: list[Any] = []

    async def reply_photo(self, photo, caption=None, reply_markup=None):
        if self.photo_error is not None:
            raise self.photo_error
        self.photos.append((photo, caption))
        self.markups.append(reply_markup)

    async def reply_text(self, value, reply_markup=None):
        self.replies.append(value)
        self.markups.append(reply_markup)


def test_with_photo_the_text_goes_as_caption():
    message = FakeMessage()
    asyncio.run(send_reply(message, ChannelReply("Hola", PHOTO)))
    assert message.photos == [(PHOTO, "Hola")] and message.replies == []


def test_without_photo_only_the_text_goes():
    message = FakeMessage()
    asyncio.run(send_reply(message, ChannelReply("Hola")))
    assert message.photos == [] and message.replies == ["Hola"]


@pytest.mark.parametrize("error", [BadRequest("Wrong file identifier/http url specified"), ConnectionError("x")])
def test_if_telegram_cannot_send_the_photo_the_text_still_goes(error, caplog):
    message = FakeMessage(photo_error=error)
    asyncio.run(send_reply(message, ChannelReply("Hola", PHOTO)))
    assert message.replies == ["Hola"]
    assert "foto de la propiedad" in caplog.text


def test_a_timeout_does_not_fall_back_to_text_because_the_photo_may_have_arrived():
    message = FakeMessage(photo_error=TimedOut())
    with pytest.raises(TimedOut):
        asyncio.run(send_reply(message, ChannelReply("Hola", PHOTO)))
    assert message.replies == []


def test_a_text_too_long_for_a_caption_goes_without_photo():
    message = FakeMessage()
    long_text = "a" * (CAPTION_MAX_CHARS + 1)
    asyncio.run(send_reply(message, ChannelReply(long_text, PHOTO)))
    assert message.photos == [] and message.replies == [long_text]


def test_the_caption_limit_counts_utf16_units_like_telegram():
    message = FakeMessage()
    emojis = "🏠" * (CAPTION_MAX_CHARS // 2 + 1)  # cada uno vale 2 unidades UTF-16
    asyncio.run(send_reply(message, ChannelReply(emojis, PHOTO)))
    assert message.photos == [] and message.replies == [emojis]


# --- por el manejador real del bot ----------------------------------------------------------------------------

def test_start_with_a_deep_link_answers_with_the_photo(engine):
    message = FakeMessage(text="/start prop_00231")
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=940))
    application = SimpleNamespace(bot_data={"engine": engine, "catalog": PhotoCatalog(), "agent": None})
    asyncio.run(telegram._on_message(update, SimpleNamespace(application=application)))

    ((photo, caption),) = message.photos
    assert photo == PHOTO and "Depto 2 rec. en Providencia" in caption and message.replies == []
