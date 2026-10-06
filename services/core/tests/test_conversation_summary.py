"""A-16: el resumen de la conversación para el asesor (qué busca, objeciones y siguiente paso)."""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import pytest
from bedrock_fakes import ScriptedBedrock, text
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy import text as sa_text

from app import summary
from app.agent import BedrockAgent, FakeAgent
from app.bedrock_llm import SUMMARY_SYSTEM, BedrockLLM, LLMError, LLMFormatError
from app.channels import canonical
from app.channels.telegram import reply_to
from app.input_guard import mentions_protected
from app.rate_limit import RateLimits
from app.summary import SummaryFacts, render, template, violations
from app.turn import process_turn
from db.models import Advisor, AdvisorNotification, Conversation, Lead
from db.session import session_scope

CHAT, FAST = "modelo-chat", "modelo-rapido"
PIDE_HUMANO = "I want to talk to an agent"
TRANSCRIPT = (
    ("user", "Busco una casa en El Poblado de 3 recámaras, tengo 4 millones"),
    ("assistant", "Tengo una casa en El Poblado por 3,950,000."),
    ("user", "Me parece cara y la quiero para marzo"),
)
FACTS = SummaryFacts(reason="solicitud_humano", zone="El Poblado", budget="4 millones", timing="3_meses")


def run(coro):
    return asyncio.run(coro)


def sections(**overrides):
    return {"busca": "Casa de 3 recámaras en El Poblado, presupuesto de 4 millones.",
            "objeciones": "Le parece cara.",
            "siguiente_paso": "Llamarle para revisar opciones."} | overrides


class Writes:
    """Agente que devuelve el resumen dado (o lanza) y guarda lo que recibió."""

    name = "writes"

    def __init__(self, result=None, error=None, delay=0.0):
        self.result, self.error, self.delay = result, error, delay
        self.seen = []

    async def summarize(self, transcript, facts):
        self.seen.append((transcript, facts))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


# --- la plantilla del agente fake ----------------------------------------------------------------------

def test_the_template_says_what_the_lead_wants_with_the_lead_data():
    result = template(FACTS)
    assert result["busca"] == "Zona El Poblado; presupuesto 4 millones; plazo 3 meses."
    assert result["objeciones"] == summary.NO_OBJECTIONS_TEMPLATE


def test_the_template_without_data_does_not_invent_anything():
    assert template(SummaryFacts(reason="frustration"))["busca"] == "Todavía no lo dijo."


@pytest.mark.parametrize("facts,expected", [
    (SummaryFacts(reason="solicitud_humano"), "Contactar al lead: pidió hablar con una persona."),
    (SummaryFacts(reason="fuera_de_alcance"), "Revisar su consulta: está fuera del alcance del agente."),
    (SummaryFacts(reason="frustration"), "Retomar la conversación: el agente no resolvió varios turnos seguidos."),
    (SummaryFacts(reason="visit_scheduled", visit_at="05/10/2026 11:00"), "Confirmar la visita del 05/10/2026 11:00."),
    (SummaryFacts(reason="visit_requested", preferred_time="el sábado en la mañana"),
     "Coordinar el horario de la visita: el lead prefiere el sábado en la mañana."),
    (SummaryFacts(reason="otro_motivo"), "Contactar al lead."),
])
def test_the_next_step_follows_the_handoff_reason(facts, expected):
    assert template(facts)["siguiente_paso"] == expected


def test_the_template_is_deterministic_and_the_fake_agent_uses_it():
    assert run(FakeAgent().summarize("cualquier cosa", FACTS)) == template(FACTS) == template(FACTS)


def test_the_summary_keeps_the_three_sections_in_order():
    assert render(sections()).split("\n") == [
        "Qué busca: Casa de 3 recámaras en El Poblado, presupuesto de 4 millones.",
        "Objeciones: Le parece cara.",
        "Siguiente paso: Llamarle para revisar opciones.",
    ]


# --- guardrails de salida -------------------------------------------------------------------------------

def test_numbers_from_the_conversation_and_from_the_lead_data_are_allowed():
    assert violations(render(sections()), TRANSCRIPT, FACTS) == []
    assert violations("Qué busca: algo por 3,950,000.", TRANSCRIPT, FACTS) == []
    assert violations("Visita el 05/10/2026 11:00.", (), SummaryFacts(reason="visit_scheduled",
                                                                       visit_at="05/10/2026 11:00")) == []


def test_an_invented_number_is_a_violation():
    assert violations("Qué busca: algo de 2 millones.", TRANSCRIPT, FACTS) == ["number"]


def test_an_exact_address_is_a_violation_unless_the_lead_is_qualified():
    text_ = "Siguiente paso: llevarlo a Calle Morelos 123."
    assert violations(text_, TRANSCRIPT, FACTS) == ["number", "address"]
    qualified = SummaryFacts(reason="solicitud_humano", lead_qualified=True)
    assert "address" not in violations(text_, TRANSCRIPT, qualified)


def test_an_unauthorised_promise_is_a_violation():
    assert "promise" in violations("Siguiente paso: decirle que su crédito está aprobado.", TRANSCRIPT, FACTS)


@pytest.mark.parametrize("value", [
    "El lead es colombiano.", "Es casado.", "Decide con su esposa.", "Lo consulta con sus hijos.",
    "Busca algo cerca de su religión.",
])
def test_a_protected_category_is_a_violation(value):
    assert "protected_category" in violations(value, TRANSCRIPT, FACTS)


@pytest.mark.parametrize("value", ["Decide con su pareja.", "Lo consulta con su familia."])
def test_the_neutral_forms_left_by_the_input_guard_are_allowed(value):
    assert violations(value, TRANSCRIPT, FACTS) == []


def test_mentions_protected_reads_the_same_rules_as_the_input_guard():
    assert mentions_protected("Soy venezolana") and not mentions_protected("Busco casa en Usaquen")


# --- generar y validar -----------------------------------------------------------------------------------

def test_an_agent_without_summarize_gets_the_template():
    class NoSummary:
        name = "sin-resumen"

    assert run(summary.summarize(NoSummary(), TRANSCRIPT, FACTS)) == render(template(FACTS))


def test_the_agent_gets_the_transcript_as_text_and_the_facts():
    agent = Writes(sections())
    assert run(summary.summarize(agent, TRANSCRIPT, FACTS)) == render(sections())
    transcript, facts = agent.seen[0]
    assert transcript.split("\n")[0] == "Lead: Busco una casa en El Poblado de 3 recámaras, tengo 4 millones"
    assert "Agente: Tengo una casa" in transcript and facts == FACTS


def test_only_the_most_recent_messages_go_to_the_llm():
    long = tuple(("user", f"mensaje {i}") for i in range(summary.SUMMARY_MAX_MESSAGES + 5))
    lines = summary.transcript_text(long).split("\n")
    assert len(lines) == summary.SUMMARY_MAX_MESSAGES and lines[-1] == f"Lead: mensaje {len(long) - 1}"


def test_a_failing_llm_leaves_the_file_without_summary():
    assert run(summary.summarize(Writes(error=LLMError("caído")), TRANSCRIPT, FACTS)) is None


def test_a_slow_llm_does_not_hang_the_turn(monkeypatch):
    monkeypatch.setattr(summary, "SUMMARY_TIMEOUT_SECONDS", 0.01)
    assert run(summary.summarize(Writes(sections(), delay=1.0), TRANSCRIPT, FACTS)) is None


@pytest.mark.parametrize("result", [None, {}, sections(objeciones=""), {"busca": "x", "objeciones": "y"}])
def test_an_incomplete_summary_is_dropped(result):
    assert run(summary.summarize(Writes(result), TRANSCRIPT, FACTS)) is None


def test_a_summary_that_fails_the_guardrails_is_dropped_not_regenerated():
    agent = Writes(sections(objeciones="Ninguna; es colombiano."))
    assert run(summary.summarize(agent, TRANSCRIPT, FACTS)) is None
    assert len(agent.seen) == 1


# --- Bedrock ---------------------------------------------------------------------------------------------

def bedrock(*responses):
    client = ScriptedBedrock(*responses)
    return client, BedrockLLM(client, CHAT, FAST, RateLimits(10, 60.0))


def test_bedrock_summarizes_with_the_chat_model_without_tools_and_without_randomness():
    client, llm = bedrock(text(json.dumps(sections())))
    assert run(llm.summarize("Lead: hola", "Zona: El Poblado")) == sections()
    (request,) = client.requests
    assert request["modelId"] == CHAT and "toolConfig" not in request
    assert request["inferenceConfig"]["temperature"] == 0.0
    assert request["system"] == [{"text": SUMMARY_SYSTEM}]
    prompt = request["messages"][0]["content"][0]["text"]
    assert "DATOS DEL LEAD:\nZona: El Poblado" in prompt and "CONVERSACIÓN:\nLead: hola" in prompt


def test_the_summary_prompt_forbids_inventing_promising_and_protected_categories():
    for rule in ("No inventes", "direcciones exactas", "No prometas", "estado civil"):
        assert rule in SUMMARY_SYSTEM


@pytest.mark.parametrize("raw", ["no es json", json.dumps({"busca": "x", "objeciones": "y"}),
                                 json.dumps(sections(siguiente_paso=3))])
def test_a_malformed_bedrock_summary_is_a_format_error(raw):
    _, llm = bedrock(text(raw))
    with pytest.raises(LLMFormatError):
        run(llm.summarize("Lead: hola", ""))


def test_the_bedrock_agent_summary_passes_through_the_guardrails():
    _, llm = bedrock(text(json.dumps(sections())))
    assert run(summary.summarize(BedrockAgent(llm), TRANSCRIPT, FACTS)) == render(sections())


def test_a_bedrock_error_leaves_the_file_without_summary():
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Converse")
    _, llm = bedrock(denied)
    assert run(summary.summarize(BedrockAgent(llm), TRANSCRIPT, FACTS)) is None


# --- de punta a punta -------------------------------------------------------------------------------------

class StubCatalog:
    async def call(self, tool, arguments=None):
        return {"properties": []}

    async def tool_specs(self):
        return []


def _advisor(engine) -> Advisor:
    with engine.begin() as connection:  # el ruteo local elige entre todos: que solo esté este
        connection.execute(sa_text("UPDATE leads SET advisor_id = NULL"))
        connection.execute(sa_text("DELETE FROM advisors"))
    with session_scope(engine) as session:
        advisor = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name="Asesor", zone=f"zona-{uuid.uuid4().hex[:4]}",
                          access_token=uuid.uuid4().hex, telegram_chat_id="chat-resumen")
        session.add(advisor)
        session.flush()
        return advisor


def _say(engine, user, value, bot, agent=None):
    return run(reply_to(engine, StubCatalog(), telegram_user_id=user, text=value, bot=bot, agent=agent))


def _lead(engine, user) -> Lead:
    with session_scope(engine) as session:
        lead = session.execute(select(Lead).where(Lead.telegram_user_id == user)).scalar_one()
        session.expunge(lead)
        return lead


def _notices(engine, user) -> list[str]:
    with session_scope(engine) as session:
        return list(session.execute(
            select(AdvisorNotification.body).join(Lead, Lead.id == AdvisorNotification.lead_id)
            .where(Lead.telegram_user_id == user, AdvisorNotification.kind == "traspaso")).scalars())


def test_the_handoff_saves_the_summary_and_the_file_shows_it_before_the_transcript(engine):
    advisor = _advisor(engine)
    bot = AsyncMock()
    user = f"tg-{uuid.uuid4().hex[:8]}"
    _say(engine, user, "I'm looking for an apartment", bot)
    assert _lead(engine, user).handoff_summary is None  # sin traspaso, sin resumen

    _say(engine, user, PIDE_HUMANO, bot)

    lead = _lead(engine, user)
    assert lead.handoff_summary == render(template(SummaryFacts(reason="solicitud_humano")))
    assert lead.handoff_summary_at is not None
    sent = [call.kwargs["text"] for call in bot.send_message.await_args_list
            if call.kwargs["chat_id"] == advisor.telegram_chat_id]
    first, *rest = sent
    assert "Resumen:\nQué busca:" in first and "Siguiente paso: Contactar al lead" in first
    assert rest and rest[0].startswith("Transcripción:")
    (notice,) = _notices(engine, user)
    assert "Resumen:\nQué busca:" in notice


def test_if_the_summary_fails_the_file_goes_out_without_it_as_before(engine):
    class BrokenSummary(FakeAgent):
        async def summarize(self, transcript, facts):
            raise LLMError("caído")

    _advisor(engine)
    bot = AsyncMock()
    user = f"tg-{uuid.uuid4().hex[:8]}"
    reply = _say(engine, user, PIDE_HUMANO, bot, agent=BrokenSummary())

    assert "agents" in reply
    assert _lead(engine, user).handoff_summary is None
    first = bot.send_message.await_args_list[0].kwargs["text"]
    assert "Traspaso de lead" in first and "Resumen:" not in first


def test_the_summary_is_written_even_without_a_channel_to_notify(engine):
    """El arnés (`POST /events`) no pasa `on_handoff`: el resumen igual queda para el panel."""
    with session_scope(engine) as session:
        lead = Lead(telegram_user_id=f"harness-{uuid.uuid4().hex[:8]}")
        session.add(lead)
        session.flush()
        conversation = Conversation(lead_id=lead.id)
        session.add(conversation)
        session.flush()
        lead_id, conversation_id = lead.id, conversation.id
    event = canonical.to_canonical(lead_id=lead_id, conversation_id=conversation_id, text=PIDE_HUMANO)
    run(process_turn(engine, event, StubCatalog()))
    with session_scope(engine) as session:
        assert session.get(Lead, lead_id).handoff_summary.startswith("Qué busca:")
