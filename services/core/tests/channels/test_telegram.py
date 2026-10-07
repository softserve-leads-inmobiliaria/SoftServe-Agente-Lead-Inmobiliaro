"""B-1: el bot traduce un mensaje de Telegram a un turno real y responde."""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from telegram.ext import CallbackQueryHandler, MessageHandler

from app.agent import AgentResult
from app.channels import telegram
from app.channels.consent import PRIVACY_NOTICE
from app.channels.telegram import build_application, reply_to, start_bot
from db.models import Conversation, Lead
from db.session import session_scope


class StubCatalog:
    """Catálogo que no habla por red: el foco del test es el canal, no el MCP."""

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"properties": []}


def test_un_mensaje_crea_el_lead_y_devuelve_respuesta(engine):
    reply = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-bot-1", text="hi"))

    assert reply
    with session_scope(engine) as session:
        lead = session.execute(select(Lead).where(Lead.telegram_user_id == "tg-bot-1")).scalar_one()
        conversation = session.execute(
            select(Conversation).where(Conversation.lead_id == lead.id)
        ).scalar_one()
    assert conversation.fsm_state == "DISCOVERY"  # GREETING + saludo avanza la FSM


def test_el_deep_link_de_propiedad_llega_al_turno(engine, monkeypatch):
    capturados: list[dict[str, Any]] = []
    real_process_turn = telegram.process_turn_reply

    async def spy(engine_, event, catalog_, agent_=None, on_qualified=None, on_handoff=None):
        capturados.append(dict(event))
        return await real_process_turn(engine_, event, catalog_, agent_, on_qualified, on_handoff)

    monkeypatch.setattr(telegram, "process_turn_reply", spy)
    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-bot-2", text="",
                         deep_link="prop_00231"))

    assert capturados[0]["property_id"] == "prop_00231"


def test_el_primer_turno_incluye_el_aviso_de_privacidad(engine):
    primera = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-bot-3", text="hi"))
    segunda = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-bot-3",
                                   text="hi again"))

    assert PRIVACY_NOTICE in primera
    assert PRIVACY_NOTICE not in segunda


def test_sin_token_el_bot_no_arranca(engine, monkeypatch):
    """Es lo que permite que CI, el arnés y los tests corran sin credenciales de Telegram."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert asyncio.run(start_bot(engine=engine, catalog=StubCatalog())) is None


def test_la_aplicacion_registra_los_handlers(engine):
    application = build_application("123:fake-token", engine=engine, catalog=StubCatalog())
    # Un solo manejador para todo texto, comandos incluidos, y otro para los botones de B-11.
    assert [type(h) for h in application.handlers[0]] == [MessageHandler, CallbackQueryHandler]


# --- #227: los mensajes de un mismo chat se atienden de a uno ------------------------------------------

class RecordingAgent:
    """Anota el historial que ve cada turno y tarda un poco, para que dos turnos se solapen si pueden."""

    name = "recording"

    def __init__(self) -> None:
        self.seen: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    async def run(self, turn, catalog):
        self.seen.append((turn.text, turn.history))
        await asyncio.sleep(0.05)
        return AgentResult(text=f"reply to {turn.text}", intent="otro")


def test_dos_mensajes_seguidos_del_mismo_chat_se_atienden_en_orden(engine):
    agent = RecordingAgent()

    async def rafaga():
        return await asyncio.gather(*(reply_to(engine, StubCatalog(), telegram_user_id="tg-serie-1", text=text,
                                               agent=agent) for text in ("Hi", "I'm looking for an apartment")))

    asyncio.run(rafaga())

    (_, first_history), (second_text, second_history) = agent.seen
    assert first_history == () and second_text == "I'm looking for an apartment"
    assert ("user", "Hi") in second_history  # el segundo turno ve el primero
    with session_scope(engine) as session:  # y los dos primeros mensajes no crean dos leads
        assert len(session.execute(select(Lead).where(Lead.telegram_user_id == "tg-serie-1")).all()) == 1


class WaitsForTheOther:
    """Cada chat espera a que el otro empiece: si los chats distintos fueran en serie, no terminaría nunca."""

    name = "espera"

    def __init__(self) -> None:
        self.started = {"tg-paralelo-a": asyncio.Event(), "tg-paralelo-b": asyncio.Event()}

    async def run(self, turn, catalog):
        me = turn.text
        self.started[me].set()
        other = next(chat for chat in self.started if chat != me)
        await asyncio.wait_for(self.started[other].wait(), timeout=5)
        return AgentResult(text="ok", intent="otro")


def test_los_mensajes_de_chats_distintos_siguen_en_paralelo(engine):
    async def dos_chats():
        agent = WaitsForTheOther()
        return await asyncio.gather(*(reply_to(engine, StubCatalog(), telegram_user_id=chat, text=chat, agent=agent)
                                      for chat in ("tg-paralelo-a", "tg-paralelo-b")))

    assert all(reply.endswith("ok") for reply in asyncio.run(dos_chats()))
