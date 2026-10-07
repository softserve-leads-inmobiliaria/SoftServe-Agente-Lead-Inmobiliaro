"""#243 (DT-60): gold set de los marcadores sobre la salida real del catálogo de demo, *gate* de CI (100% en verde).

Mismas salidas que el gold set de C-7 (`test_factuality_catalogo_gold_set.py`): las produce
`demo_catalogo.demo_del_catalogo()` corriendo `CatalogService` sobre el Postgres migrado, ya pasadas por A-4. Cada
caso es una respuesta del modelo con marcadores, los turnos del catálogo que la respaldan, lo que dijo el lead y lo
que tiene que leer el lead (`None`: la respuesta se bloquea). No llama a ningún LLM.

Al agregar un caso nuevo (una redacción real que se vio en vivo), se agrega acá; nunca se borra uno viejo salvo que
deje de aplicar.
"""
import pytest

from app.markers import build, check, lead_numbers, render

# (id, respuesta con marcadores, turnos de `demo_catalogo.TURNOS`, lo que dijo el lead, lo que lee el lead o None)
CASOS = [
    # --- se completa: el valor sale de la tool, con el formato del código -------------------------------------
    ("precio", "It's {prop_00231.price}.", ("busqueda_venta_poblado",), "", "It's $185,000."),
    ("precio_con_signo", "It's ${prop_00231.price}.", ("busqueda_venta_poblado",), "", "It's $185,000."),
    ("m2_con_decimales", "It has {prop_00231.m2} m².", ("busqueda_venta_poblado",), "", "It has 78.5 m²."),
    ("recamaras_y_banos", "{prop_00231.bedrooms} bedrooms and {prop_00231.bathrooms} bathrooms.",
     ("busqueda_venta_poblado",), "", "2 bedrooms and 2 bathrooms."),
    ("dos_propiedades", "One at {prop_00231.price} and one at {prop_00232.price}.", ("busqueda_venta_poblado",), "",
     "One at $185,000 and one at $129,000."),
    ("tipo_y_operacion", "It's an {prop_00231.kind} {prop_00231.type}.", ("busqueda_venta_poblado",), "",
     "It's an apartment for sale."),
    ("estudio_en_renta", "A {prop_00240.kind} {prop_00240.type} at {prop_00240.price} a month.",
     ("busqueda_venta_poblado",), "", "A studio for rent at $600 a month."),
    ("estado_de_la_busqueda", "Yes, it's {prop_00231.status}.", ("busqueda_venta_poblado",), "", "Yes, it's available."),
    ("estado_de_una_no_disponible", "That one is {prop_00236.status}.", ("busqueda_renta",), "",
     "That one is reserved."),
    ("estado_de_get_availability", "It's {prop_00234.status}.", ("disponibilidad_apartada",), "", "It's reserved."),
    ("amenidades", "It has a {prop_00231.amenities}.", ("ficha_venta",), "", "It has a pool and gym."),
    ("direccion_aproximada", "It's in {prop_00231.address_approx}.", ("ficha_venta",), "",
     "It's in El Poblado, Medellin, near Parque Lleras."),
    ("direccion_exacta_calificado", "The address is {prop_00231.address_exact}.", ("ficha_venta_calificado",), "",
     "The address is Carrera 37 # 8A-24, El Poblado."),
    ("enganche", "The minimum down payment is {prop_00231.requirements.down_payment_percent}% "
     "({prop_00231.requirements.down_payment_min}).", ("ficha_venta",), "",
     "The minimum down payment is 10% ($18,500)."),
    ("ingreso_de_una_renta", "You need {prop_00235.requirements.monthly_income_min} a month, "
     "{prop_00235.requirements.income_multiple} times the rent.", ("ficha_renta",), "",
     "You need $3,450 a month, 3 times the rent."),
    ("cantidad_de_la_busqueda", "I found {search.count} rentals.", ("busqueda_renta",), "", "I found 3 rentals."),
    ("busqueda_vacia", "I found {search.count} in Cali.", ("busqueda_zona_sin_coincidencia",), "Cali?",
     "I found 0 in Cali."),
    ("cobertura", "We have properties in {zones}.", ("zonas",), "",
     "We have properties in Chapinero and Usaquen in Bogota; El Poblado, Envigado and Laureles in Medellin."),
    ("promocion", "{promo_001.title}: {promo_001.description} Valid until {promo_001.valid_until}.",
     ("promociones_vigentes",), "",
     "Deferred down payment: Down payment in 6 monthly installments. Valid until December 31, 2099."),
    ("dos_turnos", "{prop_00231.price} to buy, or {prop_00235.price} a month to rent.",
     ("ficha_venta", "ficha_renta"), "", "$185,000 to buy, or $1,150 a month to rent."),
    ("presupuesto_del_lead", "With your $200,000 budget, {prop_00231.title} at {prop_00231.price} fits.",
     ("busqueda_venta_poblado",), "My budget is $200,000",
     "With your $200,000 budget, 2-bedroom apartment in El Poblado at $185,000 fits."),
    ("lista_numerada", "1. {prop_00231.title}\n2. {prop_00232.title}", ("busqueda_venta_poblado",), "",
     "1. 2-bedroom apartment in El Poblado\n2. 1-bedroom apartment in El Poblado"),
    # El número del lead se puede repetir: que no se afirme como dato de la propiedad lo revisa #230 (factuality).
    ("recamaras_que_pidio_el_lead", "You asked for 2 bedrooms: {prop_00231.title}.", ("busqueda_venta_poblado",),
     "I need 2 bedrooms", "You asked for 2 bedrooms: 2-bedroom apartment in El Poblado."),

    # --- se bloquea --------------------------------------------------------------------------------------------
    # Escrito a mano, aunque sea el valor real: podría ser el de otra propiedad (punto 5 de la revisión).
    ("precio_escrito", "It's $185,000.", ("busqueda_venta_poblado",), "", None),
    ("precio_de_otra_propiedad", "The 2-bedroom one costs $129,000.", ("busqueda_venta_poblado",), "", None),
    ("precio_inventado", "It's $150,000.", ("busqueda_venta_poblado",), "", None),
    ("m2_escritos", "It has 78.5 m².", ("busqueda_venta_poblado",), "", None),
    ("cantidad_escrita", "I found 3 rentals.", ("busqueda_renta",), "", None),
    ("enganche_escrito", "The down payment is 10%.", ("ficha_venta",), "", None),
    ("propiedad_que_no_vino", "It's {prop_00999.price}.", ("busqueda_venta_poblado",), "", None),
    ("campo_que_no_existe", "The owner is {prop_00231.owner}.", ("ficha_venta",), "", None),
    ("direccion_exacta_sin_calificar", "The address is {prop_00231.address_exact}.", ("ficha_venta",), "", None),
    ("requisito_que_no_aplica", "You need {prop_00231.requirements.monthly_income_min} a month.", ("ficha_venta",),
     "", None),
    ("estado_sin_tool_del_turno", "It's {prop_00231.status}.", ("zonas",), "", None),
    ("cobertura_sin_list_zones", "We have properties in {zones}.", ("busqueda_venta_poblado",), "", None),
    ("referencia_inventada", "Take a look at prop_00999.", ("busqueda_venta_poblado",), "", None),
    ("promocion_que_no_vino", "{promo_003.title}", ("promociones_vigentes",), "", None),
]
assert len(CASOS) >= 30, "#243 pide al menos 30 casos"


@pytest.fixture(scope="module")
def payloads(database_url):
    from demo_catalogo import demo_del_catalogo

    return demo_del_catalogo(database_url).con_a4


def test_gold_set_ids_are_unique():
    ids = [caso[0] for caso in CASOS]
    assert len(ids) == len(set(ids))


def test_gold_set_is_100_percent_green(payloads):
    """El *gate* de CI: si un solo caso falla, se ve exactamente cuál."""
    failures = []
    for caso_id, reply, turnos, lead_text, expected in CASOS:
        sources = build([item for turno in turnos for item in payloads[turno].items()])
        violations = check(reply, sources, lead_numbers([lead_text]))
        if expected is None and not violations:
            failures.append(f"{caso_id}: se esperaba que se bloqueara, y pasó como {render(reply, sources)!r}")
        elif expected is not None and violations:
            failures.append(f"{caso_id}: se bloqueó por {[(v.kind, v.value) for v in violations]}")
        elif expected is not None and render(reply, sources) != expected:
            failures.append(f"{caso_id}: el lead leería {render(reply, sources)!r}, se esperaba {expected!r}")
    assert not failures, "\n".join(failures)
