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
    "lead_que_declara_todo": "QUALIFY",
    "queja_a_humano": "HUMAN_HANDOFF",
    "solicitud_humano_directa": "HUMAN_HANDOFF",
    "sigue_explorando_zona": "DISCOVERY",
    "deep_link_solo_precio": "RECOMMEND",
}
