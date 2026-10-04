"""B-2: aviso de privacidad en el primer turno y baja determinista, sin pasar por el LLM."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select

from app.channels import telegram
from app.channels.consent import (
    OPT_OUT_CONFIRMATION,
    PRIVACY_NOTICE,
    is_first_turn,
    is_opt_out,
    with_privacy_notice,
    withdraw_consent,
)
from app.channels.identity import resolve_identity
from app.channels.telegram import reply_to
from db.models import Lead
from db.session import session_scope


class StubCatalog:
    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"properties": []}


@pytest.mark.parametrize("text", [
    "STOP", "stop", "/stop", "Baja", "no me escriban más", "quiero darme de baja",
    "cancelar suscripción", "borren mis datos",
])
def test_frases_de_baja_reconocidas(text):
    assert is_opt_out(text)


@pytest.mark.parametrize("text", [
    "hola", "", "busco departamento", "me interesa la propiedad", "¿cuánto cuesta?",
])
def test_mensajes_normales_no_son_baja(text):
    assert not is_opt_out(text)


def test_with_privacy_notice_antepone_el_aviso():
    assert with_privacy_notice("respuesta").startswith(PRIVACY_NOTICE)


def test_withdraw_consent_marca_la_fila_del_lead(engine):
    identity = resolve_identity(engine, "tg-consent-1")
    withdraw_consent(engine, identity.lead_id)

    with session_scope(engine) as session:
        lead = session.execute(select(Lead).where(Lead.id == identity.lead_id)).scalar_one()
    assert lead.consent_status == "withdrawn"
    assert lead.status == "closed_lost"


def test_is_first_turn_solo_es_cierto_antes_del_primer_turno(engine):
    identity = resolve_identity(engine, "tg-consent-2")
    assert is_first_turn(engine, identity.conversation_id)

    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-consent-2", text="hola"))
    assert not is_first_turn(engine, identity.conversation_id)


def test_la_baja_corta_antes_del_llm(engine, monkeypatch):
    """El AC pide que el opt-out nunca dependa del modelo: `process_turn_reply` no debe ejecutarse."""
    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-consent-3", text="hola"))

    async def no_debe_llamarse(*args, **kwargs):
        raise AssertionError("el opt-out no puede pasar por el turno del LLM")

    monkeypatch.setattr(telegram, "process_turn_reply", no_debe_llamarse)
    reply = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="tg-consent-3",
                                 text="STOP"))

    assert reply == OPT_OUT_CONFIRMATION
    with session_scope(engine) as session:
        lead = session.execute(
            select(Lead).where(Lead.telegram_user_id == "tg-consent-3")
        ).scalar_one()
    assert lead.consent_status == "withdrawn"
