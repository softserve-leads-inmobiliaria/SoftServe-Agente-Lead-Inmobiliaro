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
    answers,
    choose_slot,
    follow_up,
    slot_label,
    start,
    visit_when,
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

    async def assign_advisor(self, lead_id, zone=None, language="en", registered_only=False):
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
    # En inglés (#202): "pm" suma 12, como vienen los horarios de la agenda.
    ("the second one", SLOTS[1]), ("2nd", SLOTS[1]), ("number 3 please", SLOTS[2]), ("at 4 pm", SLOTS[2]),
    ("4:00 pm works", SLOTS[2]), ("10:00 am", SLOTS[0]), ("at 11", SLOTS[1]), ("at 4", None),
])
def test_the_lead_can_choose_by_number_ordinal_or_hour(text, expected):
    assert choose_slot(text, SLOTS) == expected


@pytest.mark.parametrize("text", [
    "ok", "no sé", "4", "0", "la 1 o la 2", "el lunes", "a las 10 o a las 11", "15:00",
    "not sure", "1 or 2", "on Monday", "at 10 or at 11",
])
def test_an_answer_that_does_not_pick_exactly_one_slot_is_not_a_choice(text):
    assert choose_slot(text, SLOTS) is None


def test_slots_are_shown_in_local_time_with_the_weekday():
    assert slot_label("2026-10-06T15:00:00+00:00") == "Tue Oct 6, 10:00"


# --- ofrecer ----------------------------------------------------------------------------------------------

def test_asking_for_a_visit_offers_the_first_slots_of_the_leads_advisor():
    agenda = FakeAgenda(slots=SLOTS + ["2026-10-08T09:00:00-05:00"])
    step = run(start(agenda, "lead-1", PROP, TODAY))
    assert agenda.calls[:2] == [("assign_advisor", "lead-1", False), ("get_slots", "adv_poblado", "2026-10-05", 7)]
    assert offer_of(step) == {"property": {"id": "prop_00231", "title": "Depto 2 rec. en El Poblado",
                                           "zone": "El Poblado"},
                              "advisor_id": "adv_poblado", "slots": SLOTS}
    assert "1) Tue Oct 6, 10:00" in step.reply and "3) Wed Oct 7, 16:00" in step.reply and "4)" not in step.reply
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
    assert "Tue Oct 6, 11:00" in step.reply and "Depto 2 rec. en El Poblado" in step.reply
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
    assert "I didn't quite get" in step.reply and "2) Tue Oct 6, 11:00" in step.reply


def test_a_slot_taken_meanwhile_offers_fresh_slots():
    agenda = FakeAgenda(book_fails=["SLOT_TAKEN"], slots=SLOTS[1:])
    step = run(follow_up(agenda, "conv-1", "lead-1", _offer(), "1", TODAY))
    assert not step.settled and offer_of(step)["slots"] == SLOTS[1:]
    assert step.reply.startswith("That time was just taken")


@pytest.mark.parametrize("code", ["UPSTREAM_UNAVAILABLE", "INVALID_INPUT", "NOT_FOUND"])
def test_any_other_booking_failure_falls_back_to_the_preferred_time(code):
    step = run(follow_up(FakeAgenda(book_fails=[code]), "conv-1", "lead-1", _offer(), "1", TODAY))
    assert step.reply == PREFERRED_ASK and not step.settled


def test_the_preferred_time_is_handed_to_the_advisor():
    offer = {"property": {"id": "prop_00231", "title": "Depto"}, "preferred_time": True}
    step = run(follow_up(FakeAgenda(), "conv-1", "lead-1", offer, "Mañana en la tarde ", TODAY))
    assert step.reply == PREFERRED_THANKS and step.settled and not step.booked
    assert step.tool_results[VISIT_KEY] == {"property": offer["property"], "preferred_time": "Mañana en la tarde"}


@pytest.mark.parametrize("text", ["Tomorrow afternoon", "Saturday morning", "next week, any day"])
def test_an_english_day_or_time_is_a_preferred_time(text):
    offer = {"property": None, "preferred_time": True}
    step = run(follow_up(FakeAgenda(), "conv-1", "lead-1", offer, text, TODAY))
    assert step.reply == PREFERRED_THANKS and step.settled


@pytest.mark.parametrize("text", ["ok", "mmm", "no sé", "not sure", "sounds good"])
def test_an_answer_without_a_day_or_time_is_not_a_preferred_time(text):
    offer = {"property": None, "preferred_time": True}
    step = run(follow_up(FakeAgenda(), "conv-1", "lead-1", offer, text, TODAY))
    assert step.reply == PREFERRED_AGAIN and not step.settled and offer_of(step) == offer


# --- cuándo prefiere la visita (`visit_when`, #247) -------------------------------------------------------

SATURDAY = "2026-10-10T10:00:00-05:00"
NEXT_TUESDAY_AFTERNOON = "2026-10-13T15:00:00-05:00"
WEEKEND, OTHER_DAY = {"days": [5, 6], "part": None, "other_day": False}, {"days": [], "part": None, "other_day": True}


def test_the_classifier_preference_is_validated():
    assert visit_when({"days": ["sat", "sun"], "part_of_day": None, "other_day": False}) == WEEKEND
    assert visit_when({"days": ["Tuesday"], "part_of_day": "afternoon"}) == {"days": [1], "part": "afternoon",
                                                                            "other_day": False}
    assert visit_when({"days": [], "part_of_day": None, "other_day": False}) is None
    assert visit_when({"days": ["someday"], "part_of_day": "brunch"}) is None
    assert visit_when(None) is None and visit_when("this weekend") is None


def test_this_weekend_offers_only_weekend_slots_looking_two_weeks_ahead():
    # Prueba de jurado (set N): "this weekend" ofrecía horarios del jueves.
    agenda = FakeAgenda(slots=SLOTS + [SATURDAY])
    step = run(start(agenda, "lead-1", PROP, TODAY, WEEKEND))
    assert agenda.calls[1] == ("get_slots", "adv_poblado", "2026-10-05", 14)
    assert offer_of(step)["slots"] == [SATURDAY]
    assert step.reply.startswith("Here are the available times on the weekend") and "Sat Oct 10, 10:00" in step.reply


def test_without_a_slot_that_fits_it_says_so_and_offers_the_closest():
    step = run(start(FakeAgenda(), "lead-1", PROP, TODAY, WEEKEND))
    assert step.reply.startswith("I don't have available times on the weekend. These are the closest ones")
    assert offer_of(step)["slots"] == SLOTS


def test_another_day_offers_slots_on_days_not_offered_yet():
    # Prueba de jurado M5 t7: "another day" contestaba "I didn't quite get which time you prefer".
    agenda = FakeAgenda(slots=SLOTS + ["2026-10-08T09:00:00-05:00"])
    step = run(follow_up(agenda, "conv-1", "lead-1", _offer(), "Can we do another day?", TODAY, OTHER_DAY))
    assert offer_of(step)["slots"] == ["2026-10-08T09:00:00-05:00"] and not step.settled
    assert "on another day" in step.reply and all(call[0] != "book" for call in agenda.calls)


def test_a_day_and_part_of_day_in_the_answer_offers_slots_that_fit():
    # Prueba de jurado M1 t7: "next Tuesday afternoon" ofrecía los mismos horarios del jueves.
    agenda = FakeAgenda(slots=SLOTS + [NEXT_TUESDAY_AFTERNOON])
    when = {"days": [1], "part": "afternoon", "other_day": False}
    step = run(follow_up(agenda, "conv-1", "lead-1", _offer(), "Next Tuesday afternoon?", TODAY, when))
    assert offer_of(step)["slots"] == [NEXT_TUESDAY_AFTERNOON] and "on Tuesday afternoon" in step.reply


def test_an_hour_on_another_day_than_the_one_named_is_not_booked():
    # "Thursday at 4 pm": la oferta tiene el miércoles a las 16:00, que no es el día que pidió.
    agenda = FakeAgenda()
    when = {"days": [3], "part": None, "other_day": False}
    step = run(follow_up(agenda, "conv-1", "lead-1", _offer(), "Thursday at 4 pm", TODAY, when))
    assert not step.booked and all(call[0] != "book" for call in agenda.calls)
    assert step.reply.startswith("I don't have available times on Thursday")


def test_saying_when_answers_the_offer():
    assert answers(_offer(), "Can we do another day?", OTHER_DAY)
    assert not answers(_offer(), "Is it close to the metro?")


def test_a_part_of_day_after_the_offer_can_be_the_same_day():
    # Prueba de jurado O6: tras la mañana del lunes, "anything in the afternoon?" saltaba al lunes siguiente.
    monday_morning, monday_afternoon = "2026-10-12T09:00:00-05:00", "2026-10-12T15:00:00-05:00"
    agenda = FakeAgenda(slots=[monday_morning, monday_afternoon, "2026-10-19T15:00:00-05:00"])
    offer = {"property": PROP, "advisor_id": "adv_poblado", "slots": [monday_morning]}
    when = {"days": [0], "part": "afternoon", "other_day": True}
    step = run(follow_up(agenda, "conv-1", "lead-1", offer, "Anything in the afternoon?", TODAY, when))
    assert offer_of(step)["slots"][0] == monday_afternoon
