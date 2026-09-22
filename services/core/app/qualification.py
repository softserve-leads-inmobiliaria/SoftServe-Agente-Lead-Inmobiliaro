"""Cuándo un lead pasa a "calificado" (B-7, DT-24).

Lógica pura: la banda del score BANT-E (A-9) cruza a WARM o HOT. Es el significado de negocio de
"vale la pena que un asesor lo llame", y es independiente del estado de la FSM: DT-14 decidió que el
score no condiciona las transiciones, y esto no lo cambia — solo lee la banda que ya se calcula.
"""
from __future__ import annotations

from typing import Final

QUALIFIED_BANDS: Final = frozenset({"WARM", "HOT"})


def just_qualified(previous_band: str | None, current_band: str) -> bool:
    """True solo en el turno en que la banda entra a WARM/HOT viniendo de COLD o de ningún score."""
    return current_band in QUALIFIED_BANDS and previous_band not in QUALIFIED_BANDS
