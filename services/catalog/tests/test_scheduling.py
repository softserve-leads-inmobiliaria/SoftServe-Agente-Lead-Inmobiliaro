"""Tests de C-2: asignar asesor, consultar horarios y reservar visitas, sobre Postgres real."""
import json
import threading
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.scheduling import SchedulingError, SchedulingService
from app.scheduling_api import create_scheduling_router
from db.models import Advisor, Lead, Visit
from db.repo_root import repo_root
from db.settings import database_url

CONTRACTS = repo_root() / "contracts" / "scheduling"
# Lunes 14 de septiembre de 2026, 9:00 en Guadalajara (UTC-6): el bloque de las 9:00 ya empezó.
NOW = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
TUESDAY_10 = "2026-09-15T10:00:00-06:00"


def output_validator(name):
    schema = json.loads((CONTRACTS / f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema["outputSchema"], format_checker=FormatChecker())


@pytest.fixture
def factory():
    """Sesiones contra la base de pruebas, sin asesores, leads ni visitas de otros tests."""
    engine = create_engine(database_url())
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE visits, leads, advisors CASCADE"))
    yield sessionmaker(autoflush=False, bind=engine)
    engine.dispose()


@pytest.fixture
def service(factory):
    return SchedulingService(db_session_factory=factory, now=lambda: NOW)


def add_advisor(factory, advisor_id, zone, language="es", chat_id=None):
    with factory() as db:
        db.add(Advisor(id=advisor_id, name=f"Asesor {advisor_id}", zone=zone, language=language,
                       access_token=uuid.uuid4().hex, telegram_chat_id=chat_id))
        db.commit()


def add_lead(factory, zone=None, advisor_id=None):
    with factory() as db:
        lead = Lead(telegram_user_id=uuid.uuid4().hex, zone=zone, advisor_id=advisor_id)
        db.add(lead)
        db.commit()
        return str(lead.id)


def visits(factory):
    with factory() as db:
        return db.execute(select(func.count()).select_from(Visit)).scalar_one()


def error_code(call):
    with pytest.raises(SchedulingError) as exc:
        call()
    return exc.value.error_code


# --- assign_advisor -------------------------------------------------------------------------------

def test_assigns_an_advisor_of_the_lead_zone_and_writes_it(factory, service):
    add_advisor(factory, "adv_a", "Zapopan")
    add_advisor(factory, "adv_b", "Providencia")
    lead_id = add_lead(factory, zone="Providencia")

    result = service.assign_advisor(lead_id)

    output_validator("assign_advisor").validate(result)
    assert (result["advisor_id"], result["match"]) == ("adv_b", "zone_and_language")
    with factory() as db:
        assert db.get(Lead, uuid.UUID(lead_id)).advisor_id == "adv_b"


def test_assignment_is_idempotent(factory, service):
    add_advisor(factory, "adv_a", "Providencia")
    add_advisor(factory, "adv_b", "Providencia")
    lead_id = add_lead(factory, zone="Providencia")

    first = service.assign_advisor(lead_id)
    second = service.assign_advisor(lead_id)

    assert second["advisor_id"] == first["advisor_id"]
    assert second["match"] == "existing"


def test_an_advisor_already_assigned_elsewhere_is_kept(factory, service):
    # B-7 ya pudo asignar el lead al notificar: la agenda no lo reasigna.
    add_advisor(factory, "adv_a", "Providencia")
    add_advisor(factory, "adv_b", "Zapopan")
    lead_id = add_lead(factory, zone="Providencia", advisor_id="adv_b")

    assert service.assign_advisor(lead_id)["advisor_id"] == "adv_b"


def test_leads_of_one_zone_are_shared_in_turns(factory, service):
    add_advisor(factory, "adv_a", "Providencia")
    add_advisor(factory, "adv_b", "Providencia")
    add_advisor(factory, "adv_c", "Zapopan")

    chosen = [service.assign_advisor(add_lead(factory, zone="Providencia"))["advisor_id"] for _ in range(4)]

    assert chosen == ["adv_a", "adv_b", "adv_a", "adv_b"]


def test_zone_given_by_the_caller_wins_and_ignores_case(factory, service):
    add_advisor(factory, "adv_a", "Providencia")
    add_advisor(factory, "adv_b", "Zapopan")
    lead_id = add_lead(factory, zone="Providencia")

    assert service.assign_advisor(lead_id, zone="zapopan")["advisor_id"] == "adv_b"


@pytest.mark.parametrize("zone,language,expected,match", [
    ("Zapopan", "en", "adv_en", "zone_and_language"),
    ("Zapopan", "fr", "adv_en", "zone"),         # empate de carga: gana el id menor
    ("Tlaquepaque", "en", "adv_en", "language"),
    ("Tlaquepaque", "fr", "adv_en", "any"),
    (None, "es", "adv_es", "language"),
])
def test_fallback_order(factory, service, zone, language, expected, match):
    add_advisor(factory, "adv_es", "Zapopan", language="es")
    add_advisor(factory, "adv_en", "Zapopan", language="en")
    add_advisor(factory, "adv_x", "Andares", language="en")
    # adv_x tiene carga: sin él, el grupo "language" en inglés empataría con adv_en.
    add_lead(factory, advisor_id="adv_x")

    result = service.assign_advisor(add_lead(factory, zone=zone), language=language)

    assert (result["advisor_id"], result["match"]) == (expected, match)


def test_assign_errors(factory, service):
    assert error_code(lambda: service.assign_advisor(str(uuid.uuid4()))) == "NOT_FOUND"
    assert error_code(lambda: service.assign_advisor(add_lead(factory, zone="Zapopan"))) == "NOT_FOUND"


def test_registered_only_skips_advisors_without_telegram(factory, service):
    add_advisor(factory, "adv_a", "Providencia")                      # nunca abrió el bot
    add_advisor(factory, "adv_b", "Zapopan", chat_id="chat-b")
    lead_id = add_lead(factory, zone="Providencia")

    result = service.assign_advisor(lead_id, registered_only=True)

    assert (result["advisor_id"], result["match"]) == ("adv_b", "language")


def test_registered_only_without_any_registered_advisor_assigns_nobody(factory, service):
    add_advisor(factory, "adv_a", "Providencia")
    lead_id = add_lead(factory, zone="Providencia")

    assert error_code(lambda: service.assign_advisor(lead_id, registered_only=True)) == "NOT_FOUND"
    with factory() as db:
        assert db.get(Lead, uuid.UUID(lead_id)).advisor_id is None


def test_registered_only_keeps_an_advisor_already_assigned(factory, service):
    add_advisor(factory, "adv_a", "Providencia")
    add_advisor(factory, "adv_b", "Providencia", chat_id="chat-b")
    lead_id = add_lead(factory, zone="Providencia", advisor_id="adv_a")

    assert service.assign_advisor(lead_id, registered_only=True)["advisor_id"] == "adv_a"


# --- get_slots ------------------------------------------------------------------------------------

def test_slots_are_office_hours_from_now_on(factory, service):
    add_advisor(factory, "adv_a", "Providencia")

    result = service.get_slots("adv_a", "2026-09-14", days=2)

    output_validator("get_slots").validate(result)
    # Lunes: de 10:00 a 17:00 (el de las 9:00 ya empezó). Martes: de 9:00 a 17:00.
    assert len(result["slots"]) == 8 + 9
    assert result["slots"][0] == "2026-09-14T10:00:00-06:00"
    assert result["slots"][-1] == "2026-09-15T17:00:00-06:00"


def test_sunday_has_no_slots(factory, service):
    add_advisor(factory, "adv_a", "Providencia")

    slots = service.get_slots("adv_a", "2026-09-19", days=2)["slots"]  # sábado y domingo

    assert slots and all(slot.startswith("2026-09-19") for slot in slots)


def test_a_past_start_date_means_today(factory, service):
    add_advisor(factory, "adv_a", "Providencia")

    assert service.get_slots("adv_a", "2020-01-01", days=1) == service.get_slots("adv_a", "2026-09-14", days=1)


def test_get_slots_errors(factory, service):
    add_advisor(factory, "adv_a", "Providencia")
    assert error_code(lambda: service.get_slots("adv_nadie", "2026-09-15")) == "NOT_FOUND"
    assert error_code(lambda: service.get_slots("adv_a", "15/09/2026")) == "INVALID_INPUT"
    assert error_code(lambda: service.get_slots("adv_a", "2026-09-15", days=15)) == "INVALID_INPUT"


# --- book -----------------------------------------------------------------------------------------

@pytest.fixture
def booking(factory):
    add_advisor(factory, "adv_a", "Providencia")
    lead_id = add_lead(factory, zone="Providencia")
    return {"idempotency_key": "req-1", "lead_id": lead_id, "property_id": "prop_00231",
            "advisor_id": "adv_a", "scheduled_at": TUESDAY_10}


def test_book_writes_the_visit_and_takes_the_slot(factory, service, booking):
    result = service.book(**booking)

    output_validator("book").validate(result)
    assert result["created"] is True
    assert result["scheduled_at"] == TUESDAY_10
    assert visits(factory) == 1
    assert TUESDAY_10 not in service.get_slots("adv_a", "2026-09-15", days=1)["slots"]


def test_a_retry_returns_the_same_visit_without_duplicating_it(factory, service, booking):
    first = service.book(**booking)
    retry = service.book(**booking)

    assert retry == first | {"created": False}
    assert visits(factory) == 1


def test_a_retry_after_the_visit_time_still_answers(factory, service, booking):
    first = service.book(**booking)
    later = SchedulingService(db_session_factory=factory, now=lambda: datetime(2026, 9, 20, tzinfo=timezone.utc))

    assert later.book(**booking)["visit_id"] == first["visit_id"]


def test_the_same_instant_in_another_offset_is_the_same_request(factory, service, booking):
    first = service.book(**booking)

    retry = service.book(**booking | {"scheduled_at": "2026-09-15T16:00:00+00:00"})

    assert retry["visit_id"] == first["visit_id"]


def test_reusing_a_key_for_another_visit_is_rejected(factory, service, booking):
    service.book(**booking)

    assert error_code(lambda: service.book(**booking | {"scheduled_at": "2026-09-15T11:00:00-06:00"})) \
        == "INVALID_INPUT"


def test_a_taken_slot_cannot_be_booked_again(factory, service, booking):
    service.book(**booking)
    other_lead = add_lead(factory, zone="Providencia")

    assert error_code(lambda: service.book(**booking | {"idempotency_key": "req-2", "lead_id": other_lead})) \
        == "SLOT_TAKEN"
    assert visits(factory) == 1


@pytest.mark.parametrize("scheduled_at", [
    "2026-09-14T09:00:00-06:00",  # ya empezó
    "2026-09-13T10:00:00-06:00",  # ayer
    "2026-09-20T10:00:00-06:00",  # domingo
    "2026-09-15T08:00:00-06:00",  # antes de abrir
    "2026-09-15T18:00:00-06:00",  # terminaría después de cerrar
    "2026-09-15T10:30:00-06:00",  # no es el inicio de un bloque
    "2026-09-15T10:00:00",        # sin desfase horario
])
def test_only_future_office_hour_slots_can_be_booked(factory, service, booking, scheduled_at):
    assert error_code(lambda: service.book(**booking | {"scheduled_at": scheduled_at})) == "INVALID_INPUT"
    assert visits(factory) == 0


def test_a_property_that_is_not_available_cannot_be_visited(service, booking):
    assert error_code(lambda: service.book(**booking | {"property_id": "prop_00234"})) == "INVALID_INPUT"


@pytest.mark.parametrize("field,value", [
    ("lead_id", "00000000-0000-0000-0000-000000000000"),
    ("property_id", "prop_nadie"),
    ("advisor_id", "adv_nadie"),
])
def test_unknown_references_are_not_found(service, booking, field, value):
    assert error_code(lambda: service.book(**booking | {field: value})) == "NOT_FOUND"


def _in_parallel(calls):
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(i, call):
        barrier.wait()
        try:
            results[i] = call()
        except SchedulingError as error:
            results[i] = error.error_code

    threads = [threading.Thread(target=run, args=(i, call)) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_simultaneous_bookings_of_one_slot_leave_a_single_visit(factory, service, booking):
    leads = [add_lead(factory, zone="Providencia") for _ in range(6)]

    results = _in_parallel([lambda i=i, lead=lead: service.book(**booking | {"idempotency_key": f"req-{i}",
                                                                            "lead_id": lead})
                            for i, lead in enumerate(leads)])

    assert sum(isinstance(r, dict) for r in results) == 1
    assert results.count("SLOT_TAKEN") == 5
    assert visits(factory) == 1


def test_simultaneous_retries_all_get_the_same_visit(factory, service, booking):
    results = _in_parallel([lambda: service.book(**booking) for _ in range(6)])

    assert len({r["visit_id"] for r in results}) == 1
    assert sum(r["created"] for r in results) == 1
    assert visits(factory) == 1


def test_simultaneous_assignments_of_one_lead_agree(factory, service):
    for advisor_id in ("adv_a", "adv_b", "adv_c"):
        add_advisor(factory, advisor_id, "Providencia")
    lead_id = add_lead(factory, zone="Providencia")

    results = _in_parallel([lambda: service.assign_advisor(lead_id) for _ in range(6)])

    assert len({r["advisor_id"] for r in results}) == 1
    with factory() as db:
        assert db.get(Lead, uuid.UUID(lead_id)).advisor_id == results[0]["advisor_id"]


# --- HTTP -----------------------------------------------------------------------------------------

@pytest.fixture
def client(service):
    app = FastAPI()
    app.include_router(create_scheduling_router(service))
    return TestClient(app)


def error_validator():
    schema = json.loads((CONTRACTS / "scheduling_error.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def test_http_book_and_retry(client, booking):
    first = client.post("/scheduling/book", json=booking)
    retry = client.post("/scheduling/book", json=booking)

    assert first.status_code == retry.status_code == 200
    output_validator("book").validate(first.json())
    assert retry.json()["visit_id"] == first.json()["visit_id"]


@pytest.mark.parametrize("body,status,code", [
    ({"advisor_id": "adv_a"}, 422, "INVALID_INPUT"),                          # falta date_from
    ({"advisor_id": "adv_a", "date_from": "2026-09-15", "extra": 1}, 422, "INVALID_INPUT"),
    ({"advisor_id": "adv_nadie", "date_from": "2026-09-15"}, 404, "NOT_FOUND"),
])
def test_http_errors_follow_the_contract(client, booking, body, status, code):
    response = client.post("/scheduling/get_slots", json=body)

    assert response.status_code == status
    error_validator().validate(response.json())
    assert response.json()["error_code"] == code


def test_http_slot_taken_is_409(client, factory, booking):
    client.post("/scheduling/book", json=booking)
    other = booking | {"idempotency_key": "req-2", "lead_id": add_lead(factory)}

    response = client.post("/scheduling/book", json=other)

    assert (response.status_code, response.json()["error_code"]) == (409, "SLOT_TAKEN")


def test_http_body_that_is_not_json_is_invalid_input(client):
    response = client.post("/scheduling/book", content=b"no es json")

    assert (response.status_code, response.json()["error_code"]) == (422, "INVALID_INPUT")


def test_http_database_down_is_upstream_unavailable():
    engine = create_engine("postgresql+psycopg://x:x@127.0.0.1:1/x", connect_args={"connect_timeout": 1})
    app = FastAPI()
    app.include_router(create_scheduling_router(SchedulingService(sessionmaker(bind=engine), now=lambda: NOW)))

    response = TestClient(app).post("/scheduling/get_slots", json={"advisor_id": "adv_a", "date_from": "2026-09-15"})

    assert response.status_code == 503
    assert response.json() == {"error_code": "UPSTREAM_UNAVAILABLE",
                               "message": "La agenda no está disponible por ahora."}


def test_catalog_app_serves_the_agenda_next_to_mcp(factory):
    # /scheduling tiene que resolverse antes que el servidor MCP montado en "/".
    from app.main import app

    response = TestClient(app).post("/scheduling/get_slots",
                                    json={"advisor_id": "adv_nadie", "date_from": "2026-09-15"})

    assert (response.status_code, response.json()["error_code"]) == (404, "NOT_FOUND")
