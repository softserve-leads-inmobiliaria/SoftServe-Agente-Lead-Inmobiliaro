"""A-12: la ficha del traspaso (motivo, score, propiedad, visita y transcripción completa) al asesor."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

from sqlalchemy import select, text

from app.channels.notifications import (
    TELEGRAM_MAX_CHARS,
    HandoffFile,
    LeadCard,
    render_handoff,
    split_lines,
)
from app.channels.telegram import reply_to
from db.models import Advisor, Lead
from db.session import session_scope

CALIFICA = "Tengo presupuesto de 3 millones, decido yo y es urgente"
PIDE_HUMANO = "Quiero hablar con un asesor"


class StubCatalog:
    async def call(self, tool, arguments=None):
        if tool == "get_availability":
            return {"property_id": "prop_00231", "status": "disponible", "checked_at": "2026-01-01T00:00:00Z"}
        return {"properties": [{"id": "prop_00231", "title": "Depa en Providencia",
                                "zone": "Providencia", "status": "disponible"}]}

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


def _decir(engine, lead: str, texto: str, bot) -> str:
    return asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead, text=texto, bot=bot))


def _mensajes(bot) -> list[str]:
    return [call.kwargs["text"] for call in bot.send_message.await_args_list]


def _chats(bot) -> set[str]:
    return {call.kwargs["chat_id"] for call in bot.send_message.await_args_list}


def _nuevo_lead() -> str:
    return f"tg-{uuid.uuid4().hex[:8]}"


# --- render y troceado (sin base de datos) -----------------------------------------------------

def test_la_ficha_lleva_motivo_score_propiedad_visita_y_la_transcripcion_en_orden():
    card = LeadCard(lead_id=uuid.uuid4(), zone="Providencia", score_total=80, score_band="HOT",
                    property_title="Depa en Providencia", property_id="prop_00231",
                    visit_at="25/09/2026 10:00", reasons=("presupuesto declarado",))
    ficha = HandoffFile(card=card, fsm_state="HUMAN_HANDOFF", reason="solicitud_humano",
                        transcript=(("user", "Hola"), ("assistant", "¡Hola!"), ("user", "Quiero un asesor")))

    resumen, transcripcion = render_handoff(ficha)

    assert "Traspaso de lead (HUMAN_HANDOFF): el lead pidió hablar con una persona" in resumen
    assert "HOT · 80/100" in resumen and "Depa en Providencia (prop_00231)" in resumen
    assert "Visita agendada: 25/09/2026 10:00" in resumen
    assert transcripcion == "Transcripción:\nLead: Hola\nAgente: ¡Hola!\nLead: Quiero un asesor"


def test_una_conversacion_sin_mensajes_lo_dice_en_vez_de_omitir_la_transcripcion():
    card = LeadCard(lead_id=uuid.uuid4(), zone=None, score_total=0, score_band="COLD",
                    property_title=None, property_id=None, visit_at=None, reasons=())

    partes = render_handoff(HandoffFile(card=card, fsm_state="HANDOFF", reason="visit_scheduled",
                                        transcript=()))

    assert partes[-1] == "Transcripción: sin mensajes."
    assert "Propiedad" not in partes[0] and "Visita" not in partes[0]


def test_una_transcripcion_larga_se_parte_sin_perder_ni_una_linea():
    lineas = [f"Lead: mensaje {i} " + "x" * 300 for i in range(40)]  # ~12 mil caracteres

    partes = split_lines(lineas, TELEGRAM_MAX_CHARS)

    assert len(partes) > 1
    assert all(len(parte) <= TELEGRAM_MAX_CHARS for parte in partes)
    assert "\n".join(partes).split("\n") == lineas


def test_una_sola_linea_mas_larga_que_el_tope_se_corta_sin_perder_texto():
    linea = "Lead: " + "y" * (TELEGRAM_MAX_CHARS * 2 + 10)

    partes = split_lines(["Transcripción:", linea], TELEGRAM_MAX_CHARS)

    assert all(len(parte) <= TELEGRAM_MAX_CHARS for parte in partes)
    assert "".join(partes).replace("Transcripción:", "", 1) == linea


# --- de punta a punta por el canal -------------------------------------------------------------

def test_al_pedir_un_humano_el_asesor_recibe_la_ficha_una_sola_vez_con_toda_la_conversacion(engine):
    _limpiar_asesores(engine)
    advisor = _advisor(engine, zone="Providencia", chat_id="chat-valeria")
    bot = AsyncMock()
    lead = _nuevo_lead()

    _decir(engine, lead, "Busco un departamento", bot)
    bot.send_message.assert_not_awaited()  # un turno sin traspaso no manda nada
    _decir(engine, lead, PIDE_HUMANO, bot)

    assert _chats(bot) == {"chat-valeria"}
    resumen, *transcripcion = _mensajes(bot)
    assert "Traspaso de lead (HUMAN_HANDOFF)" in resumen and "pidió hablar con una persona" in resumen
    assert "Zona: Providencia" in resumen
    completa = "\n".join(transcripcion)
    # Incluye el turno del traspaso: mensaje del lead y respuesta del agente, en orden.
    assert completa.index("Lead: Busco un departamento") < completa.index(f"Lead: {PIDE_HUMANO}")
    assert "Agente: Claro, te comunico con un asesor" in completa
    with session_scope(engine) as session:
        assigned = session.execute(select(Lead.advisor_id).where(Lead.telegram_user_id == lead)).scalar()
    assert assigned == advisor.id

    enviados = len(_mensajes(bot))
    _decir(engine, lead, "gracias", bot)  # ya en estado terminal: no hay segundo traspaso
    assert len(_mensajes(bot)) == enviados


def test_la_frustracion_manda_la_ficha_en_el_turno_que_completa_la_racha(engine):
    _limpiar_asesores(engine)
    _advisor(engine, zone="Providencia", chat_id="chat-valeria")
    bot = AsyncMock()
    lead = _nuevo_lead()

    _decir(engine, lead, "ok", bot)
    _decir(engine, lead, "ok", bot)
    bot.send_message.assert_not_awaited()
    _decir(engine, lead, "ok", bot)

    assert "frustración" in _mensajes(bot)[0]


def test_una_visita_agendada_manda_la_ficha_del_traspaso_normal(engine):
    _limpiar_asesores(engine)
    _advisor(engine, zone="Providencia", chat_id="chat-valeria")
    bot = AsyncMock()
    lead = _nuevo_lead()

    for texto in ("Hola", "Busco un departamento", "¿Cuál es el precio?", "Quiero agendar una visita"):
        _decir(engine, lead, texto, bot)
    bot.send_message.assert_not_awaited()
    _decir(engine, lead, "Agendemos la visita mañana", bot)

    assert "Traspaso de lead (HANDOFF)" in _mensajes(bot)[0]
    assert "visita agendada" in _mensajes(bot)[0]


def test_el_traspaso_va_al_asesor_que_ya_tenia_el_lead_no_a_otro(engine):
    _limpiar_asesores(engine)
    _advisor(engine, zone="Providencia", chat_id="chat-valeria")
    otro = _advisor(engine, zone="Zapopan", chat_id="chat-otro")
    bot = AsyncMock()
    lead = _nuevo_lead()
    _decir(engine, lead, "Busco un departamento", bot)
    _decir(engine, lead, CALIFICA, bot)  # califica: la tarjeta va al asesor de la zona
    with engine.begin() as connection:  # el lead pasa a otro asesor (p. ej. reasignación manual)
        connection.execute(text("UPDATE leads SET advisor_id = :a WHERE telegram_user_id = :t"),
                           {"a": otro.id, "t": lead})
    bot.reset_mock()

    _decir(engine, lead, PIDE_HUMANO, bot)

    assert _chats(bot) == {"chat-otro"}


def test_calificar_y_pasar_a_humano_en_el_mismo_turno_manda_una_sola_notificacion(engine):
    _limpiar_asesores(engine)
    advisor = _advisor(engine, zone="Providencia", chat_id="chat-valeria")
    bot = AsyncMock()
    lead = _nuevo_lead()

    _decir(engine, lead, f"{CALIFICA}. {PIDE_HUMANO}", bot)

    textos = _mensajes(bot)
    assert "Traspaso de lead" in textos[0]
    assert not any("Lead calificado" in texto for texto in textos)
    with session_scope(engine) as session:
        row = session.execute(select(Lead).where(Lead.telegram_user_id == lead)).scalar_one()
    assert row.status == "mql" and row.qualified_at is not None and row.advisor_id == advisor.id


def test_sin_asesor_registrado_el_turno_del_traspaso_sigue_y_no_manda_nada(engine):
    _limpiar_asesores(engine)
    _advisor(engine, zone="Providencia")  # existe, pero nunca abrió el bot
    bot = AsyncMock()

    reply = _decir(engine, _nuevo_lead(), PIDE_HUMANO, bot)

    assert "asesor" in reply
    bot.send_message.assert_not_awaited()


def test_si_telegram_falla_al_enviar_la_ficha_el_lead_igual_recibe_su_respuesta(engine):
    _limpiar_asesores(engine)
    _advisor(engine, zone="Providencia", chat_id="chat-valeria")
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("Telegram caído")

    reply = _decir(engine, _nuevo_lead(), PIDE_HUMANO, bot)

    assert "asesor" in reply
