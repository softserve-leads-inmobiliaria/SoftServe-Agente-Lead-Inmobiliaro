"""B-1: el bot traduce un mensaje de Telegram a un turno real y responde."""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

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
    reply = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-bot-1", text="hola"))

    assert reply
    with session_scope(engine) as session:
        lead = session.execute(select(Lead).where(Lead.telegram_user_id == "tg-bot-1")).scalar_one()
        conversation = session.execute(
            select(Conversation).where(Conversation.lead_id == lead.id)
        ).scalar_one()
    assert conversation.fsm_state == "DISCOVERY"  # GREETING + saludo avanza la FSM


def test_el_deep_link_de_propiedad_llega_al_turno(engine, monkeypatch):
    capturados: list[dict[str, Any]] = []
    real_process_turn = telegram.process_turn

    async def spy(engine_, event, catalog_, agent_=None, on_qualified=None):
        capturados.append(dict(event))
        return await real_process_turn(engine_, event, catalog_, agent_, on_qualified)

    monkeypatch.setattr(telegram, "process_turn", spy)
    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-bot-2", text="",
                         deep_link="prop_00231"))

    assert capturados[0]["property_id"] == "prop_00231"


def test_el_primer_turno_incluye_el_aviso_de_privacidad(engine):
    primera = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-bot-3", text="hola"))
    segunda = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-bot-3",
                                   text="hola de nuevo"))

    assert PRIVACY_NOTICE in primera
    assert PRIVACY_NOTICE not in segunda


def test_sin_token_el_bot_no_arranca(engine, monkeypatch):
    """Es lo que permite que CI, el arnés y los tests corran sin credenciales de Telegram."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert asyncio.run(start_bot(engine=engine, catalog=StubCatalog())) is None


def test_la_aplicacion_registra_los_handlers(engine):
    application = build_application("123:fake-token", engine=engine, catalog=StubCatalog())
    assert len(application.handlers[0]) == 1  # un solo manejador para todo texto, comandos incluidos
