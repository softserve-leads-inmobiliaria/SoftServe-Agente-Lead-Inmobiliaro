"""Reglas de calificación BANT-E (C-4).

Función pura: sin red, sin base de datos, sin estado. Implementa exactamente la tabla de puntos
de `docs/DESIGN/design.md` §3 — si un peso cambia, se cambia allí y aquí, nunca solo aquí.

El LLM nunca emite el número final: entrega una `ScoreSignal` (contrato en
`contracts/fsm_state.schema.json`) y `compute_score` calcula el resultado. La persistencia y la
integración con la FSM son de A-9; el fair housing en el scoring, de A-11/C-6.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

# Authority mide si el lead dijo cómo se decide, no con quién (A-11, DT-38). El contrato ya no distingue
# solo, pareja ni familia (#119, DT-40): estado civil y situación familiar son categorías protegidas
# (`guardrails.md` §1), y lo que no se guarda no se puede volver a puntuar.
DECISION_MAKER_POINTS: Mapping[str, int] = {
    "declarado": 20,
    "no_declarado": 0,
}
URGENCY_POINTS: Mapping[str, int] = {
    "inmediata": 20,
    "3_meses": 12,
    "explorando": 5,
    "no_declarada": 0,
}

# Cómo se leen en las razones del score, que ve el asesor (#202): el valor del contrato no es texto.
_DECISION_MAKER_TEXT: Mapping[str, str] = {"declarado": "how the decision is made was stated",
                                           "no_declarado": "how the decision is made wasn't stated"}
URGENCY_TEXT: Mapping[str, str] = {"inmediata": "immediate", "3_meses": "within 3 months",
                                    "explorando": "just exploring", "no_declarada": "not stated"}

COLD_MAX = 40
WARM_MAX = 70


@dataclass(frozen=True)
class ScoreSignal:
    """Señal estructurada por turno. No tiene campo para categorías protegidas (fair housing)."""

    budget_hint: Optional[str]
    decision_maker: str
    urgency: str
    matching_properties: int
    meets_requirements: Optional[bool]

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "ScoreSignal":
        return cls(
            budget_hint=data["budget_hint"],  # type: ignore[arg-type]
            decision_maker=data["decision_maker"],  # type: ignore[arg-type]
            urgency=data["urgency"],  # type: ignore[arg-type]
            matching_properties=data["matching_properties"],  # type: ignore[arg-type]
            meets_requirements=data["meets_requirements"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class Score:
    need_fit: int
    budget: int
    authority: int
    timing: int
    eligibility: int
    total: int
    band: str
    reasons: tuple[str, ...]

    def to_lead_score(self, lead_id: str) -> dict:
        """Forma del contrato `LeadScore` (`contracts/fsm_state.schema.json#/$defs/LeadScore`)."""
        return {
            "lead_id": lead_id,
            "need_fit": self.need_fit,
            "budget": self.budget,
            "authority": self.authority,
            "timing": self.timing,
            "eligibility": self.eligibility,
            "total": self.total,
            "band": self.band,
            "reasons": list(self.reasons),
        }


def band_for(total: int) -> str:
    """COLD <= 40, WARM 41-70, HOT >= 71."""
    if total < 0 or total > 100:
        raise ValueError(f"total fuera de rango 0-100: {total}")
    if total <= COLD_MAX:
        return "COLD"
    if total <= WARM_MAX:
        return "WARM"
    return "HOT"


def _lookup(table: Mapping[str, int], value: str, field: str) -> int:
    try:
        return table[value]
    except KeyError:
        raise ValueError(f"{field} inválido: {value!r} (válidos: {sorted(table)})") from None


def score_budget(budget_hint: Optional[str]) -> tuple[int, str]:
    if budget_hint is None or not budget_hint.strip():
        return 0, "Budget: no budget stated"
    return 20, "Budget: budget stated"


def score_authority(decision_maker: str) -> tuple[int, str]:
    points = _lookup(DECISION_MAKER_POINTS, decision_maker, "decision_maker")
    return points, f"Authority: {_DECISION_MAKER_TEXT[decision_maker]}"


def score_need_fit(matching_properties: int) -> tuple[int, str]:
    if isinstance(matching_properties, bool) or not isinstance(matching_properties, int):
        raise ValueError(f"matching_properties debe ser int: {matching_properties!r}")
    if matching_properties < 0:
        raise ValueError(f"matching_properties no puede ser negativo: {matching_properties}")
    if matching_properties == 0:
        points = 0
    elif matching_properties <= 2:
        points = 10
    else:
        points = 20
    noun = "property matches" if matching_properties == 1 else "properties match"
    return points, f"Need fit: {matching_properties} {noun}"


def score_timing(urgency: str) -> tuple[int, str]:
    points = _lookup(URGENCY_POINTS, urgency, "urgency")
    return points, f"Timing: {URGENCY_TEXT[urgency]}"


def score_eligibility(meets_requirements: Optional[bool]) -> tuple[int, str]:
    if meets_requirements is True:
        return 20, "Eligibility: meets the requirements"
    if meets_requirements is False:
        return 0, "Eligibility: doesn't meet the requirements"
    return 0, "Eligibility: requirements not checked yet"


def compute_score(signal: ScoreSignal) -> Score:
    """Calcula el score BANT-E de una señal. Determinista y sin efectos secundarios."""
    need_fit, r_need = score_need_fit(signal.matching_properties)
    budget, r_budget = score_budget(signal.budget_hint)
    authority, r_auth = score_authority(signal.decision_maker)
    timing, r_timing = score_timing(signal.urgency)
    eligibility, r_elig = score_eligibility(signal.meets_requirements)
    total = need_fit + budget + authority + timing + eligibility
    return Score(
        need_fit=need_fit,
        budget=budget,
        authority=authority,
        timing=timing,
        eligibility=eligibility,
        total=total,
        band=band_for(total),
        reasons=(r_need, r_budget, r_auth, r_timing, r_elig),
    )
