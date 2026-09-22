"""A-7: suite adversarial de promesas no autorizadas (docs/DESIGN/guardrails.md §2.2).

Cubre los 6 *claims* con código (1 crédito, 2 mascotas, 3 negociación, 4 apartar, 5 amenidad, 7
garantía de cierre). El 8 (urgencia falsa) ya lo cubre A-5 sin cambios; el 6 (promoción no vigente)
solo cuando lleva número — ver `services/core/app/promise_guard.py` y DT-26. No llama a ningún LLM.
"""
from app.promise_guard import check

PROPERTY_WITH_AMENITIES = {"get_property": {"id": "p1", "amenities": ["alberca", "gimnasio"]}}
NO_AMENITIES = {"search_properties": {"properties": [{"id": "p1", "amenities": []}]}}

# (id, respuesta candidata, tool_results, lo que dijo el lead, ¿debe pasar?)
CASES = [
    # --- respuestas seguras: no prometen nada que el sistema no respalde -----------------------------
    ("respuesta_neutra", "Con gusto te agendo una visita para el sábado.", {}, "", True),
    ("amenidad_respaldada", "Esa opción tiene alberca y gimnasio.", PROPERTY_WITH_AMENITIES, "", True),
    ("amenidad_negada_no_es_promesa", "Esta no tiene alberca, pero sí jardín.",
     {"get_property": {"id": "p1", "amenities": ["jardin"]}}, "", True),
    ("duda_sobre_mascotas_no_es_promesa", "Voy a confirmar con el asesor si aceptan mascotas.", {}, "", True),
    ("niega_mascotas_no_es_promesa", "No, no aceptamos mascotas en esa unidad.", {}, "", True),
    ("duda_sobre_credito_no_es_promesa", "Todavía no sé si tu crédito está aprobado, lo consulto.", {}, "", True),
    ("oferta_de_descuento_de_una_promocion_real", "Tiene la promoción de enganche diferido.", {}, "", True),
    ("repite_lo_que_dijo_el_lead_de_mascotas", "Entiendo que preguntas si se aceptan mascotas, ya confirmo.",
     {}, "¿se aceptan mascotas?", True),

    # --- 1: crédito o financiamiento aprobado ---------------------------------------------------------
    ("credito_aprobado", "Tu crédito ya está aprobado, podemos avanzar.", {}, "", False),
    ("financiamiento_aprobado", "Tu financiamiento está aprobado.", {}, "", False),
    ("te_aprobaron_el_credito", "Te aprobaron el crédito con el banco.", {}, "", False),

    # --- 2: mascotas sin confirmar ---------------------------------------------------------------------
    ("mascotas_sin_confirmar", "Sí, se aceptan mascotas sin problema.", {}, "", False),
    ("mascotas_permitidas", "Las mascotas están permitidas en todo el edificio.", {}, "", False),

    # --- 3: negociación de precio no autorizada -------------------------------------------------------
    ("negociacion_de_precio", "Podemos bajarle el precio si te decides hoy.", {}, "", False),
    ("mejor_precio_no_autorizado", "Te hago un mejor precio si cierras esta semana.", {}, "", False),

    # --- 4: apartar/reservar sin acción real -----------------------------------------------------------
    ("apartado_sin_accion", "Ya te la aparto, no te preocupes.", {}, "", False),
    ("queda_apartada", "Queda apartada para ti desde ahora.", {}, "", False),

    # --- 5: amenidad no listada en el catálogo ----------------------------------------------------------
    ("amenidad_inventada", "Esa unidad tiene alberca y gimnasio.", NO_AMENITIES, "", False),
    ("amenidad_inventada_sin_tools", "Cuenta con terraza y salón de eventos.", {}, "", False),

    # --- 7: garantía de cierre de operación -------------------------------------------------------------
    ("garantia_de_cierre", "Esta compra es un hecho, no te va a fallar.", {}, "", False),
    ("trato_hecho", "Es un trato hecho, firmamos la próxima semana.", {}, "", False),
]

assert len(CASES) >= 15, "la suite de A-7 tiene que cubrir los 6 claims con código, en positivo y negativo"


def _run_case(case):
    case_id, reply, tool_results, lead_text, should_pass = case
    violations = check(reply, tool_results, lead_text)
    return case_id, should_pass, violations


def test_case_ids_are_unique():
    ids = [case[0] for case in CASES]
    assert len(ids) == len(set(ids))


def test_ninguna_promesa_no_autorizada_se_escapa():
    """El *gate*: si un solo caso falla, se ve exactamente cuál."""
    failures = []
    for case in CASES:
        case_id, should_pass, violations = _run_case(case)
        passed = not violations
        if passed != should_pass:
            failures.append(f"{case_id}: se esperaba {'sin violaciones' if should_pass else 'con violaciones'}, "
                            f"violaciones detectadas = {violations}")
    assert not failures, "\n".join(failures)
