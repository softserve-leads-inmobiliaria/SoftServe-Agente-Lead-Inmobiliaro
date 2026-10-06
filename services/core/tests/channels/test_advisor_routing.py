"""A-10: el asesor del lead lo elige la agenda del catálogo (`assign_advisor`), con la regla local de
B-7 como respaldo si la agenda no responde. Desde B-15 (DT-42) el aviso queda siempre en el panel, y
Telegram es un canal adicional."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select, text

from app import scheduling_client
from app.channels import notifications
from app.channels.telegram import reply_to
from app.scheduling_client import SchedulingClient, SchedulingError
from db.models import Advisor, AdvisorNotification, Lead
from db.session import session_scope

CALIFICA = "Tengo presupuesto de 3 millones, decido yo y es urgente"
PIDE_HUMANO = "Quiero hablar con un asesor"
SCORE = {"total": 60, "band": "WARM", "reasons": ["presupuesto declarado"]}


class StubCatalog:
    async def call(self, tool, arguments=None):
        return {"properties": []}

    async def tool_specs(self):
        return []


class FakeAgenda:
    """Hace lo que la agenda del catálogo: asigna `advisor_id` (o falla con `error`) y lo deja escrito."""

    def __init__(self, engine, advisor_id: str | None = None, error: str | None = None) -> None:
        self.engine, self.advisor_id, self.error = engine, advisor_id, error
        self.calls: list[dict[str, Any]] = []

    async def assign_advisor(self, lead_id, zone=None, language="es", registered_only=False):
        self.calls.append({"lead_id": lead_id, "registered_only": registered_only})
        if self.error:
            raise SchedulingError(self.error, "falla simulada")
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE leads SET advisor_id = :a WHERE id = :l AND advisor_id IS NULL"),
                               {"a": self.advisor_id, "l": lead_id})
        return {"lead_id": lead_id, "advisor_id": self.advisor_id, "name": "x", "zone": "x",
                "language": "es", "match": "zone_and_language"}


@pytest.fixture
def clean(engine):
    with engine.begin() as connection:
        connection.execute(text("UPDATE leads SET advisor_id = NULL"))
        connection.execute(text("DELETE FROM advisors"))


def _advisor(engine, *, zone: str, chat_id: str | None = None) -> str:
    with session_scope(engine) as session:
        advisor = Advisor(id=f"adv_{uuid.uuid4().hex[:6]}", name=f"Asesor {zone}", zone=zone,
                          access_token=uuid.uuid4().hex, telegram_chat_id=chat_id)
        session.add(advisor)
        session.flush()
        return advisor.id


def _lead(engine, zone: str = "El Poblado") -> dict[str, str]:
    with session_scope(engine) as session:
        lead = Lead(telegram_user_id=f"tg-{uuid.uuid4().hex[:8]}", zone=zone)
        session.add(lead)
        session.flush()
        return {"lead_id": str(lead.id), "conversation_id": str(uuid.uuid4())}


def _advisor_of(engine, lead_id: str) -> str | None:
    with session_scope(engine) as session:
        return session.get(Lead, uuid.UUID(lead_id)).advisor_id


def _notices(engine, lead_id: str) -> list[tuple[str, str, str]]:
    """(asesor, tipo, texto) de los avisos del lead en el panel, del más viejo al más nuevo."""
    with session_scope(engine) as session:
        rows = session.execute(select(AdvisorNotification).where(AdvisorNotification.lead_id == uuid.UUID(lead_id))
                               .order_by(AdvisorNotification.created_at)).scalars()
        return [(row.advisor_id, row.kind, row.body) for row in rows]


# --- notificaciones -----------------------------------------------------------------------------

def test_the_card_goes_to_the_advisor_the_agenda_chose(engine, clean):
    _advisor(engine, zone="El Poblado", chat_id="chat-local")  # el que elegiría la regla local
    chosen = _advisor(engine, zone="Usaquen", chat_id="chat-agenda")
    agenda, bot, event = FakeAgenda(engine, chosen), AsyncMock(), _lead(engine)

    advisor = asyncio.run(notifications.notify_advisor(engine, event, SCORE, bot, agenda))

    assert advisor.id == chosen
    assert bot.send_message.await_args.kwargs["chat_id"] == "chat-agenda"
    assert agenda.calls == [{"lead_id": event["lead_id"], "registered_only": False}]
    assert _advisor_of(engine, event["lead_id"]) == chosen
    [(advisor_id, kind, body)] = _notices(engine, event["lead_id"])
    assert (advisor_id, kind) == (chosen, "calificado") and "WARM · 60/100" in body and "Ficha:" not in body


def test_without_any_advisor_nobody_is_notified_or_assigned(engine, clean):
    bot, event = AsyncMock(), _lead(engine)

    advisor = asyncio.run(notifications.notify_advisor(engine, event, SCORE, bot,
                                                        FakeAgenda(engine, error="NOT_FOUND")))

    assert advisor is None
    bot.send_message.assert_not_awaited()
    assert _advisor_of(engine, event["lead_id"]) is None and _notices(engine, event["lead_id"]) == []


def test_an_advisor_without_telegram_gets_the_card_in_the_panel(engine, clean):
    offline = _advisor(engine, zone="El Poblado")
    bot, event = AsyncMock(), _lead(engine)

    advisor = asyncio.run(notifications.notify_advisor(engine, event, SCORE, bot, FakeAgenda(engine, offline)))

    assert advisor.id == offline
    bot.send_message.assert_not_awaited()
    assert [(a, kind) for a, kind, _ in _notices(engine, event["lead_id"])] == [(offline, "calificado")]


def test_if_telegram_fails_the_notice_stays_in_the_panel(engine, clean):
    chosen = _advisor(engine, zone="El Poblado", chat_id="chat-agenda")
    bot, event = AsyncMock(), _lead(engine)
    bot.send_message.side_effect = RuntimeError("Telegram caído")

    advisor = asyncio.run(notifications.notify_advisor(engine, event, SCORE, bot, FakeAgenda(engine, chosen)))

    assert advisor.id == chosen
    assert len(_notices(engine, event["lead_id"])) == 1


@pytest.mark.parametrize("error", ["UPSTREAM_UNAVAILABLE", "INVALID_INPUT"])
def test_if_the_agenda_fails_the_local_rule_still_notifies(engine, clean, error):
    local = _advisor(engine, zone="El Poblado", chat_id="chat-local")
    bot, event = AsyncMock(), _lead(engine, zone="El Poblado")

    advisor = asyncio.run(notifications.notify_advisor(engine, event, SCORE, bot, FakeAgenda(engine, error=error)))

    assert advisor.id == local
    assert bot.send_message.await_args.kwargs["chat_id"] == "chat-local"
    assert _advisor_of(engine, event["lead_id"]) == local


def test_the_handoff_file_goes_to_the_advisor_the_agenda_chose(engine, clean):
    chosen = _advisor(engine, zone="Usaquen", chat_id="chat-agenda")
    bot = AsyncMock()

    advisor = asyncio.run(notifications.notify_handoff(engine, _lead(engine), SCORE, "HUMAN_HANDOFF",
                                                        "solicitud_humano", bot, FakeAgenda(engine, chosen)))

    assert advisor.id == chosen
    assert {c.kwargs["chat_id"] for c in bot.send_message.await_args_list} == {"chat-agenda"}


def test_the_handoff_notice_has_the_reason_but_not_the_transcript(engine, clean):
    offline = _advisor(engine, zone="Usaquen")
    bot, event = AsyncMock(), _lead(engine)

    advisor = asyncio.run(notifications.notify_handoff(engine, event, SCORE, "HUMAN_HANDOFF", "solicitud_humano",
                                                        bot, FakeAgenda(engine, offline)))

    assert advisor.id == offline
    bot.send_message.assert_not_awaited()
    [(_, kind, body)] = _notices(engine, event["lead_id"])
    assert kind == "traspaso" and "el lead pidió hablar con una persona" in body
    assert "Transcripción" not in body


def test_telegram_turns_use_the_process_agenda(engine, clean, monkeypatch):
    """De punta a punta por el canal: calificar y pedir un humano consultan la agenda del proceso."""
    chosen = _advisor(engine, zone="Usaquen", chat_id="chat-agenda")
    agenda = FakeAgenda(engine, chosen)
    monkeypatch.setattr(scheduling_client, "get_scheduling", lambda: agenda)
    bot, lead = AsyncMock(), f"tg-{uuid.uuid4().hex[:8]}"

    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead, text=CALIFICA, bot=bot))
    asyncio.run(reply_to(engine, StubCatalog(), telegram_user_id=lead, text=PIDE_HUMANO, bot=bot))

    assert len(agenda.calls) == 2
    assert {c.kwargs["chat_id"] for c in bot.send_message.await_args_list} == {"chat-agenda"}
    with session_scope(engine) as session:
        row = session.execute(select(Lead).where(Lead.telegram_user_id == lead)).scalar_one()
    assert row.advisor_id == chosen


# --- cliente HTTP ---------------------------------------------------------------------------------

def _client(handler) -> SchedulingClient:
    return SchedulingClient("http://catalog:8000/", transport=httpx.MockTransport(handler))


def test_client_posts_to_the_agenda_and_returns_the_assignment():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"advisor_id": "adv_001"})

    result = asyncio.run(_client(handler).assign_advisor("lead-1", registered_only=True))

    assert result == {"advisor_id": "adv_001"}
    assert seen["url"] == "http://catalog:8000/scheduling/assign_advisor"
    assert seen["body"] == {"lead_id": "lead-1", "language": "es", "registered_only": True}


@pytest.mark.parametrize("response,code", [
    (httpx.Response(404, json={"error_code": "NOT_FOUND", "message": "no hay"}), "NOT_FOUND"),
    (httpx.Response(503, json={"error_code": "UPSTREAM_UNAVAILABLE", "message": "caído"}), "UPSTREAM_UNAVAILABLE"),
    (httpx.Response(500, text="Internal Server Error"), "UPSTREAM_UNAVAILABLE"),
])
def test_client_turns_error_answers_into_contract_codes(response, code):
    with pytest.raises(SchedulingError) as exc:
        asyncio.run(_client(lambda request: response).assign_advisor("lead-1"))
    assert exc.value.code == code


def test_client_without_network_is_upstream_unavailable():
    def handler(request):
        raise httpx.ConnectError("sin red", request=request)

    with pytest.raises(SchedulingError) as exc:
        asyncio.run(_client(handler).assign_advisor("lead-1"))
    assert exc.value.code == "UPSTREAM_UNAVAILABLE"


def test_client_uses_the_catalog_url_of_the_environment(monkeypatch):
    monkeypatch.setenv("CATALOG_SERVICE_URL", "http://catalog:8000/")
    assert SchedulingClient()._url == "http://catalog:8000/scheduling"
