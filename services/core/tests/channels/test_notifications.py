"""B-7: registro del asesor por deep-link y tarjeta por Telegram al calificar."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy import select, text

from app.channels import advisor_registration, canonical
from app.channels.notifications import LeadCard, pick_advisor, render
from app.channels.telegram import ChannelReply, reply_to, reply_turn, run_after_reply
from db.models import Advisor, AdvisorNotification, Lead
from db.session import session_scope

CALIFICA = "My budget is 3 million, I decide and it's urgent"


class StubCatalog:
    async def call(self, tool, arguments=None):
        if tool == "get_availability":
            return {"property_id": "prop_00231", "status": "disponible", "checked_at": "2026-01-01T00:00:00Z"}
        return {"properties": [{"id": "prop_00231", "title": "Depa en El Poblado",
                                "zone": "El Poblado", "status": "disponible"}]}

    async def tool_specs(self):
        return []


def _advisor(engine, *, zone: str, chat_id: str | None = None) -> Advisor:
    with session_scope(engine) as session:
        advisor = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name=f"Asesor {zone}", zone=zone,
                          access_token=uuid.uuid4().hex, telegram_chat_id=chat_id)
        session.add(advisor)
        session.flush()
        return advisor


def _limpiar_asesores(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("UPDATE leads SET advisor_id = NULL"))
        connection.execute(text("DELETE FROM advisors"))


# --- deep-link de registro ---------------------------------------------------------------------

def test_el_prefijo_adv_da_el_token_y_los_otros_no():
    assert canonical.advisor_token("adv_abc123") == "abc123"
    assert canonical.advisor_token("prop_00231") is None
    assert canonical.advisor_token("adv_") is None
    assert canonical.advisor_token(None) is None


def test_un_token_valido_liga_el_chat_al_asesor(engine):
    advisor = _advisor(engine, zone="El Poblado")

    reply = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="chat-777", text="",
                                 deep_link=f"adv_{advisor.access_token}"))

    assert "I'll let you know here" in reply
    with session_scope(engine) as session:
        assert session.get(Advisor, advisor.id).telegram_chat_id == "chat-777"
        # Registrarse no crea un lead: un asesor no es un prospecto.
        assert session.execute(select(Lead).where(Lead.telegram_user_id == "chat-777")).first() is None


def test_un_token_invalido_no_registra_ni_dice_por_que(engine):
    reply = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="chat-778", text="",
                                 deep_link="adv_token-inventado"))

    assert reply == advisor_registration.UNKNOWN_TOKEN_REPLY


def test_un_chat_pertenece_a_un_solo_asesor(engine):
    primero = _advisor(engine, zone="Usaquen", chat_id="chat-779")
    segundo = _advisor(engine, zone="Envigado")

    advisor_registration.register(engine, access_token=segundo.access_token, chat_id="chat-779")

    with session_scope(engine) as session:
        assert session.get(Advisor, primero.id).telegram_chat_id is None
        assert session.get(Advisor, segundo.id).telegram_chat_id == "chat-779"


# --- el chat registrado del asesor no es un lead (#211) -----------------------------------------

def _leads_del_chat(engine, chat_id: str) -> list[Lead]:
    with session_scope(engine) as session:
        return list(session.execute(select(Lead).where(Lead.telegram_user_id == chat_id)).scalars())


def test_lo_que_escribe_el_asesor_en_su_chat_no_abre_un_turno_de_lead(engine, monkeypatch):
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://panel.example.com/")
    advisor = _advisor(engine, zone="Laureles", chat_id="chat-790")

    for texto, deep_link in (("Hi, any new leads?", None), ("", "prop_00231")):
        reply = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="chat-790", text=texto,
                                     deep_link=deep_link))
        assert reply == advisor_registration.advisor_chat_reply(advisor)
        assert f"https://panel.example.com/panel/{advisor.access_token}" in reply and "/stop" in reply

    # Ni lead ni conversación (y sin lead tampoco hay funnel), y el chat sigue recibiendo los avisos.
    assert _leads_del_chat(engine, "chat-790") == []
    with session_scope(engine) as session:
        assert session.get(Advisor, advisor.id).telegram_chat_id == "chat-790"


def test_con_stop_el_asesor_desliga_su_chat_y_despues_puede_escribir_como_lead(engine):
    advisor = _advisor(engine, zone="Chapinero", chat_id="chat-791")

    reply = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="chat-791", text="/stop"))

    assert reply == advisor_registration.unregistered_reply(advisor) and advisor.access_token in reply
    with session_scope(engine) as session:
        assert session.get(Advisor, advisor.id).telegram_chat_id is None
    assert _leads_del_chat(engine, "chat-791") == []  # la baja del asesor no es la de un lead

    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="chat-791", text="Hi, I'm looking for a house"))
    assert len(_leads_del_chat(engine, "chat-791")) == 1


def test_el_chat_de_un_lead_sigue_como_siempre(engine):
    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id="chat-792", text="Hi, I'm looking for a house"))
    assert len(_leads_del_chat(engine, "chat-792")) == 1


# --- elección del asesor y tarjeta -------------------------------------------------------------

def test_se_prefiere_el_asesor_de_la_zona_y_si_no_hay_cualquiera(engine):
    _limpiar_asesores(engine)
    usaquen = _advisor(engine, zone="Usaquen", chat_id="chat-z")
    poblado = _advisor(engine, zone="El Poblado")  # sin Telegram también cuenta (B-15)

    with session_scope(engine) as session:
        assert pick_advisor(session, "El Poblado").id == poblado.id
        assert pick_advisor(session, "Laureles").id in {usaquen.id, poblado.id}  # cola general
    _limpiar_asesores(engine)
    with session_scope(engine) as session:
        assert pick_advisor(session, "El Poblado") is None


def test_la_tarjeta_degrada_sin_propiedad_ni_visita():
    card = LeadCard(lead_id=uuid.uuid4(), zone=None, score_total=60, score_band="WARM",
                    property_title=None, property_id=None, visit_at=None, reasons=())
    texto = render(card)

    assert "WARM · 60/100" in texto
    assert "not set" in texto
    assert "Propiedad" not in texto and "Visita" not in texto


def test_la_tarjeta_completa_lleva_propiedad_visita_y_razones():
    card = LeadCard(lead_id=uuid.uuid4(), zone="El Poblado", score_total=80, score_band="HOT",
                    property_title="Depa en El Poblado", property_id="prop_00231",
                    visit_at="Fri Sep 25, 10:00", reasons=("Budget: budget stated",))
    texto = render(card)

    assert "Depa en El Poblado (prop_00231)" in texto
    assert "Scheduled visit: Fri Sep 25, 10:00" in texto
    assert "Why: Budget: budget stated" in texto


# --- de punta a punta por el canal -------------------------------------------------------------

def test_al_calificar_el_asesor_de_la_zona_recibe_la_tarjeta_una_vez(engine):
    _limpiar_asesores(engine)
    advisor = _advisor(engine, zone="El Poblado", chat_id="chat-valeria")
    bot = AsyncMock()
    lead = f"tg-{uuid.uuid4().hex[:8]}"

    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead,
                         text="I'm looking for an apartment", bot=bot))   # fija la zona
    # El turno que califica también busca: así la tarjeta lleva la propiedad de interés.
    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead,
                         text=f"I'm looking for an apartment. {CALIFICA}", bot=bot))
    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead, text="Is it still available?",
                         bot=bot))

    bot.send_message.assert_awaited_once()
    kwargs: dict[str, Any] = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == "chat-valeria"
    assert "Qualified lead (WARM" in kwargs["text"]
    assert "Depa en El Poblado" in kwargs["text"]
    with session_scope(engine) as session:
        row = session.execute(select(Lead).where(Lead.telegram_user_id == lead)).scalar_one()
    assert row.status == "mql" and row.advisor_id == advisor.id


def test_la_propiedad_que_el_lead_miro_antes_sigue_en_la_tarjeta(engine):
    """`last_tool_results` se sobrescribe cada turno (A-9), pero la propiedad de interés se arrastra
    (B-8): la tarjeta la muestra aunque el turno que califica no la haya mirado."""
    _limpiar_asesores(engine)
    _advisor(engine, zone="El Poblado", chat_id="chat-valeria")
    bot = AsyncMock()
    lead = f"tg-{uuid.uuid4().hex[:8]}"

    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead,
                         text="I'm looking for an apartment", bot=bot))
    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead, text=CALIFICA, bot=bot))

    texto = bot.send_message.await_args.kwargs["text"]
    assert "Area: El Poblado" in texto
    assert "Property of interest: Depa en El Poblado (prop_00231)" in texto


def test_sin_telegram_el_turno_sigue_y_el_aviso_queda_en_el_panel(engine):
    _limpiar_asesores(engine)
    offline = _advisor(engine, zone="El Poblado")  # existe, pero nunca abrió el bot
    bot = AsyncMock()
    lead = f"tg-{uuid.uuid4().hex[:8]}"

    reply = asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead, text=CALIFICA, bot=bot))

    assert reply
    bot.send_message.assert_not_awaited()
    with session_scope(engine) as session:
        row = session.execute(select(Lead).where(Lead.telegram_user_id == lead)).scalar_one()
    assert row.status == "mql" and row.advisor_id == offline.id
    with session_scope(engine) as session:
        kinds = session.execute(select(AdvisorNotification.kind)
                                .where(AdvisorNotification.lead_id == row.id)).scalars().all()
    assert kinds == ["calificado"]


# --- #231: la respuesta al lead no espera los avisos ----------------------------------------------------

def test_la_respuesta_sale_antes_del_aviso_al_asesor(engine):
    _limpiar_asesores(engine)
    _advisor(engine, zone="El Poblado", chat_id="chat-valeria")
    bot = AsyncMock()
    lead = f"tg-{uuid.uuid4().hex[:8]}"
    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead, text="I'm looking for an apartment", bot=bot))

    reply = asyncio.run(reply_turn(engine, StubCatalog(), telegram_user_id=lead, text=CALIFICA, bot=bot))

    assert reply.text and reply.after_reply is not None
    bot.send_message.assert_not_awaited()  # el turno ya respondió y el aviso sigue pendiente
    asyncio.run(run_after_reply(reply))
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == "chat-valeria"


def test_un_fallo_en_lo_pendiente_no_llega_al_lead(engine, caplog):
    async def falla() -> None:
        raise RuntimeError("Telegram caído")

    asyncio.run(run_after_reply(ChannelReply("hola", after_reply=falla)))  # no lanza
    assert "fallo lo pendiente del turno" in caplog.text


def test_un_turno_sin_avisos_no_deja_nada_pendiente(engine):
    reply = asyncio.run(reply_turn(engine, StubCatalog(), telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}",
                                   text="Hi, I'm looking for a house"))
    assert reply.after_reply is None
