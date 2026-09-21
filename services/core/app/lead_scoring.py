"""Score BANT-E turno a turno (A-9): acumula la señal del lead y calcula el score con las reglas de C-4.

Reparto de responsabilidades (contrato `ScoreSignal`, design.md §3):
- el LLM emite solo señal cruda: `budget_hint`, `decision_maker`, `urgency`;
- `matching_properties` y `meets_requirements` los deriva el código de los resultados de las tools;
- el número final lo calcula `compute_score`, nunca el LLM.

Todo aquí es puro: recibe la señal acumulada, lo que trae el turno y devuelve la nueva señal y el score.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.scoring import DECISION_MAKER_POINTS, URGENCY_POINTS, Score, ScoreSignal, compute_score

NOT_DECLARED = {"decision_maker": "no_declarado", "urgency": "no_declarada"}
ENUMS = {"decision_maker": DECISION_MAKER_POINTS, "urgency": URGENCY_POINTS}


@dataclass(frozen=True)
class TurnEvidence:
    """Lo que trae un turno para el score: señal cruda del LLM y resultados de las tools del turno."""

    raw_signal: Mapping[str, Any] = field(default_factory=dict)
    tool_results: Mapping[str, Any] = field(default_factory=dict)


def merge_signal(current: Mapping[str, Any], evidence: TurnEvidence) -> dict[str, Any]:
    """Acumula: lo que el lead declara en un turno se conserva en los siguientes.

    Un campo que el mensaje no menciona (o que el LLM devuelve inválido) no pisa lo ya declarado:
    ignorarlo es más seguro que inventar un valor. Lo declarado más recientemente gana.
    """
    merged = dict(current)
    raw = evidence.raw_signal

    budget = raw.get("budget_hint")
    if isinstance(budget, str) and budget.strip():
        merged["budget_hint"] = budget.strip()
    for name, table in ENUMS.items():
        value = raw.get(name)
        if isinstance(value, str) and value in table and value != NOT_DECLARED[name]:
            merged[name] = value

    found = evidence.tool_results.get("search_properties")
    if isinstance(found, Mapping) and isinstance(found.get("properties"), list):
        merged["matching_properties"] = len(found["properties"])
    checked = evidence.tool_results.get("check_requirements")
    if isinstance(checked, Mapping) and isinstance(checked.get("meets_requirements"), bool):
        merged["meets_requirements"] = checked["meets_requirements"]
    return merged


def score_turn(current: Mapping[str, Any], evidence: TurnEvidence) -> tuple[dict[str, Any], Score]:
    """(señal acumulada, score) después de este turno."""
    signal = merge_signal(current, evidence)
    return signal, compute_score(ScoreSignal.from_dict(signal))
