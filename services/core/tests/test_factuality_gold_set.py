"""A-5: gold set de ≥30 casos, *gate* de CI (100% en verde, fusionado con la historia — ver A-05.md).

Cada caso es una respuesta candidata realista del agente, junto con lo que de verdad devolvieron las
tools en ese turno (y lo que dijo el lead, cuando aplica), y si esa respuesta debería pasar el
guardrail de factualidad o no. No llama a ningún LLM: prueba la regla de la que depende todo lo
demás (regenerar, degradar) directamente, así que corre sin credenciales y en milisegundos.

Al agregar un caso nuevo (un giro de redacción real que rompió el guardrail, una propiedad más), se
agrega aquí, nunca se borra uno viejo salvo que deje de aplicar.
"""
from app.factuality import check

SEARCH_RESULT = {
    "search_properties": {
        "properties": [
            {"id": "prop_00231", "title": "Depto 2 rec. en El Poblado", "price": 2350000, "type": "venta",
             "zone": "El Poblado", "m2": 78.5, "bedrooms": 2, "bathrooms": 2,
             "amenities": ["alberca", "gimnasio"], "status": "disponible"},
            {"id": "prop_00233", "title": "Casa 3 rec. en Usaquen", "price": 4800000, "type": "venta",
             "zone": "Usaquen", "m2": 180, "bedrooms": 3, "bathrooms": 2.5,
             "amenities": ["jardín", "cochera"], "status": "disponible"},
        ],
        "unavailable": [{"id": "prop_00234", "title": "Casa 4 rec. en Usaquen", "status": "apartado"}],
    },
}
PROPERTY_RESULT = {
    "get_property": {"id": "prop_00231", "title": "Depto 2 rec. en El Poblado", "price": 2350000, "m2": 78.5,
                     "bedrooms": 2, "bathrooms": 2, "amenities": ["alberca", "gimnasio"], "status": "disponible",
                     "address_approx": "El Poblado, cerca de Av. Vallarta"},
}
REQUIREMENTS_RESULT = {
    "check_requirements": {"meets_requirements": False,
                           "details": {"down_payment_ok": False, "income_ok": True, "references_ok": True,
                                       "notes": "Se requiere un enganche de al menos 10% del precio."}},
}
PROMOTIONS_RESULT = {
    "get_promotions": {"promotions": [{"id": "promo_001", "property_id": "prop_00231", "title": "Enganche diferido",
                                       "description": "Enganche en 6 mensualidades."}]},
}
EMPTY_SEARCH = {"search_properties": {"properties": [], "unavailable": []}}

# (id, respuesta candidata, tool_results de este turno, lo que dijo el lead, ¿debe pasar?)
CASES = [
    # --- respuestas correctas: solo repiten lo que las tools devolvieron -----------------------------
    ("precio_correcto", "Ese departamento cuesta $2,350,000.", SEARCH_RESULT, "", True),
    ("precio_con_formato_distinto", "Cuesta 2,350,000 pesos.", SEARCH_RESULT, "", True),
    ("m2_correcto", "Tiene 78.5 m² de construcción.", SEARCH_RESULT, "", True),
    ("recamaras_correcto", "Es de 2 recámaras y 2 baños.", SEARCH_RESULT, "", True),
    ("varias_propiedades_correctas", "Tengo una en $2,350,000 y otra en $4,800,000.", SEARCH_RESULT, "", True),
    ("conteo_de_resultados", "Encontré 2 opciones para ti.", SEARCH_RESULT, "", True),
    ("estado_disponible_correcto", "Sí, esa sigue disponible.", SEARCH_RESULT, "", True),
    ("estado_apartado_correcto", "Esa ya está apartada, te muestro otras.", SEARCH_RESULT, "", True),
    ("ficha_precio_correcto", "La ficha completa: $2,350,000, 78.5 m², 2 recámaras.", PROPERTY_RESULT, "", True),
    ("enganche_de_una_nota", "Necesitas un enganche de al menos 10% del precio.", REQUIREMENTS_RESULT, "", True),
    ("promocion_real", "Tiene una promoción: enganche diferido en 6 mensualidades.", PROMOTIONS_RESULT, "", True),
    ("sin_numeros_ni_estados", "¿Buscas para comprar o rentar?", SEARCH_RESULT, "", True),
    ("sin_resultados_sin_numeros", "Por ahora no tengo nada que coincida con lo que buscas.", EMPTY_SEARCH, "", True),
    ("repite_presupuesto_del_lead", "Con $3,000,000 tienes buenas opciones en Usaquen.", EMPTY_SEARCH,
     "mi presupuesto es de 3,000,000", True),
    ("repite_recamaras_pedidas", "Busco departamentos de 3 recámaras como pediste.", EMPTY_SEARCH,
     "quiero uno de 3 recámaras", True),
    ("precio_de_la_otra_propiedad", "La casa de Usaquen está en $4,800,000.", SEARCH_RESULT, "", True),
    ("bathrooms_decimal_correcto", "Tiene 2.5 baños.", SEARCH_RESULT, "", True),
    # Límite conocido: "4" es real (título de la apartada), pero el guardrail no distingue DE QUÉ
    # propiedad habla el texto — solo que el número existe en algo que las tools devolvieron este
    # turno. Documentado a propósito, no es un bug: ver DT-23.
    ("numero_real_de_otra_propiedad_del_turno", "Esa tiene 4 recámaras.", SEARCH_RESULT, "", True),
    ("estado_sin_verificar_correcto", "No pude confirmar la disponibilidad de esa, mejor te muestro otras.",
     {"search_properties": {"properties": [], "unavailable": [{"id": "x", "status": "sin_verificar"}]}}, "", True),

    # --- respuestas inventadas: no deben pasar --------------------------------------------------------
    ("precio_inventado", "Ese departamento cuesta $1,000,000.", SEARCH_RESULT, "", False),
    ("precio_redondeado_de_mas", "Cuesta como $2,400,000.", SEARCH_RESULT, "", False),
    ("m2_inventado", "Tiene 90 m² de construcción.", SEARCH_RESULT, "", False),
    ("recamaras_inventadas", "Es de 6 recámaras.", SEARCH_RESULT, "", False),
    ("conteo_inventado", "Tengo 5 opciones para ti.", SEARCH_RESULT, "", False),
    ("estado_disponible_falso", "Sí, esa sigue disponible.", {"search_properties": {"properties": [],
     "unavailable": [{"id": "prop_00234", "status": "apartado"}]}}, "", False),
    ("estado_cerrado_sin_respaldo", "Esa ya está cerrada.", SEARCH_RESULT, "", False),
    ("descuento_inventado", "Te puedo garantizar un 20% de descuento.", SEARCH_RESULT, "", False),
    ("descuento_inventado_en_promocion", "La promoción es de 15% de descuento.", PROMOTIONS_RESULT, "", False),
    ("enganche_inventado", "Necesitas un enganche de al menos 25% del precio.", REQUIREMENTS_RESULT, "", False),
    ("precio_sin_ninguna_tool", "Ese tipo de casas cuesta $5,500,000 normalmente.", EMPTY_SEARCH, "", False),
    ("direccion_con_numero_inventado", "Está en Av. Vallarta 4500.", SEARCH_RESULT, "", False),
    ("mezcla_precio_real_y_m2_inventado", "Cuesta $2,350,000 y tiene 120 m².", SEARCH_RESULT, "", False),
    ("presupuesto_del_lead_alterado", "Con $4,000,000 tienes buenas opciones.", EMPTY_SEARCH,
     "mi presupuesto es de 3,000,000", False),
    ("ficha_precio_inventado", "Esa propiedad cuesta $3,000,000.", PROPERTY_RESULT, "", False),
    ("urgencia_falsa_con_numero", "Solo queda 1 disponible, apúrate.", SEARCH_RESULT, "", False),
]

assert len(CASES) >= 30, "el gold set de A-5 tiene que tener al menos 30 casos (criterio de aceptación)"


def _run_case(case):
    case_id, reply, tool_results, lead_text, should_pass = case
    violations = check(reply, tool_results, lead_text)
    return case_id, should_pass, violations


def test_gold_set_ids_are_unique():
    ids = [case[0] for case in CASES]
    assert len(ids) == len(set(ids))


def test_gold_set_is_100_percent_green():
    """El *gate* de CI: si un solo caso falla, se ve exactamente cuál."""
    failures = []
    for case in CASES:
        case_id, should_pass, violations = _run_case(case)
        passed = not violations
        if passed != should_pass:
            failures.append(f"{case_id}: se esperaba {'sin violaciones' if should_pass else 'con violaciones'}, "
                            f"violaciones detectadas = {violations}")
    assert not failures, "\n".join(failures)
