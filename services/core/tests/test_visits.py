"""Tests de B-8 (sin base de datos): ofrecer horarios, entender cuál elige el lead y reservarlo."""
import asyncio
from datetime import date

import pytest

from app.scheduling_client import SchedulingError
from app.visits import (
    OFFER_KEY,
    PREFERRED_AGAIN,
    PREFERRED_ASK,
    PREFERRED_THANKS,
    VISIT_KEY,
    choose_slot,
    follow_up,
    slot_label,
    start,
)

SLOTS = ["2026-10-06T10:00:00-05:00", "2026-10-06T11:00:00-05:00", "2026-10-07T16:00:00-05:00"]
PROP = {"id": "prop_00231", "title": "Depto 2 rec. en El Poblado", "zone": "El Poblado", "status": "disponible"}
TODAY = date(2026, 10, 5)


class FakeAgenda:
    """La agenda del catálogo: guarda lo que le piden y falla donde se le diga."""

    def __init__(self, slots=SLOTS, fail=None, book_fails=None):
        self.slots, self.fail, self.book_fails = list(slots), fail or {}, list(book_fails or [])
        self.calls = []

    def _maybe_fail(self, operation):
        if operation in self.fail:
            raise SchedulingError(self.fail[operation], "falla de prueba")

    async def assign_advisor(self, lead_id, zone=None, language="es", registered_only=False):
        self.calls.append(("assign_advisor", lead_id, registered_only))
        self._maybe_fail("assign_advisor")
        return {"advisor_id": "adv_poblado", "lead_id": lead_id}

    async def get_slots(self, advisor_id, date_from, days=7):
        self.calls.append(("get_slots", advisor_id, date_from, days))
        self._maybe_fail("get_slots")
        return {"advisor_id": advisor_id, "slots": list(self.slots)}

    async def book(self, idempotency_key, lead_id, property_id, advisor_id, scheduled_at):
        self.calls.append(("book", idempotency_key, lead_id, property_id, advisor_id, scheduled_at))
        if self.book_fails:
            raise SchedulingError(self.book_fails.pop(0), "falla de prueba")
        return {"visit_id": "6f1c2c1e-0000-4000-8000-000000000001", "lead_id": lead_id,
                "property_id": property_id, "advisor_id": advisor_id, "scheduled_at": scheduled_at,
                "created": True}


def run(coroutine):
    return asyncio.run(coroutine)


def offer_of(step):
    return step.tool_results[OFFER_KEY]


# --- elegir el horario ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("2", SLOTS[1]), ("La 2", SLOTS[1]), ("opción 3 por favor", SLOTS[2]),
    ("la primera", SLOTS[0]), ("La segunda", SLOTS[1]),
    ("a las 16:00", SLOTS[2]), ("10:00", SLOTS[0]), ("a las 4", None),
    ("el miércoles a las 16", SLOTS[2]),
])
def test_the_lead_can_choose_by_number_ordinal_or_hour(text, expected):
    assert choose_slot(text, SLOTS) == expected


@pytest.mark.parametrize("text", [
    "ok", "no sé", "4", "0", "la 1 o la 2", "el lunes", "a las 10 o a las 11", "15:00",
])
def test_an_answer_that_does_not_pick_exactly_one_slot_is_not_a_choice(text):
    assert choose_slot(text, SLOTS) is None


def test_slots_are_shown_in_local_time_with_the_weekday():
    assert slot_label("2026-10-06T15:00:00+00:00") == "mar 06/10 10:00"


# --- ofrecer ----------------------------------------------------------------------------------------------

def test_asking_for_a_visit_offers_the_first_slots_of_the_leads_advisor():
    agenda = FakeAgenda(slots=SLOTS + ["2026-10-08T09:00:00-05:00"])
    step = run(start(agenda, "lead-1", PROP, TODAY))
    assert agenda.calls[:2] == [("assign_advisor", "lead-1", False), ("get_slots", "adv_poblado", "2026-10-05", 7)]
    assert offer_of(step) == {"property": {"id": "prop_00231", "title": "Depto 2 rec. en El Poblado",
                                           "zone": "El Poblado"},
                              "advisor_id": "adv_poblado", "slots": SLOTS}
    assert "1) mar 06/10 10:00" in step.reply and "3) mié 07/10 16:00" in step.reply and "4)" not in step.reply
    assert not step.settled


@pytest.mark.parametrize("agenda", [
    FakeAgenda(fail={"assign_advisor": "UPSTREAM_UNAVAILABLE"}),
    FakeAgenda(fail={"assign_advisor": "NOT_FOUND"}),  # ningún asesor abrió el bot
    FakeAgenda(fail={"get_slots": "UPSTREAM_UNAVAILABLE"}),
    FakeAgenda(slots=[]),
], ids=["agenda caida", "sin asesor", "sin horarios por falla", "sin horarios libres"])
def test_without_slots_the_lead_is_asked_for_a_preferred_time(agenda):
    step = run(start(agenda, "lead-1", PROP, TODAY))
    assert step.reply == PREFERRED_ASK and offer_of(step)["preferred_time"] is True
    assert offer_of(step)["property"]["id"] == "prop_00231"


def test_without_a_known_property_there_is_nothing_to_book():
    agenda = FakeAgenda()
    step = run(start(agenda, "lead-1", None, TODAY))
    assert step.reply == PREFERRED_ASK and agenda.calls == []


# --- responder a la oferta --------------------------------------------------------------------------------

def _offer():
    return offer_of(run(start(FakeAgenda(), "lead-1", PROP, TODAY)))


def test_choosing_a_slot_books_it_with_an_idempotency_key_per_conversation_and_slot():
    agenda = FakeAgenda()
    step = run(follow_up(agenda, "conv-1", "lead-1", _offer(), "la 2", TODAY))
    assert agenda.calls == [("book", f"conv-1:{SLOTS[1]}", "lead-1", "prop_00231", "adv_poblado", SLOTS[1])]
    assert step.settled and step.booked
    assert "mar 06/10 11:00" in step.reply and "Depto 2 rec. en El Poblado" in step.reply
    assert step.tool_results[VISIT_KEY]["scheduled_at"] == SLOTS[1]


def test_a_retry_of_the_same_answer_uses_the_same_key():
    agenda = FakeAgenda()
    for _ in range(2):
        run(follow_up(agenda, "conv-1", "lead-1", _offer(), "2", TODAY))
    assert agenda.calls[0][1] == agenda.calls[1][1]


def test_an_answer_that_is_not_a_choice_offers_the_same_slots_again():
    agenda = FakeAgenda()
    offer = _offer()
    step = run(follow_up(agenda, "conv-1", "lead-1", offer, "mmm", TODAY))
    assert not step.settled and agenda.calls == [] and offer_of(step) == offer
    assert "No me quedó claro" in step.reply and "2) mar 06/10 11:00" in step.reply


def test_a_slot_taken_meanwhile_offers_fresh_slots():
    agenda = FakeAgenda(book_fails=["SLOT_TAKEN"], slots=SLOTS[1:])
    step = run(follow_up(agenda, "conv-1", "lead-1", _offer(), "1", TODAY))
    assert not step.settled and offer_of(step)["slots"] == SLOTS[1:]
    assert step.reply.startswith("Ese horario se acaba de ocupar")


@pytest.mark.parametrize("code", ["UPSTREAM_UNAVAILABLE", "INVALID_INPUT", "NOT_FOUND"])
def test_any_other_booking_failure_falls_back_to_the_preferred_time(code):
    step = run(follow_up(FakeAgenda(book_fails=[code]), "conv-1", "lead-1", _offer(), "1", TODAY))
    assert step.reply == PREFERRED_ASK and not step.settled


def test_the_preferred_time_is_handed_to_the_advisor():
    offer = {"property": {"id": "prop_00231", "title": "Depto"}, "preferred_time": True}
    step = run(follow_up(FakeAgenda(), "conv-1", "lead-1", offer, "Mañana en la tarde ", TODAY))
    assert step.reply == PREFERRED_THANKS and step.settled and not step.booked
    assert step.tool_results[VISIT_KEY] == {"property": offer["property"], "preferred_time": "Mañana en la tarde"}


@pytest.mark.parametrize("text", ["ok", "mmm", "no sé"])
def test_an_answer_without_a_day_or_time_is_not_a_preferred_time(text):
    offer = {"property": None, "preferred_time": True}
    step = run(follow_up(FakeAgenda(), "conv-1", "lead-1", offer, text, TODAY))
    assert step.reply == PREFERRED_AGAIN and not step.settled and offer_of(step) == offer
