"""Tests de C-4: reglas BANT-E (tabla de docs/DESIGN/design.md §3)."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.scoring import (
    ScoreSignal,
    band_for,
    compute_score,
    score_authority,
    score_budget,
    score_eligibility,
    score_need_fit,
    score_timing,
)

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts" / "fsm_state.schema.json").read_text(
        encoding="utf-8"
    )
)


def signal(**overrides):
    base = dict(
        budget_hint=None,
        decision_maker="no_declarado",
        urgency="no_declarada",
        matching_properties=0,
        meets_requirements=None,
    )
    return ScoreSignal(**{**base, **overrides})


# --- Budget -----------------------------------------------------------------
@pytest.mark.parametrize("hint,points", [
    (None, 0), ("", 0), ("   ", 0),
    ("1.5M-2.5M MXN", 20), ("no sé, algo flexible", 20),
])
def test_budget(hint, points):
    assert score_budget(hint)[0] == points


# --- Authority --------------------------------------------------------------
@pytest.mark.parametrize("dm,points", [
    ("declarado", 20), ("no_declarado", 0),
])
def test_authority(dm, points):
    assert score_authority(dm)[0] == points


# --- Need fit ---------------------------------------------------------------
@pytest.mark.parametrize("n,points", [
    (0, 0), (1, 10), (2, 10), (3, 20), (50, 20),
])
def test_need_fit(n, points):
    assert score_need_fit(n)[0] == points


@pytest.mark.parametrize("bad", [-1, True, 1.5, "2"])
def test_need_fit_rejects_invalid(bad):
    with pytest.raises(ValueError):
        score_need_fit(bad)


# --- Timing -----------------------------------------------------------------
@pytest.mark.parametrize("urgency,points", [
    ("inmediata", 20), ("3_meses", 12), ("explorando", 5), ("no_declarada", 0),
])
def test_timing(urgency, points):
    assert score_timing(urgency)[0] == points


# --- Eligibility ------------------------------------------------------------
@pytest.mark.parametrize("meets,points", [(True, 20), (False, 0), (None, 0)])
def test_eligibility(meets, points):
    assert score_eligibility(meets)[0] == points


# --- Valores inválidos ------------------------------------------------------
def test_unknown_enum_values_raise():
    with pytest.raises(ValueError):
        score_authority("abuelo")
    with pytest.raises(ValueError):
        score_timing("ayer")


# --- Bandas: casos límite ---------------------------------------------------
@pytest.mark.parametrize("total,band", [
    (0, "COLD"), (40, "COLD"), (41, "WARM"), (70, "WARM"), (71, "HOT"), (100, "HOT"),
])
def test_band_boundaries(total, band):
    assert band_for(total) == band


@pytest.mark.parametrize("total", [-1, 101])
def test_band_out_of_range(total):
    with pytest.raises(ValueError):
        band_for(total)


# --- Composición ------------------------------------------------------------
def test_empty_signal_is_cold_zero():
    s = compute_score(signal())
    assert (s.total, s.band) == (0, "COLD")


def test_max_signal_is_hot_100():
    s = compute_score(signal(
        budget_hint="2M", decision_maker="declarado", urgency="inmediata",
        matching_properties=5, meets_requirements=True,
    ))
    assert (s.total, s.band) == (100, "HOT")


def test_total_is_sum_of_dimensions_and_band_matches():
    s = compute_score(signal(
        budget_hint="1.5M", decision_maker="declarado", urgency="3_meses",
        matching_properties=2, meets_requirements=None,
    ))
    assert (s.need_fit, s.budget, s.authority, s.timing, s.eligibility) == (10, 20, 20, 12, 0)
    assert s.total == 62 and s.band == "WARM"


def test_each_dimension_has_a_reason():
    assert len(compute_score(signal()).reasons) == 5


def test_the_reasons_are_in_english_without_contract_values():
    # #202: las lee el asesor en la tarjeta y en el panel; "3_meses" o "declarado" no son texto.
    s = compute_score(signal(budget_hint="$300,000", decision_maker="declarado", urgency="3_meses",
                             matching_properties=1, meets_requirements=True))
    assert s.reasons == (
        "Need fit: 1 property matches",
        "Budget: budget stated",
        "Authority: how the decision is made was stated",
        "Timing: within 3 months",
        "Eligibility: meets the requirements",
    )
    assert compute_score(signal()).reasons[2:] == (
        "Authority: how the decision is made wasn't stated", "Timing: not stated",
        "Eligibility: requirements not checked yet",
    )


def test_is_deterministic():
    sig = signal(budget_hint="1M", decision_maker="declarado", urgency="explorando")
    assert compute_score(sig) == compute_score(sig)


def test_output_matches_lead_score_contract():
    lead_id = "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
    doc = compute_score(signal(budget_hint="1M", matching_properties=3)).to_lead_score(lead_id)
    validator = Draft202012Validator(
        {"$schema": SCHEMA["$schema"], "$ref": "#/$defs/LeadScore", "$defs": SCHEMA["$defs"]},
        format_checker=FormatChecker(),
    )
    validator.validate(doc)


def test_from_dict_accepts_contract_score_signal():
    example = json.loads(
        (Path(__file__).resolve().parents[3] / "contracts" / "examples" / "fsm_state.valid.json")
        .read_text(encoding="utf-8")
    )["score_signal"]
    assert compute_score(ScoreSignal.from_dict(example)).total == 10 + 20 + 20 + 12 + 0
