"""Lo que cada guion de harness/fixtures debe dejar en Postgres al terminar.

Una sola fuente para `tests/harness/test_e2e.py` (bases temporales) y `live_run.py` (la corrida real
contra el stack de Docker, OBS-6). Un guion nuevo en `harness/fixtures/` tiene que declarar acá su
estado final: `test_every_fixture_has_a_declared_final_state` lo exige.
"""

# Estado final de la FSM en `conversations.fsm_state` tras reproducir cada guion (A-1).
EXPECTED_FINAL_STATE = {
    "saludo_y_busqueda": "RECOMMEND",
    "deep_link_y_visita": "HUMAN_HANDOFF",
    "flujo_completo_hasta_handoff": "HANDOFF",
    # #229 (DT-61): el presupuesto, quién decide y cuándo cierran QUALIFY, sin esperar a `consulta_precio`.
    "lead_que_declara_todo": "RECOMMEND",
    "queja_a_humano": "HUMAN_HANDOFF",
    "solicitud_humano_directa": "HUMAN_HANDOFF",
    "sigue_explorando_zona": "DISCOVERY",
    "deep_link_solo_precio": "RECOMMEND",
}

# Con el LLM real (`live_run.py --sin-frases`), otros estados finales también válidos (corridas del 3-oct,
# #48). El modelo de Bedrock suele pedir la zona antes de buscar: sin búsqueda la FSM se queda en `QUALIFY`,
# y si el lead pide la visita sin haber visto una propiedad, la conversación espera su horario preferido en
# `SCHEDULE`. El modelo no siempre elige igual, así que el mismo guion puede llegar a uno u otro.
ALSO_VALID_WITH_LLM = {
    "saludo_y_busqueda": ("QUALIFY",),
    "deep_link_solo_precio": ("QUALIFY",),
    "flujo_completo_hasta_handoff": ("SCHEDULE",),
}


def accepted_final_states(name: str, real_llm: bool) -> set[str]:
    """Los estados finales que se aceptan para un guion. Con el agente *fake*, solo el declarado."""
    expected = EXPECTED_FINAL_STATE.get(name)
    if expected is None:
        return set()
    return {expected, *(ALSO_VALID_WITH_LLM.get(name, ()) if real_llm else ())}
