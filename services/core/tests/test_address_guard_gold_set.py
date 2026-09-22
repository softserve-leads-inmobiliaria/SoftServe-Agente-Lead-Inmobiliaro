"""A-6: gold set de dirección reservada, *gate* de CI (0 fugas — ver A-06.md).

Cada caso es una respuesta candidata del agente, si el lead está calificado, lo que el lead mismo
escribió (cuando aplica), y si esa respuesta debería pasar el guardrail o no. No llama a ningún LLM:
prueba `address_guard.check()` directamente, así corre sin credenciales y en milisegundos — mismo
criterio que el gold set de A-5 (`test_factuality_gold_set.py`).

Al agregar un caso nuevo, se agrega aquí, nunca se borra uno viejo salvo que deje de aplicar.
"""
from app.address_guard import check

# (id, respuesta candidata, lead_qualified, lo que dijo el lead, ¿debe pasar?)
CASES = [
    # --- lead calificado: la dirección exacta ya es suya para ver -------------------------------------
    ("calificado_ve_direccion_real", "Está en Calle Lerdo 1204, Providencia.", True, "", True),
    ("calificado_ve_blvd_con_sufijo", "Es el Blvd. Puerta de Hierro 4965 PH.", True, "", True),

    # --- sin calificar, pero no es una dirección exacta --------------------------------------------
    ("approx_sin_numero_no_dispara", "Está cerca de Av. Vallarta, en Providencia.", False, "", True),
    ("solo_zona_no_dispara", "Está en Providencia, muy cerca del centro.", False, "", True),
    ("sin_ninguna_calle", "Tiene 2 recámaras y cuesta $2,350,000.", False, "", True),
    ("zona_comercial_sin_numero", "Es zona comercial, cerca de Glorieta Chapalita.", False, "", True),
    # El número existe, pero es una distancia, no una dirección (el único falso positivo real de
    # este patrón tan simple — de ahí la salvedad por palabra de unidad).
    ("distancia_no_es_direccion", "Circuito Los Pinos está a 5 minutos de la entrada.", False, "", True),
    ("distancia_en_cuadras_no_es_direccion", "Queda a 10 cuadras de Calle Independencia.", False, "", True),
    # El lead ya dio su propia dirección: repetírsela no es una fuga de la propiedad (mismo criterio
    # que A-5 con los números del lead).
    ("direccion_del_lead_no_es_fuga", "Veo que vives en Av. Independencia 200, busco algo cerca de ahí.",
     False, "vivo en Av. Independencia 200", True),

    # --- sin calificar y sí es una dirección exacta: debe bloquearse -------------------------------
    ("direccion_real_sin_calificar", "Está en Calle Lerdo 1204, Providencia.", False, "", False),
    ("avenida_abreviada", "Se ubica en Av. Guadalupe 501, Chapalita.", False, "", False),
    ("avenida_completa", "Se ubica en Avenida Guadalupe 501, Chapalita.", False, "", False),
    ("blvd_con_sufijo_sin_calificar", "Es el Blvd. Puerta de Hierro 4965 PH.", False, "", False),
    ("circuito_sin_calificar", "Está en Circuito Los Pinos 45, Zapopan.", False, "", False),
    ("minuscula_sigue_disparando", "queda en av. juárez 45, cerca del centro", False, "", False),
    ("direccion_al_inicio_de_la_oracion", "Calle Sorolla 88 es la dirección exacta.", False, "", False),
    # El número "88" también aparece como m² en los resultados reales de este turno: A-5 lo dejaría
    # pasar por estar "respaldado" por una tool, pero sigue siendo una dirección — A-6 no depende de
    # qué permitió A-5 este turno (DT-23).
    ("numero_coincide_con_un_dato_real_del_turno", "Tiene 88 m² y está en Calle Sorolla 88, Providencia.",
     False, "", False),
    ("direccion_de_otra_propiedad_del_turno", "La otra opción está en Calle Gante 19, Providencia.",
     False, "", False),
]

assert len(CASES) >= 15, "el gold set de A-6 tiene que cubrir tanto casos que deben pasar como los que no"


def _run_case(case):
    case_id, reply, lead_qualified, lead_text, should_pass = case
    violations = check(reply, lead_qualified, lead_text)
    return case_id, should_pass, violations


def test_gold_set_ids_are_unique():
    ids = [case[0] for case in CASES]
    assert len(ids) == len(set(ids))


def test_gold_set_cero_fugas():
    """El *gate* de CI: si un solo caso falla, se ve exactamente cuál."""
    failures = []
    for case in CASES:
        case_id, should_pass, violations = _run_case(case)
        passed = not violations
        if passed != should_pass:
            failures.append(f"{case_id}: se esperaba {'sin violaciones' if should_pass else 'con violaciones'}, "
                            f"violaciones detectadas = {violations}")
    assert not failures, "\n".join(failures)
