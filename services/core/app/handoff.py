"""Traspaso a un humano (A-13): cuándo una racha de turnos sin resolver cuenta como frustración.

`HUMAN_HANDOFF` se alcanza por tres caminos (ver `app/fsm.py`): petición explícita, una intención
fuera de alcance del agente, y una racha de turnos seguidos sin resolver. Este módulo es el tercero.

"Sin resolver" es la intención `otro` (el clasificador no supo qué quería el lead). Un `otro` suelto
NO dispara nada —es ruido: un "ok" o un "gracias" caen ahí—, pero varios seguidos sí. Los turnos que
fuerza el guardrail de entrada (*jailbreak*, fuera de dominio) no cuentan: nadie puede provocar un
traspaso con mensajes adversariales.
"""
from __future__ import annotations

from typing import Final

UNRESOLVED_INTENT: Final = "otro"
FRUSTRATION_STREAK: Final = 3  # turnos seguidos sin resolver que se leen como frustración

FRUSTRATION_REPLY: Final = ("Perdona que no te esté ayudando como esperas. "
                            "Te comunico con un asesor de nuestro equipo.")


def next_streak(current: int, intent: str, counts: bool = True) -> int:
    """Racha tras este turno: suma si quedó sin resolver, se reinicia si se resolvió.

    Con `counts=False` (turno que fuerza el guardrail) la racha queda como estaba.
    """
    if not counts:
        return current
    return current + 1 if intent == UNRESOLVED_INTENT else 0


def is_frustrated(streak: int) -> bool:
    return streak >= FRUSTRATION_STREAK
