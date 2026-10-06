"""Tests de A-9 (sin base de datos): la señal se acumula turno a turno y el score sale de las reglas de C-4."""
import pytest

from app.fakes.fake_llm import FakeLLM
from app.lead_scoring import TurnEvidence, merge_signal, score_turn
from app.scoring import ScoreSignal, compute_score

EMPTY = {"budget_hint": None, "decision_maker": "no_declarado", "urgency": "no_declarada",
         "matching_properties": 0, "meets_requirements": None}


def turn(raw=None, **tool_results):
    return TurnEvidence(raw_signal=raw or {}, tool_results=tool_results)


def test_an_empty_turn_leaves_the_signal_and_the_score_at_zero():
    signal, score = score_turn(EMPTY, turn())
    assert signal == EMPTY
    assert (score.total, score.band) == (0, "COLD")


def test_what_the_lead_declares_is_kept_in_later_turns():
    signal = merge_signal(EMPTY, turn({"budget_hint": "3 millones", "decision_maker": "declarado", "urgency": None}))
    signal = merge_signal(signal, turn({"budget_hint": None, "decision_maker": None, "urgency": "inmediata"}))
    signal = merge_signal(signal, turn())
    assert signal["budget_hint"] == "3 millones"
    assert signal["decision_maker"] == "declarado" and signal["urgency"] == "inmediata"


def test_the_latest_declaration_wins():
    signal = merge_signal(EMPTY, turn({"urgency": "explorando", "decision_maker": "declarado"}))
    signal = merge_signal(signal, turn({"urgency": "inmediata", "decision_maker": "declarado"}))
    assert (signal["urgency"], signal["decision_maker"]) == ("inmediata", "declarado")


def test_the_score_grows_as_the_conversation_reveals_more():
    totals = []
    signal = dict(EMPTY)
    for evidence in (
        turn(),
        turn({"budget_hint": "presupuesto de 3 millones"}),
        turn({"decision_maker": "declarado"}),
        turn({"urgency": "inmediata"}),
        turn(search_properties={"properties": [{}, {}, {}]}),
        turn(check_requirements={"meets_requirements": True}),
    ):
        signal, score = score_turn(signal, evidence)
        totals.append((score.total, score.band))
    assert totals == [(0, "COLD"), (20, "COLD"), (40, "COLD"), (60, "WARM"), (80, "HOT"), (100, "HOT")]


def test_the_score_is_exactly_what_the_pure_rules_compute():
    signal, score = score_turn(EMPTY, turn({"budget_hint": "2 millones", "decision_maker": "declarado",
                                            "urgency": "3_meses"}, search_properties={"properties": [{}]}))
    assert score == compute_score(ScoreSignal.from_dict(signal))
    assert (score.need_fit, score.budget, score.authority, score.timing, score.eligibility) == (10, 20, 20, 12, 0)
    assert len(score.reasons) == 5


def test_matching_properties_comes_from_the_search_result_not_from_the_llm():
    signal = merge_signal(EMPTY, turn({"matching_properties": 99}, search_properties={"properties": [{}, {}]}))
    assert signal["matching_properties"] == 2
    assert merge_signal(EMPTY, turn({"matching_properties": 99}))["matching_properties"] == 0


def test_eligibility_comes_from_check_requirements_not_from_the_llm():
    assert merge_signal(EMPTY, turn({"meets_requirements": True}))["meets_requirements"] is None
    assert merge_signal(EMPTY, turn(check_requirements={"meets_requirements": False}))["meets_requirements"] is False


def test_a_search_without_results_resets_the_match_count():
    current = EMPTY | {"matching_properties": 5}
    assert merge_signal(current, turn(search_properties={"properties": []}))["matching_properties"] == 0


# --- A-15: la propiedad del anuncio cuenta como coincidencia -------------------------------------------------

LISTING = {"id": "prop_00231", "title": "Depto 2 rec. en El Poblado", "status": "disponible"}


def from_listing(listing=LISTING, **tool_results):
    return TurnEvidence(tool_results=tool_results, listing=listing)


def test_an_available_listing_counts_as_one_match():
    signal, score = score_turn(EMPTY, from_listing())
    assert signal["matching_properties"] == 1 and score.need_fit > 0


@pytest.mark.parametrize("status", ["apartado", "cerrado", "sin_verificar", None])
def test_a_listing_that_is_not_available_does_not_count(status):
    assert merge_signal(EMPTY, from_listing(LISTING | {"status": status}))["matching_properties"] == 0


def test_a_search_in_the_same_turn_with_more_results_wins():
    assert merge_signal(EMPTY, from_listing(search_properties={"properties": [{}, {}, {}]}))["matching_properties"] == 3


def test_an_empty_search_in_the_same_turn_does_not_erase_the_listing():
    assert merge_signal(EMPTY, from_listing(search_properties={"properties": []}))["matching_properties"] == 1


def test_the_listing_never_lowers_what_an_earlier_search_found():
    assert merge_signal(EMPTY | {"matching_properties": 4}, from_listing())["matching_properties"] == 4


def test_a_later_search_wins_as_before():
    signal = merge_signal(EMPTY, from_listing())
    assert merge_signal(signal, turn(search_properties={"properties": [{}, {}]}))["matching_properties"] == 2
    assert merge_signal(signal, turn())["matching_properties"] == 1


def test_a_turn_without_tools_keeps_the_previous_matches_and_eligibility():
    current = EMPTY | {"matching_properties": 4, "meets_requirements": True}
    merged = merge_signal(current, turn({"urgency": "explorando"}))
    assert (merged["matching_properties"], merged["meets_requirements"]) == (4, True)


@pytest.mark.parametrize("raw", [
    {"decision_maker": "el_dueño", "urgency": "mañana"},
    {"decision_maker": 5, "urgency": ["inmediata"]},
    {"budget_hint": 3000000},
    {"budget_hint": "   "},
    {"decision_maker": "no_declarado", "urgency": "no_declarada"},
])
def test_invalid_or_empty_values_from_the_llm_never_overwrite_what_was_declared(raw):
    current = EMPTY | {"budget_hint": "3 millones", "decision_maker": "declarado", "urgency": "inmediata"}
    assert merge_signal(current, turn(raw)) == current


def test_unknown_fields_from_the_llm_are_ignored():
    """Sin campo para categorías protegidas: si el LLM las mete, no llegan a la señal."""
    signal = merge_signal(EMPTY, turn({"nacionalidad": "x", "religion": "y", "genero": "z"}))
    assert signal == EMPTY


def test_the_merge_does_not_mutate_its_input():
    current = dict(EMPTY)
    merge_signal(current, turn({"decision_maker": "declarado"}))
    assert current == EMPTY


# --- el extractor (fake LLM) solo emite los tres campos crudos --------------------------------

def test_the_fake_extractor_can_only_emit_the_three_raw_fields():
    signal = FakeLLM().extract_signal("Soy de otro país, de otra religión y de otro género")
    assert set(signal) == {"budget_hint", "decision_maker", "urgency"}
    assert all(value is None for value in signal.values())


@pytest.mark.parametrize("text,expected", [
    ("Hola", {"budget_hint": None, "decision_maker": None, "urgency": None}),
    ("Mi presupuesto es de 3 millones", {"budget_hint": "presupuesto es de 3 millones",
                                         "decision_maker": None, "urgency": None}),
    ("Tengo $2,500,000 disponibles", {"budget_hint": "$2,500,000", "decision_maker": None, "urgency": None}),
    ("Decido yo, y lo necesito urgente", {"budget_hint": None, "decision_maker": "declarado", "urgency": "inmediata"}),
    ("Lo veo con mi pareja, en tres meses", {"budget_hint": None, "decision_maker": "declarado", "urgency": "3_meses"}),
    ("Lo hablo con mi familia", {"budget_hint": None, "decision_maker": "declarado", "urgency": None}),
    ("Solo estoy viendo, sin prisa", {"budget_hint": None, "decision_maker": None, "urgency": "explorando"}),
    ("MI PRESUPUESTO: 2 MILLONES", {"budget_hint": "presupuesto: 2 millones", "decision_maker": None,
                                    "urgency": None}),
])
def test_fake_extractor_examples(text, expected):
    assert FakeLLM().extract_signal(text) == expected


def test_fake_extractor_output_always_merges_cleanly():
    for text in ("", "Hola", "presupuesto", "urgente y decido yo", "$", "mil"):
        merge_signal(EMPTY, turn(FakeLLM().extract_signal(text)))
