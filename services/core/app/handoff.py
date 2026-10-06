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

from app.fsm import HUMAN_INTENTS, HUMAN_REQUESTED, VISIT_SCHEDULED
from app.lead_scoring import declared_signal

HANDOFF_STATES: Final = frozenset({"HANDOFF", "HUMAN_HANDOFF"})  # estados en los que un asesor toma el lead

UNRESOLVED_INTENT: Final = "otro"
VISIT_REQUESTED_REASON: Final = "visit_requested"  # visita pedida sin reservar: la coordina el asesor (B-8)
FRUSTRATION_STREAK: Final = 3  # turnos seguidos sin resolver que se leen como frustración

FRUSTRATION_REPLY: Final = ("Sorry I'm not being as helpful as you expected. "
                            "I'll put you in touch with one of our agents.")


def is_unresolved(intent: str, raw_signal: Mapping[str, Any] | None = None) -> bool:
    """`otro` sin ningún dato de calificación declarado en el turno."""
    return intent == UNRESOLVED_INTENT and not declared_signal(raw_signal or {})


def next_streak(current: int, intent: str, counts: bool = True,
                raw_signal: Mapping[str, Any] | None = None, resolved: bool = False) -> int:
    """Racha tras este turno: suma si quedó sin resolver, se reinicia si se resolvió.

    `raw_signal` es la señal cruda del turno: si declara un dato de calificación, el turno cuenta
    como resuelto. `resolved` lo marca el código cuando el turno resolvió algo por su cuenta (una
    visita agendada, B-8: "la 2" no tiene intención propia). Con `counts=False` (turno que fuerza el
    guardrail) la racha queda como estaba.
    """
    if not counts:
        return current
    return current + 1 if not resolved and is_unresolved(intent, raw_signal) else 0


def is_frustrated(streak: int) -> bool:
    return streak >= FRUSTRATION_STREAK


def handoff_reason(after: str, trigger: str | None, intent: str, visit_booked: bool = True) -> str | None:
    """Por qué la conversación llegó a un traspaso en este turno; None si no hubo traspaso (A-12).

    Los estados de traspaso son terminales, así que un trigger que termina en uno es siempre la
    transición de este turno: no hace falta comparar con el estado anterior. Una visita que la agenda
    no pudo reservar (B-8) se traspasa como `visit_requested`: el asesor tiene que coordinarla.
    """
    if after not in HANDOFF_STATES or trigger is None:
        return None
    if trigger == HUMAN_REQUESTED:
        return intent if intent in HUMAN_INTENTS else "frustration"
    if trigger == VISIT_SCHEDULED and not visit_booked:
        return VISIT_REQUESTED_REASON
    return trigger
