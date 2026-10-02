"""Traspaso a un humano (A-13): cuándo una racha de turnos sin resolver cuenta como frustración.

`HUMAN_HANDOFF` se alcanza por tres caminos (ver `app/fsm.py`): petición explícita, una intención
fuera de alcance del agente, y una racha de turnos seguidos sin resolver. Este módulo es el tercero.

"Sin resolver" es la intención `otro` (el clasificador no supo qué quería el lead), salvo que en ese
mismo turno el lead haya dado un dato de calificación (presupuesto, quién decide, plazo): el
clasificador no tiene una intención para eso y lo deja en `otro`, pero el lead está cooperando (#125).
Un `otro` suelto
NO dispara nada —es ruido: un "ok" o un "gracias" caen ahí—, pero varios seguidos sí. Los turnos que
fuerza el guardrail de entrada (*jailbreak*, fuera de dominio) no cuentan: nadie puede provocar un
traspaso con mensajes adversariales.
"""
from __future__ import annotations

from typing import Any, Final, Mapping

from app.fsm import HUMAN_INTENTS, HUMAN_REQUESTED
from app.lead_scoring import declared_signal

HANDOFF_STATES: Final = frozenset({"HANDOFF", "HUMAN_HANDOFF"})  # estados en los que un asesor toma el lead

UNRESOLVED_INTENT: Final = "otro"
FRUSTRATION_STREAK: Final = 3  # turnos seguidos sin resolver que se leen como frustración

FRUSTRATION_REPLY: Final = ("Perdona que no te esté ayudando como esperas. "
                            "Te comunico con un asesor de nuestro equipo.")


def is_unresolved(intent: str, raw_signal: Mapping[str, Any] | None = None) -> bool:
    """`otro` sin ningún dato de calificación declarado en el turno."""
    return intent == UNRESOLVED_INTENT and not declared_signal(raw_signal or {})


def next_streak(current: int, intent: str, counts: bool = True,
                raw_signal: Mapping[str, Any] | None = None) -> int:
    """Racha tras este turno: suma si quedó sin resolver, se reinicia si se resolvió.

    `raw_signal` es la señal cruda del turno: si declara un dato de calificación, el turno cuenta
    como resuelto. Con `counts=False` (turno que fuerza el guardrail) la racha queda como estaba.
    """
    if not counts:
        return current
    return current + 1 if is_unresolved(intent, raw_signal) else 0


def is_frustrated(streak: int) -> bool:
    return streak >= FRUSTRATION_STREAK


def handoff_reason(after: str, trigger: str | None, intent: str) -> str | None:
    """Por qué la conversación llegó a un traspaso en este turno; None si no hubo traspaso (A-12).

    Los estados de traspaso son terminales, así que un trigger que termina en uno es siempre la
    transición de este turno: no hace falta comparar con el estado anterior.
    """
    if after not in HANDOFF_STATES or trigger is None:
        return None
    if trigger == HUMAN_REQUESTED:
        return intent if intent in HUMAN_INTENTS else "frustration"
    return trigger
