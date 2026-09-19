"""Máquina de estados de la conversación (A-1, docs/DESIGN/design.md §2).

Lógica pura: sin base de datos ni LLM. Un *trigger* (algo que pasó en la conversación) mueve la FSM
de un estado a otro solo si la arista existe en `TRANSITIONS`; cualquier otra combinación es un
error explícito. El estado se persiste en Postgres (state_store.py, DT-1, DT-11).

Estados terminales: HANDOFF (traspaso normal al asesor), HUMAN_HANDOFF (el lead pidió una persona) y
LOST (lead perdido). `HUMAN_HANDOFF` es alcanzable desde todos los estados no terminales.
"""
from __future__ import annotations

from typing import Final

from db.models import FSM_STATES

# Triggers.
GREETED: Final = "greeted"                        # primer mensaje del lead atendido
DISCOVERY_COMPLETE: Final = "discovery_complete"  # ya sabemos qué busca
QUALIFIED: Final = "qualified"                    # calificación suficiente para recomendar
VISIT_REQUESTED: Final = "visit_requested"        # el lead quiere visitar una propiedad
VISIT_SCHEDULED: Final = "visit_scheduled"        # la visita quedó agendada
NO_RESPONSE: Final = "no_response"                # sin respuesta tras N intentos (scheduler)
LEAD_RETURNED: Final = "lead_returned"            # el lead vuelve a escribir tras NURTURE
HUMAN_REQUESTED: Final = "human_requested"        # petición explícita / frustración / fuera de alcance

TERMINAL_STATES: Final = frozenset({"HANDOFF", "HUMAN_HANDOFF", "LOST"})

# (estado actual, trigger) -> estado nuevo. Es exactamente el diagrama de design.md §2.
TRANSITIONS: Final[dict[tuple[str, str], str]] = {
    ("GREETING", GREETED): "DISCOVERY",
    ("DISCOVERY", DISCOVERY_COMPLETE): "QUALIFY",
    ("QUALIFY", QUALIFIED): "RECOMMEND",
    ("RECOMMEND", VISIT_REQUESTED): "SCHEDULE",
    ("SCHEDULE", VISIT_SCHEDULED): "HANDOFF",
    ("DISCOVERY", NO_RESPONSE): "NURTURE",
    ("NURTURE", LEAD_RETURNED): "DISCOVERY",
    ("NURTURE", NO_RESPONSE): "LOST",
    **{(state, HUMAN_REQUESTED): "HUMAN_HANDOFF" for state in FSM_STATES if state not in TERMINAL_STATES},
}

TRIGGERS: Final = frozenset(trigger for _, trigger in TRANSITIONS)


class InvalidTransition(ValueError):
    def __init__(self, state: str, trigger: str) -> None:
        super().__init__(f"no hay transición desde {state} con el trigger {trigger!r}")
        self.state = state
        self.trigger = trigger


def next_state(state: str, trigger: str) -> str:
    try:
        return TRANSITIONS[(state, trigger)]
    except KeyError:
        raise InvalidTransition(state, trigger) from None


# Política de avance de la conversación con el fake LLM (CT-7): qué intención hace avanzar cada
# estado. Es deliberadamente mínima; A-2 y A-9 la reemplazan con señales reales del LLM.
_ADVANCE: Final[dict[str, tuple[str, str | None]]] = {
    # estado: (trigger, intención que lo dispara; None = cualquier intención)
    "GREETING": (GREETED, None),
    "DISCOVERY": (DISCOVERY_COMPLETE, "busqueda_propiedad"),
    "QUALIFY": (QUALIFIED, "consulta_precio"),
    "RECOMMEND": (VISIT_REQUESTED, "agendar_visita"),
    "SCHEDULE": (VISIT_SCHEDULED, "agendar_visita"),
}


def trigger_for(state: str, intent: str) -> str | None:
    """Trigger que provoca el mensaje de un lead en `state`, o None si no mueve la FSM.

    La intención `otro` (cajón de sastre) nunca dispara `HUMAN_REQUESTED`: revisar el criterio de
    A-2/A-13 antes de darle ese efecto.
    """
    if state in TERMINAL_STATES:
        return None
    if intent == "solicitud_humano":
        return HUMAN_REQUESTED
    if state == "NURTURE":
        return LEAD_RETURNED
    trigger, required_intent = _ADVANCE[state]
    return trigger if required_intent in (None, intent) else None
