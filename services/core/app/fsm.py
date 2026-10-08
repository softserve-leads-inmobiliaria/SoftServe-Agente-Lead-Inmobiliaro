"""Máquina de estados de la conversación (A-1, docs/DESIGN/design.md §2).

Lógica pura: sin base de datos ni LLM. Un *trigger* (algo que pasó en la conversación) mueve la FSM
de un estado a otro solo si la arista existe en `TRANSITIONS`; cualquier otra combinación es un
error explícito. El estado se persiste en Postgres (state_store.py, DT-1, DT-11).

Estados terminales: HANDOFF (traspaso normal al asesor), HUMAN_HANDOFF (el lead pidió una persona) y
LOST (lead perdido). `HUMAN_HANDOFF` es alcanzable desde todos los estados no terminales, y `SCHEDULE` desde
todos los activos: la visita se puede pedir en cualquier momento de la conversación (#229, DT-61).
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
# Donde se puede pedir la visita: todos los estados activos menos `SCHEDULE`, que ya la está agendando (#229).
VISIT_STATES: Final = frozenset(state for state in FSM_STATES if state not in TERMINAL_STATES | {"SCHEDULE"})

# (estado actual, trigger) -> estado nuevo. Es exactamente el diagrama de design.md §2.
TRANSITIONS: Final[dict[tuple[str, str], str]] = {
    ("GREETING", GREETED): "DISCOVERY",
    ("DISCOVERY", DISCOVERY_COMPLETE): "QUALIFY",
    ("QUALIFY", QUALIFIED): "RECOMMEND",
    ("SCHEDULE", VISIT_SCHEDULED): "HANDOFF",
    ("DISCOVERY", NO_RESPONSE): "NURTURE",
    ("NURTURE", LEAD_RETURNED): "DISCOVERY",
    ("NURTURE", NO_RESPONSE): "LOST",
    **{(state, VISIT_REQUESTED): "SCHEDULE" for state in VISIT_STATES},
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


# Qué hace avanzar cada estado del embudo (#229, DT-61). Avanza por lo que pasó en la conversación, no por la
# etiqueta del clasificador: en vivo, un lead de un anuncio que preguntaba la disponibilidad y daba su presupuesto
# se quedaba en DISCOVERY porque ningún mensaje se clasificaba `busqueda_propiedad`. La intención sigue valiendo
# (es lo único que da el fake LLM, CT-7).
_ADVANCE: Final[dict[str, tuple[str, str | None]]] = {
    # estado: (trigger, intención que lo dispara; None = cualquier intención)
    "GREETING": (GREETED, None),
    "DISCOVERY": (DISCOVERY_COMPLETE, "busqueda_propiedad"),  # o buscó o miró una propiedad (`explored`)
    "QUALIFY": (QUALIFIED, "consulta_precio"),  # o ya hay datos de calificación (`qualifying`)
}


# Intenciones que un agente de ventas no atiende: las ve una persona (A-13).
HUMAN_INTENTS: Final = frozenset({"solicitud_humano", "fuera_de_alcance"})


def trigger_for(state: str, intent: str, frustrated: bool = False, visit_settled: bool = False,
                visit_started: bool = False, explored: bool = False, qualifying: bool = False) -> str | None:
    """Trigger que provoca el mensaje de un lead en `state`, o None si no mueve la FSM.

    `HUMAN_HANDOFF` se alcanza por tres caminos: petición explícita, intención fuera de alcance y
    una racha de turnos sin resolver (`frustrated`, ver `app/handoff.py`). La intención `otro`
    (cajón de sastre) por sí sola nunca lo dispara: un mensaje que el clasificador no entendió es
    ruido, no una petición; solo cuenta cuando se repite.

    `SCHEDULE` no avanza por una intención: avanza cuando el código deja resuelta la visita
    (`visit_settled`, B-8): reservada en la agenda o con el horario preferido del lead para el asesor. Tampoco
    se llega por una intención: se llega cuando el código arrancó la agenda (`visit_started`), desde cualquier
    estado de `VISIT_STATES`.

    `explored` (el turno buscó o miró una propiedad) cierra DISCOVERY; `qualifying` (la señal acumulada ya
    tiene algún dato de calificación) cierra QUALIFY.
    """
    if state in TERMINAL_STATES:
        return None
    if intent in HUMAN_INTENTS or frustrated:
        return HUMAN_REQUESTED
    if state == "SCHEDULE":
        return VISIT_SCHEDULED if visit_settled else None
    if visit_started:
        return VISIT_REQUESTED
    if state == "NURTURE":
        return LEAD_RETURNED
    if state not in _ADVANCE:
        return None
    trigger, required_intent = _ADVANCE[state]
    signalled = (state == "DISCOVERY" and explored) or (state == "QUALIFY" and qualifying)
    return trigger if signalled or required_intent in (None, intent) else None
