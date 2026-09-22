"""A-5: unidad del guardrail de factualidad (`app/factuality.py`)."""
from app.factuality import Violation, check, extract_numbers, extract_statuses, literal_values


def test_extracts_a_price_with_thousand_separators():
    assert extract_numbers("Cuesta $2,350,000 en total") == {"2350000"}


def test_extracts_a_decimal_size():
    assert extract_numbers("Tiene 78.5 m²") == {"78.5"}


def test_does_not_extract_digits_embedded_in_an_id():
    assert extract_numbers("La propiedad prop_00231 te interesa") == set()


def test_normalizes_trailing_zeros_the_same_on_both_sides():
    assert extract_numbers("son 100 metros") == literal_values({"m2": 100.0})[0]


def test_extracts_every_grammatical_form_of_a_status_as_its_canonical_value():
    assert extract_statuses("Está apartada, no cerrada, y la otra sigue disponible") == {"apartado", "cerrado",
                                                                                          "disponible"}


def test_literal_values_reads_numbers_from_typed_fields_lists_and_prose():
    tool_results = {"search_properties": {"properties": [
        {"id": "prop_1", "price": 2350000, "m2": 78.5, "bedrooms": 2, "status": "disponible"},
    ]}}
    numbers, statuses = literal_values(tool_results)
    assert {"2350000", "78.5", "2"} <= numbers
    assert "1" in numbers  # el tamaño de la lista `properties`: "tengo 1 opción" no es un invento
    assert statuses == {"disponible"}


def test_literal_values_reads_numbers_embedded_in_a_notes_string():
    """`check_requirements` devuelve el % de enganche dentro de una nota, no como campo numérico."""
    tool_results = {"check_requirements": {"details": {"notes": "Se requiere un enganche de al menos 10%."}}}
    numbers, _ = literal_values(tool_results)
    assert "10" in numbers


def test_check_passes_a_reply_that_only_repeats_tool_data():
    tool_results = {"search_properties": {"properties": [{"price": 2350000, "m2": 78.5, "status": "disponible"}]}}
    reply = "Tengo un departamento de 78.5 m² en $2,350,000, disponible."
    assert check(reply, tool_results) == []


def test_check_flags_a_price_that_never_came_from_a_tool():
    tool_results = {"search_properties": {"properties": [{"price": 2350000}]}}
    violations = check("Cuesta $2,500,000", tool_results)
    assert violations == [Violation("number", "2500000")]


def test_check_flags_a_status_that_never_came_from_a_tool():
    """Ninguna propiedad de este turno se reportó `cerrado`: decirlo es un invento, no un hecho."""
    tool_results = {"search_properties": {"properties": [{"status": "disponible"}], "unavailable": []}}
    assert check("Esa propiedad ya está cerrada", tool_results) == [Violation("status", "cerrado")]


def test_check_allows_a_number_the_lead_said_even_if_no_tool_confirms_it():
    """Repetir el presupuesto del lead no es inventar un dato de una propiedad."""
    tool_results = {}
    assert check("Con $5,000,000 tienes buenas opciones.", tool_results, lead_text="mi presupuesto es 5,000,000") \
        == []


def test_check_reports_every_violation_not_just_the_first():
    violations = check("Cuesta $9,000,000 y ya está cerrada", {"search_properties": {"properties": []}})
    assert {(v.kind, v.value) for v in violations} == {("number", "9000000"), ("status", "cerrado")}


def test_a_property_that_the_search_moved_to_unavailable_still_counts_as_literal():
    """A-4 deja el estado real en `unavailable`: decir que está apartada, con eso, sí es verificable."""
    tool_results = {"search_properties": {"properties": [], "unavailable": [{"id": "a", "status": "apartado"}]}}
    assert check("Esa está apartada por ahora.", tool_results) == []
