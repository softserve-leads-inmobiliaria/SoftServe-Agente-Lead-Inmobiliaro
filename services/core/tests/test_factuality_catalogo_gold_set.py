"""C-7: gold set de factualidad sobre el catálogo de demo, *gate* de CI (100% en verde).

El gold set de A-5 (`test_factuality_gold_set.py`) congela dicts escritos a mano. Cuando el catálogo
migró a Postgres (C-1) y se sembró el inventario de demo (C-9), esos dicts quedaron desfasados —sus
`search_properties` no tenían `address_approx`, sus `get_property` no tenían `type`/`zone`/`address_exact`,
sus `get_promotions` no tenían `valid_until`— y nada lo señalaba. Este gold set usa las salidas **reales**
del catálogo de demo: los `tool_results` los produce `demo_catalogo.payloads_del_demo()` corriendo
`CatalogService` sobre el Postgres migrado, con el inventario de `scripts/seed_catalog.py`. Si el
catálogo cambia, estas fixtures cambian con él.

Cada caso es una respuesta candidata del agente, el turno del catálogo que respalda esa respuesta, lo
que dijo el lead (cuando aplica) y si la respuesta debería pasar el guardrail o no. No llama a ningún
LLM: prueba `factuality.check()` directamente, así que corre sin credenciales.

Al agregar un caso nuevo (un giro de redacción real que rompió el guardrail, una propiedad más del
inventario), se agrega aquí y en `demo_catalogo.TURNOS` si necesita un turno nuevo, nunca se borra uno
viejo salvo que deje de aplicar.
"""
import json

import pytest
from demo_catalogo import CHECKED_AT, TURNOS
from jsonschema import Draft202012Validator, FormatChecker

from app.factuality import check
from db.repo_root import repo_root

CONTRACTS = repo_root() / "contracts" / "mcp"


def _output_schema(tool: str) -> dict:
    return json.loads((CONTRACTS / f"{tool}.schema.json").read_text(encoding="utf-8"))["outputSchema"]

# --- carga del catálogo real (una vez por módulo) --------------------------------------------------
# `database_url` es la base temporal migrada que monta `conftest.py`. El subproceso del generador
# siembra el inventario de C-9 en ella y devuelve las salidas reales de las tools.
#


@pytest.fixture(scope="module")
def demo(database_url):
    from demo_catalogo import demo_del_catalogo

    return demo_del_catalogo(database_url)


@pytest.fixture(scope="module")
def payloads(demo):
    """`last_tool_results` de cada turno: la salida del catálogo ya pasada por A-4."""
    return demo.con_a4


@pytest.fixture(scope="module")
def crudo(demo):
    """La salida del catálogo sin A-4, que es la que se valida contra `contracts/mcp/`."""
    return demo.crudo


# --- los turnos del catálogo que este gold set usa -------------------------------------------------
# Se referencian por nombre de `demo_catalogo.TURNOS` para que quede explícito de dónde sale cada
# `tool_results`. Un turno que no esté en esta lista no debería aparecer en ningún caso.
#
# (id, respuesta candidata, turno del catálogo, lo que dijo el lead, ¿debe pasar?)
CASOS = [
    # --- respuestas correctas: solo repiten lo que devolvieron las tools del turno ---------------------    # Búsqueda de venta en Providencia: prop_00231 ($2,350,000) y prop_00232 ($1,650,000), ambas
    # disponibles; prop_00240 (estudio, renta) también sale por zona.
    ("precio_depto_providencia_correcto", "Ese departamento cuesta $2,350,000.",
     "busqueda_venta_providencia", "", True),
    ("precio_depto_1rec_correcto", "El otro sale en $1,650,000.",
     "busqueda_venta_providencia", "", True),
    ("m2_depto_correcto", "Tiene 78.5 m² de construcción.",
     "busqueda_venta_providencia", "", True),
    ("recamaras_depto_correctas", "Es de 2 recámaras y 2 baños.",
     "busqueda_venta_providencia", "", True),
    ("dos_propiedades_precios_correctos", "Uno en $2,350,000 y otro en $1,650,000.",
     "busqueda_venta_providencia", "", True),
    # Rental: prop_00235 ($18,500) y prop_00240 ($9,500) disponibles; prop_00236 ($12,000) va a
    # `unavailable` por estar apartado.
    ("renta_precio_correcto", "El depto de Chapalita en renta cuesta $18,500.",
     "busqueda_renta", "", True),
    ("renta_precio_estudio_correcto", "El estudio sale en $9,500.",
     "busqueda_renta", "", True),
    ("renta_m2_correcto", "El depto tiene 70 m².",
     "busqueda_renta", "", True),
    ("renta_tipo_correcto", "Ese es en renta, no en venta.",
     "busqueda_renta", "", True),
    # Preventa: prop_00237 ($3,900,000) y prop_00238 ($8,500,000), ambas disponibles.
    ("preventa_precio_correcto", "El depto en preventa cuesta $3,900,000.",
     "busqueda_preventa", "", True),
    ("preventa_penthouse_correcto", "El penthouse cuesta $8,500,000.",
     "busqueda_preventa", "", True),
    # Sin recámaras (C-9): estudio, terreno y local, los tres con `bedrooms: 0` real.
    ("terreno_m2_correcto", "El terreno tiene 620 m².",
     "busqueda_sin_recamaras", "", True),
    ("estudio_cero_recamaras_real", "El estudio no tiene recámaras, es un estudio.",
     "busqueda_sin_recamaras", "", True),
    ("terreno_precio_correcto", "El terreno cuesta $2,100,000.",
     "busqueda_sin_recamaras", "", True),
    # Ficha completa (get_property).
    ("ficha_precio_correcto", "La ficha completa: $2,350,000, 78.5 m², 2 recámaras.",
     "ficha_venta", "", True),
    ("ficha_terreno_banos_cero", "El terreno no tiene baños.",
     "ficha_terreno_calificado", "", True),
    ("ficha_amenidades_correctas", "Tiene alberca y gimnasio.",
     "ficha_venta", "", True),
    # Requisitos: el `notes` que devuelve la tool da el número que se puede citar.
    ("enganche_venta_10_correcto", "Necesitas un enganche de al menos 10% del precio.",
     "requisitos_venta_no_cumple", "", True),
    ("enganche_preventa_20_correcto", "Para la preventa es un enganche de al menos 20%.",
     "requisitos_preventa_no_cumple", "", True),
    ("ingreso_renta_3x_correcto", "Se requiere un ingreso de al menos 3 veces la renta.",
     "requisitos_renta_no_cumple", "", True),
    ("referencias_requeridas_correcto", "Se requieren referencias.",
     "requisitos_renta_no_cumple", "", True),
    # Promociones vigentes.
    ("promocion_enganche_diferido", "Tiene la promoción de enganche diferido.",
     "promociones_vigentes", "", True),
    ("promocion_mensualidades_correcta", "El enganche diferido es en 6 mensualidades.",
     "promociones_vigentes", "", True),
    # Respuestas sin números, que siempre pasan.
    ("pregunta_sin_cifras", "¿Buscas para comprar o rentar?",
     "busqueda_sin_filtros", "", True),
    ("sin_coincidencia_sin_cifras", "No tengo nada que coincida con lo que buscas.",
     "busqueda_zona_sin_coincidencia", "", True),
    # Números que dijo el propio lead se pueden repetir.
    ("repite_presupuesto_del_lead", "Con $3,000,000 tienes buenas opciones.",
     "busqueda_zona_sin_coincidencia", "mi presupuesto es de 3,000,000", True),
    # Estado real reportado por A-4: prop_00236 está `apartado` y aparece en `unavailable` con su
    # estado real, así que decir que está apartada sí es verificable.
    ("estado_apartado_real", "El depto de Chapalita de 1 recámara ya está apartado.",
     "busqueda_renta", "", True),

    # --- los casos borde del resto del inventario (C-9) ---------------------------------------------
    # Búsqueda por presupuesto de venta (2M-5M): prop_00231, prop_00233, prop_00237 y prop_00241 se
    # ofrecen; prop_00239 (cerrada) se aparta. Ojo que en este turno `2.5` sí está autorizado (los
    # 2.5 baños de la casa de Zapopan), así que un caso de m² o baños inventados tiene que usar otro.
    ("presupuesto_venta_casa_zapopan", "La casa de Zapopan cuesta $4,800,000.",
     "busqueda_presupuesto_venta", "", True),
    ("presupuesto_venta_cerrada_real", "La de Tlaquepaque ya está cerrada, no la ofrezco.",
     "busqueda_presupuesto_venta", "", True),
    # Búsqueda por presupuesto de renta (9,000-20,000): prop_00235 y prop_00240.
    ("presupuesto_renta_precio_correcto", "Con ese presupuesto, el depto de Chapalita sale en $18,500.",
     "busqueda_presupuesto_renta", "", True),
    # Una recámara: solo prop_00232 se ofrece; prop_00236 (también de 1 recámara) está apartada.
    ("una_recamara_precio_correcto", "El depto de una recámara cuesta $1,650,000.",
     "busqueda_una_recamara", "", True),
    # Zapopan: prop_00233 y prop_00241 disponibles, prop_00234 (la casa de 4 recámaras) apartada.
    ("zapopan_terreno_y_casa", "En Zapopan tengo el terreno de $2,100,000 y la casa de $4,800,000.",
     "busqueda_zapopan", "", True),
    # Fichas por tipo de operación.
    ("ficha_renta_precio_correcto", "Este depto en renta está en $18,500.",
     "ficha_renta", "", True),
    ("ficha_preventa_precio_correcto", "El depto en preventa cuesta $3,900,000.",
     "ficha_preventa", "", True),
    ("ficha_cerrada_precio_correcto", "La casa de Tlaquepaque está en $2,900,000 pero ya está cerrada.",
     "ficha_cerrada", "", True),
    # Disponibilidad consultada directamente: el estado es lo único que la tool devuelve.
    ("disponibilidad_confirma_apartado", "Confirmo: está apartado.",
     "disponibilidad_apartada", "", True),
    ("disponibilidad_confirma_cerrada", "Confirmo: está cerrada.",
     "disponibilidad_cerrada", "", True),
    ("disponibilidad_confirma_disponible", "Confirmo: sigue disponible.",
     "disponibilidad_disponible", "", True),
    # Requisitos cumplidos: el `notes` es "Cumple los requisitos.", sin un solo número. Eso deja el
    # turno sin números autorizados, así que cualquier cifra en la respuesta se bloquea salvo que la
    # haya dicho el propio lead (los dos casos de abajo).
    ("requisitos_cumplidos_confirmados", "Cumples los requisitos.",
     "requisitos_venta_cumple", "", True),
    ("requisitos_renta_cumplidos", "Cumples los requisitos para rentar.",
     "requisitos_renta_cumple", "", True),
    ("cifra_sin_respaldo_cuando_cumples", "Cumples los requisitos y puedes apartar en $500,000.",
     "requisitos_venta_cumple", "", False),
    ("cifra_del_propio_lead_cuando_cumple", "Cumples los requisitos y tu enganche de $500,000 alcanza.",
     "requisitos_venta_cumple", "tengo 500,000 de enganche", True),
    # La promoción vencida (C-9): prop_00233 tiene `promo_003` en la base, pero `get_promotions` filtra
    # por vigencia (`service.py:163`), así que para esa casa el turno no trae ninguna promoción. El
    # único número autorizado es el 0 del conteo de la lista vacía.
    ("propiedad_sin_promociones_vigentes", "Esa casa de Zapopan no tiene promociones vigentes.",
     "promociones_de_propiedad", "", True),
    ("conteo_cero_promociones", "Esa propiedad tiene 0 promociones vigentes.",
     "promociones_de_propiedad", "", True),
    ("promocion_vencida_no_se_cita", "Esa casa tiene la promoción de verano, que acaba en 2000.",
     "promociones_de_propiedad", "", False),
    ("promocion_inventada_en_casa_sin_promos", "Esa casa tiene una promoción de descuento del 15%.",
     "promociones_de_propiedad", "", False),

    # --- respuestas inventadas: no deben pasar -----------------------------------------------------
    # Precios que no están en el turno.
    ("precio_inventado_venta", "Ese departamento cuesta $1,000,000.",
     "busqueda_venta_providencia", "", False),
    ("precio_redondeado_de_mas", "Cuesta como $2,400,000.",
     "busqueda_venta_providencia", "", False),
    ("renta_precio_inventado", "El depto en renta cuesta $15,000.",
     "busqueda_renta", "", False),
    ("preventa_precio_inventado", "El depto en preventa cuesta $3,000,000.",
     "busqueda_preventa", "", False),
    ("terreno_precio_inventado", "El terreno cuesta $1,500,000.",
     "busqueda_sin_recamaras", "", False),
    # Características que no están en el turno.
    ("m2_inventado", "Tiene 120 m² de construcción.",
     "busqueda_venta_providencia", "", False),
    ("recamaras_inventadas", "Es de 5 recámaras.",
     "busqueda_venta_providencia", "", False),
    # En `busqueda_venta_providencia` hay 3 propiedades, y A-5 permite decir "tengo 3 opciones"
    # (conteo de `properties`), así que 3 no serviría como número inventado: 4 sí.
    ("banos_inventados", "Tiene 4 baños.",
     "busqueda_venta_providencia", "", False),
    # El estudio es `bedrooms: 0` real (C-9), pero el turno autoriza 0, 1, 2 y 3 —los bathrooms de las
    # tres propiedades y el conteo del listado—, así que un caso sobre "cuántas recámaras tiene" aquí
    # solo puede usar un número fuera de esos cuatro. Es el mismo límite de DT-23 que documenta
    # `numero_real_de_otra_propiedad_del_turno` en el gold set de A-5, y los casos de abajo lo fijan.
    ("estudio_con_recamaras_inventadas", "El estudio tiene 4 recámaras.",
     "busqueda_sin_recamaras", "", False),
    # Estados que no están respaldados.
    ("disponible_falso_para_apartada", "Sí, ese de $12,000 sigue disponible.",
     "busqueda_renta", "", False),
    ("disponible_falso_para_cerrada", "Esa de $2,900,000 sigue disponible.",
     "busqueda_sin_filtros", "", False),
    # Requisitos inventados: el enganche de preventa es 20%, decir 10% no está respaldado.
    ("enganche_inventado", "Necesitas un enganche de al menos 25% del precio.",
     "requisitos_venta_no_cumple", "", False),
    ("enganche_preventa_subestimado", "Necesitas un enganche de al menos 10%.",
     "requisitos_preventa_no_cumple", "", False),
    # Promociones: la de prop_00235 no existe (promociones: []), no se puede inventar una.
    ("promocion_inexistente", "Ese tiene una promoción de 15% de descuento.",
     "promociones_sin_promociones", "", False),
    # Estados que no están respaldados (el guardrail también valida estados, no solo números).
    ("estado_inventado_cerrada", "Ese departamento está cerrado.",
     "busqueda_venta_providencia", "", False),
    ("estado_inventado_apartada_en_renta", "Ese depto en renta está apartado.",
     "ficha_renta", "", False),
    ("estado_inventado_cerrada_en_preventa", "El depto en preventa ya está cerrado.",
     "ficha_preventa", "", False),
    ("estado_cerrada_sin_respaldo_en_promociones", "Esa propiedad está cerrada.",
     "promociones_vigentes", "", False),
    ("estado_inventado_cerrada_sin_requisitos", "Esa propiedad está cerrada y no cumple requisitos.",
     "requisitos_venta_cumple", "", False),
    ("apartada_cuando_es_disponible", "Esa propiedad está apartada.",
     "disponibilidad_disponible", "", False),
    ("disponible_cuando_es_apartada", "Esa propiedad está disponible.",
     "disponibilidad_apartada", "", False),
    ("disponible_cuando_es_cerrada", "Esa propiedad está disponible.",
     "disponibilidad_cerrada", "", False),
    # Mezcla un precio real con una característica inventada.
    ("mezcla_precio_real_m2_inventado", "Cuesta $2,350,000 y tiene 120 m².",
     "busqueda_venta_providencia", "", False),
    # El presupuesto del lead, alterado.
    ("presupuesto_del_lead_alterado", "Con $4,000,000 tienes buenas opciones.",
     "busqueda_zona_sin_coincidencia", "mi presupuesto es de 3,000,000", False),
    # A-4 mueve las no disponibles a `unavailable` guardando SOLO `{id, title, status}`
    # (`availability.py:61`): el precio se descarta a propósito, porque el agente no debe cotizar una
    # propiedad que no puede ofrecer. Decir el precio de la apartada es por lo tanto inventar, aunque
    # sea el precio real de prop_00236 — y así se fija, para que nadie lea el caso como un bug.
    ("precio_de_propiedad_apartada", "El depto de $12,000 está apartado.",
     "busqueda_renta", "", False),

    # --- límites conocidos, fijados a propósito ----------------------------------------------------
    # DT-23: el guardrail no sabe de QUÉ propiedad habla el texto, solo si el número existe en algo
    # que las tools devolvieron este turno. En el inventario real esto es la commonplace, porque una
    # búsqueda devuelve varias: el terreno cuesta $2,100,000 y el estudio del mismo turno $9,500, así
    # que atribuirle al terreno el precio del estudio no se detecta. Documentado, no es un bug.
    ("precio_de_otra_propiedad_del_turno", "El terreno cuesta $9,500.",
     "busqueda_sin_recamaras", "", True),
    # El conteo de resultados sí se puede citar (A-5, `_COUNTABLE_LISTS`): el turno trae 3.
    ("conteo_de_resultados_del_turno", "Encontré 3 opciones sin recámaras.",
     "busqueda_sin_recamaras", "", True),
    # `get_property` con lead calificado devuelve `address_exact` = "Calle Lerdo 1204", y
    # `factuality._walk` extrae números de cualquier string del payload: el 1204 de la calle entra al
    # set de permitidos. El mismo número, con un lead NO calificado, sí se bloquea —los dos casos de
    # abajo son el mismo texto y el mismo número, y el veredicto depende de `lead_qualified`, que
    # A-5 nunca ve. No es una fuga de la dirección (A-6 la bloquea por su cuenta en los dos casos,
    # es DT-23); lo que queda libre es el número suelto.
    ("numero_de_calle_con_lead_calificado", "Está en Lerdo 1204.",
     "ficha_venta_calificado", "", True),
    ("numero_de_calle_sin_calificar", "Está en Lerdo 1204.",
     "ficha_venta", "", False),
]

assert len(CASOS) >= 30, "el gold set de C-7 tiene que tener al menos 30 casos (criterio de aceptación)"

# Turnos referenciados por los casos que deben existir en el catálogo de demo.
_TURNOS_USADOS = {caso[2] for caso in CASOS}
assert _TURNOS_USADOS <= set(TURNOS), f"casos que referencian un turno inexistente: {_TURNOS_USADOS - set(TURNOS)}"


# --- gate: el 100% en verde, con el nombre exacto del caso que falle -------------------------------
def test_gold_set_ids_are_unique():
    ids = [caso[0] for caso in CASOS]
    assert len(ids) == len(set(ids))


def test_gold_set_is_100_percent_green(payloads):
    """El *gate* de CI: si un solo caso falla, se ve exactamente cuál."""
    failures = []
    for caso in CASOS:
        caso_id, reply, turno, lead_text, should_pass = caso
        violations = check(reply, payloads[turno], lead_text)
        passed = not violations
        if passed != should_pass:
            failures.append(
                f"{caso_id}: se esperaba {'sin violaciones' if should_pass else 'con violaciones'}, "
                f"violaciones detectadas = {[(v.kind, v.value) for v in violations]}")
    assert not failures, "\n".join(failures)


# --- el contrato: la salida cruda del catálogo tiene que cumplirlo --------------------------------
def test_la_salida_cruda_del_catalogo_cumple_el_contrato(crudo):
    """Cada turno que el catálogo puede responder valida contra `contracts/mcp/`.

    Este es el test que C-7 agrega sobre A-5: los dicts escritos a mano del gold set de A-5 nunca se
    contrastaron con los contratos, así que cuando C-1 los dejó desfasados (faltaba `address_approx`,
    `type`, `zone`, `address_exact` y `valid_until`) nada lo señalaba. Aquí la salida real de
    `CatalogService` se valida contra el `outputSchema` de cada tool, así que un campo que el catálogo
    deje de devolver, o que devuelva con otro tipo, rompe el CI.
    """
    failures = []
    for nombre, (tool, _args) in TURNOS.items():
        schema = _output_schema(tool)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        payload = crudo[nombre][tool]
        errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(payload)]
        if errors:
            failures.append(f"{nombre} ({tool}): " + "; ".join(errors))
    assert not failures, "\n".join(failures)


# --- la capa de A-4: la forma post-revalidación queda fijada --------------------------------------
def test_a4_solo_agrega_esos_dos_campos_y_no_toca_el_resto(demo, payloads, crudo):
    """Lo que A-4 (`AvailabilityGuard`) le hace al payload del catálogo antes de que A-5 lo vea.

    Esto no reimplementa A-4: la capa la produjo el guardrail real (`demo_catalogo._pasar_por_a4`), así
    que si `availability.py` cambiara la forma del payload, este test se cae con ella.

    El `outputSchema` del catálogo tiene `additionalProperties: false` y no contempla la
    revalidación, así que la forma post-A-4 no se puede validar contra el contrato: se fija aquí de
    forma explícita. Un `search_properties` post-A-4 es el mismo objeto más `availability_checked_at`
    en cada propiedad ofrecida y una lista `unavailable` al nivel superior; un `get_property` es el
    mismo objeto más `availability_checked_at`; las otras tres tools pasan intactas.
    """
    for nombre, (tool, _args) in TURNOS.items():
        antes, despues = crudo[nombre][tool], payloads[nombre][tool]

        if tool == "search_properties":
            originales = {p["id"]: p for p in antes["properties"]}
            for p in despues["properties"]:
                original = originales[p["id"]]
                # Lo que A-4 agrega a una propiedad es solo el timestamp de la revalidación; el resto
                # del objeto tiene que ser el mismo, campo por campo.
                assert set(p) - set(original) == {"availability_checked_at"}, (
                    f"{nombre}: A-4 agregó campos inesperados a {p['id']}: {set(p) - set(original)}")
                assert p["status"] == "disponible", f"{nombre}: {p['id']} se ofreció sin revalidar"
                # El estado y el timestamp tienen que ser los de `get_availability`, no los del listado:
                # de eso se trata la revalidación, y si se usara el del listado el test no lo notaría.
                assert (p["status"], p["availability_checked_at"]) == (
                    demo.disponibilidad[p["id"]]["status"], CHECKED_AT), (
                    f"{nombre}: {p['id']} no se revalidó contra get_availability")
                for campo, valor in original.items():
                    assert p[campo] == valor, f"{nombre}: A-4 cambió {campo} de {p['id']}"
            # Y lo que se movió a `unavailable` conserva su estado real: de eso vive el "esa ya está
            # apartada" que el guardrail sí puede dejar pasar.
            for u in despues["unavailable"]:
                assert u["status"] == demo.disponibilidad[u["id"]]["status"], (
                    f"{nombre}: A-4 no usó el estado vigente de {u['id']}")
                assert u["status"] == originales[u["id"]]["status"], (
                    f"{nombre}: A-4 cambió el estado de {u['id']} al moverlo a unavailable")
                assert u == {"id": u["id"], "title": originales[u["id"]]["title"], "status": u["status"]}, (
                    f"{nombre}: `unavailable` solo debe llevar id, title y status; lleva {sorted(u)}")
        elif tool == "get_property":
            assert set(despues) - set(antes) == {"availability_checked_at"}, (
                f"{nombre}: A-4 agregó campos inesperados: {set(despues) - set(antes)}")
            assert (despues["status"], despues["availability_checked_at"]) == (
                demo.disponibilidad[antes["id"]]["status"], CHECKED_AT), (
                f"{nombre}: la ficha no se revalidó contra get_availability")
        else:
            # get_availability, get_promotions y check_requirements no pasan por la revalidación.
            assert despues == antes, f"{nombre}: A-4 no debería modificar la salida de {tool}"
