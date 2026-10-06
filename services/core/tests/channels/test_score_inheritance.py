"""#84: la conversación nueva de un lead existente hereda lo que es del lead, no lo pierde."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, update

from app.channels.identity import resolve_identity
from app.channels.telegram import reply_to
from db.models import Conversation, FsmContext, Lead, LeadScore
from db.session import session_scope

CALIFICA = "My budget is 3 million, I decide and it's urgent"
SIGNAL = {"budget_hint": "budget is 3 million", "decision_maker": "declarado", "urgency": "inmediata",
          "matching_properties": 2, "meets_requirements": True}


class StubCatalog:
    async def call(self, tool, arguments=None):
        if tool == "get_availability":
            return {"property_id": "prop_00231", "status": "disponible", "checked_at": "2026-01-01T00:00:00Z"}
        return {"properties": [{"id": "prop_00231", "title": "Depa en El Poblado",
                                "zone": "El Poblado", "status": "disponible"}]}

    async def tool_specs(self):
        return []


def _decir(engine, lead: str, texto: str) -> str:
    return asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead, text=texto))


def _score(engine, lead: str) -> LeadScore:
    with session_scope(engine) as session:
        lead_id = session.execute(select(Lead.id).where(Lead.telegram_user_id == lead)).scalar_one()
        return session.get(LeadScore, lead_id)


def _terminar(engine, conversation_id: uuid.UUID, estado: str = "HUMAN_HANDOFF") -> None:
    with session_scope(engine) as session:
        session.execute(update(Conversation).where(Conversation.id == conversation_id).values(fsm_state=estado))


def _con_contexto(engine, conversation_id: uuid.UUID, **values) -> None:
    with session_scope(engine) as session:
        session.execute(update(FsmContext).where(FsmContext.conversation_id == conversation_id).values(**values))


def test_la_conversacion_nueva_hereda_la_senal_del_score_y_la_calificacion(engine):
    primera = resolve_identity(engine, "tg-herencia")
    _con_contexto(engine, primera.conversation_id, score_signal=SIGNAL, lead_qualified=True)
    _terminar(engine, primera.conversation_id)

    segunda = resolve_identity(engine, "tg-herencia")

    assert segunda.conversation_id != primera.conversation_id
    with session_scope(engine) as session:
        contexto = session.get(FsmContext, segunda.conversation_id)
        assert contexto.score_signal == SIGNAL and contexto.lead_qualified is True
        # Lo que es de la conversación sí arranca limpio.
        assert contexto.unresolved_streak == 0 and contexto.last_tool_results == {}


def test_hereda_de_la_ultima_conversacion_y_no_de_una_mas_vieja(engine):
    primera = resolve_identity(engine, "tg-herencia-ultima")
    _con_contexto(engine, primera.conversation_id, lead_qualified=False)
    _terminar(engine, primera.conversation_id, "LOST")
    segunda = resolve_identity(engine, "tg-herencia-ultima")
    _con_contexto(engine, segunda.conversation_id, score_signal=SIGNAL, lead_qualified=True)
    _terminar(engine, segunda.conversation_id)

    tercera = resolve_identity(engine, "tg-herencia-ultima")

    with session_scope(engine) as session:
        contexto = session.get(FsmContext, tercera.conversation_id)
        assert contexto.score_signal == SIGNAL and contexto.lead_qualified is True


def test_la_primera_conversacion_de_un_lead_arranca_con_los_valores_por_defecto(engine):
    identity = resolve_identity(engine, "tg-primera-vez")

    with session_scope(engine) as session:
        contexto = session.get(FsmContext, identity.conversation_id)
        assert contexto.lead_qualified is False
        assert contexto.score_signal["budget_hint"] is None and contexto.score_signal["matching_properties"] == 0


def test_una_conversacion_viva_no_se_toca(engine):
    primera = resolve_identity(engine, "tg-viva")
    _con_contexto(engine, primera.conversation_id, score_signal=SIGNAL)

    segunda = resolve_identity(engine, "tg-viva")

    assert segunda.conversation_id == primera.conversation_id
    with session_scope(engine) as session:
        assert session.get(FsmContext, primera.conversation_id).score_signal == SIGNAL


def test_el_lead_que_vuelve_tras_el_traspaso_no_baja_a_frio(engine):
    """El caso visto en vivo: WARM 60, traspaso y un mensaje más dejaban `lead_scores` en COLD 0."""
    lead = f"tg-{uuid.uuid4().hex[:8]}"
    _decir(engine, lead, CALIFICA)
    antes = _score(engine, lead)
    assert antes.band in ("WARM", "HOT")
    _decir(engine, lead, "I want to talk to an agent")  # HUMAN_HANDOFF: la conversación termina

    _decir(engine, lead, "Hi again")  # abre la conversación nueva

    despues = _score(engine, lead)
    assert despues.total >= antes.total and despues.band == antes.band


def test_el_lead_calificado_que_vuelve_sigue_marcado_como_calificado(engine):
    lead = f"tg-{uuid.uuid4().hex[:8]}"
    _decir(engine, lead, CALIFICA)
    _decir(engine, lead, "I want to talk to an agent")

    _decir(engine, lead, "Hi again")

    with session_scope(engine) as session:
        lead_id = session.execute(select(Lead.id).where(Lead.telegram_user_id == lead)).scalar_one()
        ultima = session.execute(select(Conversation).where(Conversation.lead_id == lead_id)
                                 .order_by(Conversation.started_at.desc())).scalars().first()
        assert session.get(FsmContext, ultima.id).lead_qualified is True
