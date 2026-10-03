"""Tests de C-3 contra un Postgres real: migraciones, constraints y persistencia de la FSM.

Cada corrida crea una base temporal, aplica `alembic upgrade head` y la borra al terminar. Sin
Postgres alcanzable los tests se omiten; en CI (`REQUIRE_DB=1`) fallan en lugar de omitirse.
Localmente: `docker compose up -d postgres`.
"""
import json
import uuid
from datetime import UTC, date, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from db import models
from db.models import (
    Advisor,
    AuditLog,
    Conversation,
    FsmContext,
    FunnelEvent,
    Lead,
    LeadScore,
    Message,
    Property,
    Visit,
)
from db.repo_root import repo_root
from db.testing import alembic_config, temporary_database

ROOT = repo_root()
CONTRACT = json.loads((ROOT / "contracts" / "fsm_state.schema.json").read_text(encoding="utf-8"))
FSM_VALIDATOR = Draft202012Validator(CONTRACT, format_checker=FormatChecker())


@pytest.fixture(scope="module")
def database_urls():
    with temporary_database(migrate=False) as url:
        yield url


@pytest.fixture(scope="module")
def engine(database_urls):
    command.upgrade(alembic_config(database_urls), "head")
    engine = create_engine(database_urls)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    """Sesión dentro de una transacción que siempre se revierte: los tests no se contaminan."""
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, expire_on_commit=False) as session:
            yield session
        transaction.rollback()


def new_lead(session, **overrides) -> Lead:
    lead = Lead(telegram_user_id=f"tg_{uuid.uuid4().hex}", **overrides)
    session.add(lead)
    session.flush()
    return lead


def new_conversation(session, lead=None, **overrides) -> Conversation:
    conversation = Conversation(lead_id=(lead or new_lead(session)).id, **overrides)
    session.add(conversation)
    session.flush()
    return conversation


# --- migraciones -----------------------------------------------------------------------------

def test_upgrade_creates_every_table_and_pgvector(engine):
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == set(models.Base.metadata.tables) | {"alembic_version"}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar()


def test_migrations_and_models_do_not_drift(engine):
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True,
                                                               "compare_server_default": True})
        assert compare_metadata(context, models.Base.metadata) == []


def test_downgrade_then_upgrade_round_trips(database_urls):
    config = alembic_config(database_urls)
    command.downgrade(config, "base")
    engine = create_engine(database_urls)
    assert inspect(engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    assert set(inspect(engine).get_table_names()) == set(models.Base.metadata.tables) | {"alembic_version"}
    engine.dispose()


@pytest.mark.parametrize("old", ["solo", "pareja", "familia"])
def test_0006_rewrites_the_old_decision_maker_values(old):
    """#119 (DT-40): con quién decidía el lead no queda guardado ni en la señal ni en las razones.

    SQL plano y no los modelos: los modelos describen `head`, y la base está en 0005."""
    signal = json.dumps({"budget_hint": None, "decision_maker": old, "urgency": "no_declarada",
                         "matching_properties": 0, "meets_requirements": None})
    with temporary_database(migrate=False) as url:
        config = alembic_config(url)
        command.upgrade(config, "0005")
        engine = create_engine(url)
        with engine.begin() as connection:
            lead_id = connection.execute(text(
                "INSERT INTO leads (telegram_user_id) VALUES ('tg_0006') RETURNING id")).scalar_one()
            conversation_id = connection.execute(text(
                "INSERT INTO conversations (lead_id) VALUES (:lead) RETURNING id"), {"lead": lead_id}).scalar_one()
            connection.execute(text(
                "INSERT INTO fsm_context (conversation_id, score_signal) VALUES (:conversation, CAST(:signal AS jsonb))"),
                {"conversation": conversation_id, "signal": signal})
            connection.execute(text(
                "INSERT INTO lead_scores (lead_id, need_fit, budget, authority, timing, eligibility, total, band,"
                " reasons) VALUES (:lead, 0, 0, 20, 0, 0, 20, 'COLD', :reasons)"),
                {"lead": lead_id, "reasons": ["Need fit", f"Authority: decision_maker={old}", "Timing"]})
        command.upgrade(config, "0006")
        with engine.connect() as connection:
            stored = connection.execute(text(
                "SELECT score_signal ->> 'decision_maker' FROM fsm_context WHERE conversation_id = :c"),
                {"c": conversation_id}).scalar_one()
            reasons = connection.execute(text("SELECT reasons FROM lead_scores WHERE lead_id = :l"),
                                         {"l": lead_id}).scalar_one()
        assert stored == "declarado"
        assert reasons == ["Need fit", "Authority: decision_maker=declarado", "Timing"]
        engine.dispose()


# --- estado de la FSM (lo que A-1 necesita) ----------------------------------------------------

def test_new_conversation_starts_in_greeting(session):
    conversation = new_conversation(session)
    session.refresh(conversation)
    assert conversation.fsm_state == "GREETING"


@pytest.mark.parametrize("state", models.FSM_STATES)
def test_every_fsm_state_can_be_persisted(session, state):
    conversation = new_conversation(session, fsm_state=state)
    session.refresh(conversation)
    assert conversation.fsm_state == state


def test_unknown_fsm_state_is_rejected(session):
    lead = new_lead(session)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Conversation(lead_id=lead.id, fsm_state="INVENTADO"))
        session.flush()


def test_persisted_state_reads_back_as_a_valid_fsm_state_contract(session):
    """Estado + contexto + score guardados en Postgres forman exactamente el contrato FsmState (CT-3)."""
    lead = new_lead(session)
    conversation = new_conversation(session, lead, fsm_state="RECOMMEND")
    session.add(FsmContext(
        conversation_id=conversation.id, lead_qualified=True,
        last_tool_results={"search_properties": {"properties": []}},
        score_signal={"budget_hint": "2M", "decision_maker": "declarado", "urgency": "3_meses",
                      "matching_properties": 2, "meets_requirements": True}))
    session.add(LeadScore(lead_id=lead.id, need_fit=10, budget=20, authority=12, timing=12,
                          eligibility=20, total=74, band="HOT", reasons=["a", "b", "c", "d", "e"]))
    session.flush()
    session.expire_all()

    context = session.get(FsmContext, conversation.id)
    score = session.get(LeadScore, lead.id)
    state = {
        "conversation_id": str(conversation.id),
        "lead_id": str(lead.id),
        "fsm_state": session.get(Conversation, conversation.id).fsm_state,
        "lead_qualified": context.lead_qualified,
        "last_tool_results": context.last_tool_results,
        "score_signal": context.score_signal,
        "score": {"lead_id": str(lead.id), "need_fit": score.need_fit, "budget": score.budget,
                  "authority": score.authority, "timing": score.timing,
                  "eligibility": score.eligibility, "total": score.total, "band": score.band,
                  "reasons": score.reasons},
    }
    FSM_VALIDATOR.validate(state)


def test_fsm_context_defaults_match_a_fresh_conversation(session):
    conversation = new_conversation(session)
    session.add(FsmContext(conversation_id=conversation.id))
    session.flush()
    session.expire_all()
    context = session.get(FsmContext, conversation.id)
    assert context.lead_qualified is False
    assert context.last_tool_results == {}
    FSM_VALIDATOR.validate({
        "conversation_id": str(conversation.id), "lead_id": str(conversation.lead_id),
        "fsm_state": "GREETING", "lead_qualified": context.lead_qualified,
        "last_tool_results": context.last_tool_results, "score_signal": context.score_signal,
        "score": None,
    })


def test_only_one_fsm_context_per_conversation(session):
    conversation = new_conversation(session)
    session.add(FsmContext(conversation_id=conversation.id))
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(FsmContext(conversation_id=conversation.id))
        session.flush()


# --- reglas de integridad ----------------------------------------------------------------------

@pytest.mark.parametrize("band,total,ok", [
    ("COLD", 40, True), ("COLD", 41, False), ("WARM", 41, True), ("WARM", 70, True),
    ("WARM", 71, False), ("HOT", 71, True), ("HOT", 70, False), ("HOT", 101, False),
])
def test_score_band_must_match_total(session, band, total, ok):
    lead = new_lead(session)
    score = LeadScore(lead_id=lead.id, need_fit=0, budget=0, authority=0, timing=0, eligibility=0,
                      total=total, band=band, reasons=["r"])
    if ok:
        session.add(score)
        session.flush()
    else:
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(score)
            session.flush()


def test_score_dimension_above_20_is_rejected(session):
    lead = new_lead(session)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(LeadScore(lead_id=lead.id, need_fit=21, budget=0, authority=0, timing=0,
                              eligibility=0, total=21, band="COLD", reasons=["r"]))
        session.flush()


def test_telegram_identity_is_unique(session):
    lead = new_lead(session)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Lead(telegram_user_id=lead.telegram_user_id))
        session.flush()


def test_lead_defaults_and_invalid_consent(session):
    lead = new_lead(session)
    session.refresh(lead)
    assert (lead.status, lead.consent_status) == ("nuevo", "pending")
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Lead(telegram_user_id="otro", consent_status="tal_vez"))
        session.flush()


def test_visit_idempotency_key_prevents_duplicates(session):
    lead = new_lead(session)
    session.add_all([
        Property(id="prop_x", title="t", price=1, type="venta", zone="z", m2=1, bedrooms=1,
                 bathrooms=1, address_approx="a", address_exact="b"),
        Advisor(id="adv_x", name="n", zone="z", access_token="tok"),
    ])
    session.flush()
    when = datetime(2026, 10, 1, 12, tzinfo=UTC)
    session.add(Visit(lead_id=lead.id, property_id="prop_x", advisor_id="adv_x", scheduled_at=when,
                      idempotency_key="evt-1"))
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Visit(lead_id=lead.id, property_id="prop_x", advisor_id="adv_x", scheduled_at=when,
                          idempotency_key="evt-1"))
        session.flush()


def test_property_status_and_type_are_constrained(session):
    for field, value in (("status", "vendida"), ("type", "traspaso")):
        kwargs = dict(id=f"p_{field}", title="t", price=1, type="venta", zone="z", m2=1, bedrooms=1,
                      bathrooms=1, address_approx="a", address_exact="b") | {field: value}
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(Property(**kwargs))
            session.flush()


def test_funnel_events_are_append_only(session):
    lead = new_lead(session)
    event = FunnelEvent(lead_id=lead.id, event_type="lead_created")
    session.add(event)
    session.flush()
    with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
        session.execute(text("UPDATE funnel_events SET event_type = 'x'"))
    with pytest.raises(DBAPIError, match="append-only"), session.begin_nested():
        session.execute(text("DELETE FROM funnel_events"))


def test_lead_with_funnel_events_cannot_be_deleted(session):
    lead = new_lead(session)
    session.add(FunnelEvent(lead_id=lead.id, event_type="lead_created"))
    session.flush()
    with pytest.raises(DBAPIError), session.begin_nested():
        session.delete(lead)
        session.flush()


def test_deleting_a_lead_removes_its_conversation_data(session):
    lead = new_lead(session)
    conversation = new_conversation(session, lead)
    session.add_all([FsmContext(conversation_id=conversation.id),
                     Message(conversation_id=conversation.id, role="user", content="hola")])
    session.flush()
    session.delete(lead)
    session.flush()
    for table in ("conversations", "fsm_context", "messages"):
        assert session.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_message_role_is_constrained_and_tool_calls_are_json(session):
    conversation = new_conversation(session)
    session.add(Message(conversation_id=conversation.id, role="assistant", content="ok",
                        tool_calls=[{"name": "search_properties", "arguments": {}}]))
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(Message(conversation_id=conversation.id, role="robot", content="x"))
        session.flush()


def test_audit_log_and_promotions_basics(session):
    session.add(AuditLog(actor="guardrail", action="jailbreak_attempt", payload={"turn": 3}))
    session.add(Property(id="prop_p", title="t", price=1, type="renta", zone="z", m2=1, bedrooms=0,
                         bathrooms=1, address_approx="a", address_exact="b"))
    session.flush()
    session.execute(text("INSERT INTO promotions (id, property_id, title, description, valid_until) "
                         "VALUES ('promo_x', 'prop_p', 't', 'd', :d)"), {"d": date(2099, 1, 1)})
    assert session.execute(text("SELECT count(*) FROM promotions")).scalar() == 1


def test_faq_embedding_dimension_is_enforced(session):
    from db.models import EMBEDDING_DIM, FaqEntry

    session.add(FaqEntry(question="q", answer="a", embedding=[0.1] * EMBEDDING_DIM))
    session.flush()
    with pytest.raises(DBAPIError), session.begin_nested():
        session.add(FaqEntry(question="q2", answer="a2", embedding=[0.1] * (EMBEDDING_DIM - 1)))
        session.flush()
