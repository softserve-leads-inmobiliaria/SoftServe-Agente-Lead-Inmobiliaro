"""Tests de A-1: el estado vive en Postgres (necesitan Postgres; ver db/testing.py)."""
import json
import threading
import uuid

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.state_store import ConversationMismatch, StateStore
from db.models import LeadScore
from db.repo_root import repo_root
from db.session import session_scope

CONTRACT = json.loads((repo_root() / "contracts" / "fsm_state.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(CONTRACT, format_checker=FormatChecker())


def ids():
    return uuid.uuid4(), uuid.uuid4()


def test_first_message_creates_the_lead_conversation_and_context(engine):
    conversation_id, lead_id = ids()
    with session_scope(engine) as session:
        state = StateStore(session).lock(conversation_id, lead_id)
    assert state.fsm_state == "GREETING"
    assert state.lead_qualified is False and state.score is None
    with engine.connect() as connection:
        for table, column, value in (("leads", "id", lead_id), ("conversations", "id", conversation_id),
                                     ("fsm_context", "conversation_id", conversation_id)):
            assert connection.execute(text(f"SELECT count(*) FROM {table} WHERE {column} = :v"),
                                      {"v": value}).scalar() == 1


def test_state_is_read_back_from_postgres_by_a_brand_new_engine(engine, database_url):
    """Sin memoria compartida: otro engine (como tras reiniciar el servicio) ve el mismo estado."""
    conversation_id, lead_id = ids()
    with session_scope(engine) as session:
        store = StateStore(session)
        store.lock(conversation_id, lead_id)
        store.save_state(conversation_id, "QUALIFY")
    restarted = create_engine(database_url)
    try:
        with session_scope(restarted) as session:
            assert StateStore(session).lock(conversation_id, lead_id).fsm_state == "QUALIFY"
    finally:
        restarted.dispose()


def test_locking_twice_does_not_duplicate_rows_or_reset_the_state(engine):
    conversation_id, lead_id = ids()
    for state in ("RECOMMEND", None):
        with session_scope(engine) as session:
            store = StateStore(session)
            store.lock(conversation_id, lead_id)
            if state:
                store.save_state(conversation_id, state)
    with session_scope(engine) as session:
        assert StateStore(session).lock(conversation_id, lead_id).fsm_state == "RECOMMEND"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM conversations WHERE id = :c"),
                                  {"c": conversation_id}).scalar() == 1


def test_a_conversation_cannot_be_used_by_another_lead(engine):
    conversation_id, lead_id = ids()
    with session_scope(engine) as session:
        StateStore(session).lock(conversation_id, lead_id)
    with pytest.raises(ConversationMismatch), session_scope(engine) as session:
        StateStore(session).lock(conversation_id, uuid.uuid4())


def test_persisted_state_is_a_valid_fsm_state_contract(engine):
    conversation_id, lead_id = ids()
    with session_scope(engine) as session:
        store = StateStore(session)
        store.lock(conversation_id, lead_id)
        store.save_state(conversation_id, "RECOMMEND")
        session.add(LeadScore(lead_id=lead_id, need_fit=10, budget=20, authority=12, timing=12,
                              eligibility=20, total=74, band="HOT", reasons=["a", "b", "c", "d", "e"]))
    with session_scope(engine) as session:
        state = StateStore(session).lock(conversation_id, lead_id)
    contract = state.to_contract()
    VALIDATOR.validate(contract)
    assert contract["fsm_state"] == "RECOMMEND" and contract["score"]["band"] == "HOT"


def test_touch_updates_last_message_at(engine):
    conversation_id, lead_id = ids()
    with session_scope(engine) as session:
        StateStore(session).lock(conversation_id, lead_id)
    with engine.connect() as connection:
        before = connection.execute(text("SELECT last_message_at FROM conversations WHERE id = :c"),
                                    {"c": conversation_id}).scalar()
    with session_scope(engine) as session:
        StateStore(session).touch(conversation_id)
    with engine.connect() as connection:
        after = connection.execute(text("SELECT last_message_at FROM conversations WHERE id = :c"),
                                   {"c": conversation_id}).scalar()
    assert after > before


def test_concurrent_turns_of_the_same_conversation_are_serialized(engine):
    """Mientras un turno tiene la fila bloqueada, otro turno de esa conversación tiene que esperar."""
    conversation_id, lead_id = ids()
    with session_scope(engine) as session:
        StateStore(session).lock(conversation_id, lead_id)

    holder_ready, release = threading.Event(), threading.Event()

    def hold_lock():
        with session_scope(engine) as session:
            StateStore(session).lock(conversation_id, lead_id)
            holder_ready.set()
            release.wait(timeout=10)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert holder_ready.wait(timeout=10)
    try:
        with Session(engine) as other:
            other.execute(text("SET lock_timeout = '300ms'"))
            with pytest.raises(OperationalError, match="lock"):
                StateStore(other).lock(conversation_id, lead_id)
    finally:
        release.set()
        thread.join(timeout=10)
