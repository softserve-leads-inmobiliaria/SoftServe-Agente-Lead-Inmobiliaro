"""Revisión del PR #75: lo que llega de verdad a los handlers de Telegram, el agente, los fallos y la baja.

Los tests anteriores llaman directo a `reply_to`, así que no veían qué mensajes atiende el bot. Aquí se
usan los handlers reales de la librería.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, text
from telegram import Update

from app.agent import AgentResult
from app.channels import telegram
from app.channels.consent import OPT_OUT_CONFIRMATION
from app.channels.telegram import ERROR_REPLY, build_application, reply_to
from db.models import Lead
from db.session import session_scope


class StubCatalog:
    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"properties": []}


class RecordingAgent:
    name = "recording"

    def __init__(self):
        self.inputs = []

    async def run(self, turn, catalog):
        self.inputs.append(turn)
        return AgentResult(text="respuesta del agente", intent="saludo")


def update_for(message_text, *, command=False):
    message = {"message_id": 1, "date": 1700000000, "chat": {"id": 7, "type": "private"},
               "from": {"id": 7, "is_bot": False, "first_name": "x"}, "text": message_text}
    if command:
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(message_text.split()[0])}]
    return Update.de_json({"update_id": 1, "message": message}, None)


def handlers_reached(application, update):
    return [h for group in application.handlers.values() for h in group if h.check_update(update)]


# --- qué mensajes atiende el bot ---------------------------------------------------------------------------

@pytest.mark.parametrize("message_text,command", [
    ("hola", False), ("STOP", False), ("/stop", True), ("/stop@mi_bot", True), ("/start", True),
    ("/start prop_00231", True), ("/ayuda", True),
])
def test_every_text_reaches_a_handler_commands_included(engine, message_text, command):
    application = build_application("123:fake", engine=engine, catalog=StubCatalog())
    assert len(handlers_reached(application, update_for(message_text, command=command))) == 1


def test_a_message_without_text_is_not_a_text_message(engine):
    application = build_application("123:fake", engine=engine, catalog=StubCatalog())
    photo = Update.de_json({"update_id": 2, "message": {
        "message_id": 2, "date": 1700000000, "chat": {"id": 7, "type": "private"},
        "from": {"id": 7, "is_bot": False, "first_name": "x"},
        "photo": [{"file_id": "a", "file_unique_id": "b", "width": 1, "height": 1}]}}, None)
    assert handlers_reached(application, photo) == []


def test_updates_are_processed_concurrently_so_a_slow_turn_does_not_block_other_leads(engine):
    application = build_application("123:fake", engine=engine, catalog=StubCatalog())
    assert application.concurrent_updates > 1


# --- el mensaje pasa por el manejador real ------------------------------------------------------------------

class FakeMessage:
    def __init__(self, message_text):
        self.text = message_text
        self.date = None
        self.replies = []

    async def reply_text(self, value):
        self.replies.append(value)


def dispatch(engine, message_text, *, user_id, agent=None, catalog=None):
    message = FakeMessage(message_text)
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=user_id))
    application = SimpleNamespace(bot_data={"engine": engine, "catalog": catalog or StubCatalog(), "agent": agent})
    asyncio.run(telegram._on_message(update, SimpleNamespace(application=application)))
    return message.replies


def consent_of(engine, user_id):
    with session_scope(engine) as session:
        return session.execute(select(Lead.consent_status).where(Lead.telegram_user_id == user_id)).scalar_one()


def test_the_stop_command_withdraws_consent(engine):
    assert dispatch(engine, "/stop", user_id=910) == [OPT_OUT_CONFIRMATION]
    assert consent_of(engine, "910") == "withdrawn"


def test_the_stop_command_addressed_to_the_bot_in_a_group_also_works(engine):
    dispatch(engine, "/stop@mi_bot", user_id=911)
    assert consent_of(engine, "911") == "withdrawn"


def test_a_plain_word_still_works(engine):
    dispatch(engine, "STOP", user_id=912)
    assert consent_of(engine, "912") == "withdrawn"


def test_the_opt_out_leaves_an_audit_trail_like_the_one_the_core_writes(engine):
    dispatch(engine, "/stop", user_id=913)
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT actor, payload FROM audit_log WHERE action = 'input.opt_out' AND payload->>'channel' = 'telegram' "
            "AND payload->>'lead_id' = (SELECT id::text FROM leads WHERE telegram_user_id = '913')")).one()
    assert row.actor == "channel"


def test_start_with_a_deep_link_sends_the_property_and_an_empty_text(engine, monkeypatch):
    seen = []
    real = telegram.process_turn

    async def spy(engine_, event, catalog_, agent_=None):
        seen.append(dict(event))
        return await real(engine_, event, catalog_, agent_)

    monkeypatch.setattr(telegram, "process_turn", spy)
    dispatch(engine, "/start prop_00231", user_id=914)
    assert seen[0]["text"] == "" and seen[0]["property_id"] == "prop_00231"


def test_a_bare_start_is_a_greeting_not_an_unresolved_turn(engine):
    (reply,) = dispatch(engine, "/start", user_id=915)
    assert "asistente" in reply  # el saludo del agente, no "Gracias por tu mensaje..."


# --- el agente configurado llega al canal ---------------------------------------------------------------------

def test_the_agent_configured_in_main_is_the_one_that_answers_on_telegram(engine):
    agent = RecordingAgent()
    (reply,) = dispatch(engine, "Busco casa", user_id=916, agent=agent)
    assert "respuesta del agente" in reply and [i.text for i in agent.inputs] == ["Busco casa"]


def test_reply_to_uses_the_given_agent_and_defaults_to_the_fake_one(engine):
    agent = RecordingAgent()
    assert "respuesta del agente" in asyncio.run(
        reply_to(engine, StubCatalog(), telegram_user_id="917", text="hola", agent=agent))
    assert "asistente" in asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="918", text="hola"))


def test_the_application_carries_the_agent(engine):
    agent = RecordingAgent()
    application = build_application("123:fake", engine=engine, catalog=StubCatalog(), agent=agent)
    assert application.bot_data["agent"] is agent


def test_main_gives_the_agent_to_the_bot():
    """El bug: `start_bot` no recibía el agente y Telegram usaba siempre el fake, aun con LLM_PROVIDER=bedrock."""
    import inspect

    from app import main

    assert "agent=agent" in inspect.getsource(main.lifespan)


# --- si el turno falla, el lead recibe una respuesta ---------------------------------------------------------------

def test_a_failing_turn_answers_the_lead_instead_of_staying_silent(engine, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("catálogo caído")

    monkeypatch.setattr(telegram, "process_turn", broken)
    assert dispatch(engine, "Busco casa", user_id=919) == [ERROR_REPLY]


def test_a_failure_sending_the_reply_does_not_crash_the_bot(engine):
    class Broken(FakeMessage):
        async def reply_text(self, value):
            raise ConnectionError("Telegram no responde")

    message = Broken("hola")
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=920))
    application = SimpleNamespace(bot_data={"engine": engine, "catalog": StubCatalog(), "agent": None})
    asyncio.run(telegram._on_message(update, SimpleNamespace(application=application)))  # no lanza


# --- el token no se filtra a los logs ------------------------------------------------------------------------------

def test_the_http_client_does_not_log_the_url_that_carries_the_bot_token():
    logging.getLogger("httpx").setLevel(logging.INFO)
    telegram._quiet_http_logs()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
