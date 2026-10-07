"""DT-60 (#243): los marcadores que escribe el modelo y los valores que pone el código. Sin red ni base.

El gold set sobre las salidas reales del catálogo es `test_markers_gold_set.py`.
"""
import json

import pytest

from app import markers
from app.markers import build, check, lead_numbers, outside, render
from db.repo_root import repo_root

CASA = {"id": "p1", "title": "Casa en Usaquen", "price": 365000.0, "type": "venta", "kind": "casa", "zone": "Usaquen",
        "m2": 180.0, "bedrooms": 3, "bathrooms": 2.5, "amenities": ["garden", "garage"], "status": "disponible",
        "address_approx": "Usaquen, Bogota", "address_exact": None,
        "requirements": {"down_payment_percent": 20, "down_payment_min": 73000.0, "income_multiple": None,
                         "monthly_income_min": None, "references_required": False}}


def sources(*payloads, remembered=None):
    return build(list(payloads), remembered)


def test_each_field_is_written_the_way_the_lead_reads_it():
    s = sources(("get_property", CASA))
    text = ("{p1.title}: {p1.kind} {p1.type}, {p1.price}, {p1.m2} m², {p1.bedrooms} bedrooms, {p1.bathrooms} baths, "
            "{p1.amenities}, {p1.status}, {p1.zone}, {p1.requirements.down_payment_percent} down "
            "({p1.requirements.down_payment_min})")
    assert render(text, s) == ("Casa en Usaquen: house for sale, $365,000, 180 m², 3 bedrooms, 2.5 baths, garden and "
                               "garage, available, Usaquen, 20% down ($73,000)")
    assert check(text, s, set()) == []


@pytest.mark.parametrize("value, expected", [(185000.0, "$185,000"), (1150.5, "$1,150.50"), (600, "$600"),
                                             (2350000, "$2,350,000")])
def test_money_has_the_dollar_sign_and_thousands_separators(value, expected):
    assert render("{p1.price}", sources(("get_property", {**CASA, "price": value}))) == expected


def test_a_dollar_or_percent_sign_the_model_added_is_not_doubled():
    s = sources(("get_property", CASA))
    assert render("${p1.price}, $ {p1.price}, {p1.requirements.down_payment_percent}%", s) == "$365,000, $365,000, 20%"


def test_the_requirements_can_be_named_without_their_prefix():
    """Así los enumera el prompt; la sugerencia de la corrección usa el mismo nombre corto."""
    s = sources(("get_property", CASA))
    assert render("{p1.down_payment_min}, {p1.down_payment_percent}%", s) == "$73,000, 20%"
    s = sources(("get_property", CASA), ("get_property", {**CASA, "id": "p2"}))
    assert check("It's $73,000 down.", s, set())[0].hint == "{p1.down_payment_min} or {p2.down_payment_min}"


def test_every_internal_value_of_the_contracts_has_an_english_name():
    schema = json.loads((repo_root() / "contracts" / "mcp" / "get_property.schema.json").read_text(encoding="utf-8"))
    props = schema["outputSchema"]["properties"]
    assert set(props["type"]["enum"]) == set(markers.TYPE)
    assert set(props["kind"]["enum"]) == set(markers.KIND)
    assert set(props["status"]["enum"]) | {"sin_verificar"} == set(markers.STATUS)  # sin_verificar: A-4


def test_a_marker_that_cannot_be_filled_is_left_as_it_is_and_blocked():
    s = sources(("get_property", CASA))
    for text in ("{p2.price}", "{p1.owner}", "{p1.address_exact}", "{p1.requirements.monthly_income_min}", "{p1}",
                 "{search.count}", "{zones}"):
        assert render(text, s) == text
        assert check(text, s, set()) == [markers.Violation("marker", text)], text


def test_the_status_only_comes_from_this_turn_never_from_memory():
    remembered = {"p1": {k: v for k, v in CASA.items() if k != "status"}}
    s = sources(remembered=remembered)
    assert render("{p1.price}", s) == "$365,000"
    assert check("{p1.status}", s, set()) == [markers.Violation("marker", "{p1.status}")]
    s = sources(("get_availability", {"property_id": "p1", "status": "apartado"}), remembered=remembered)
    assert render("{p1.status}", s) == "reserved"


def test_this_turn_wins_over_memory():
    s = sources(("get_property", {**CASA, "price": 350000.0}), remembered={"p1": {"price": 365000.0}})
    assert render("{p1.price}", s) == "$350,000"


def test_two_lookups_of_the_same_tool_in_one_turn_can_both_be_named():
    s = sources(("get_property", CASA), ("get_property", {**CASA, "id": "p2", "price": 99000.0}))
    assert render("{p1.price} or {p2.price}", s) == "$365,000 or $99,000"


def test_a_search_gives_its_properties_its_alternatives_its_unavailable_ones_and_the_count():
    search = {"properties": [], "alternatives": [{**CASA, "id": "a1"}, {**CASA, "id": "a2"}],
              "alternatives_without": "zone", "unavailable": [{"id": "u1", "title": "X", "status": "cerrado"}]}
    s = sources(("search_properties", search))
    assert render("{search.count}: {a1.price}, {a2.zone}; {u1.status}", s) == "2: $365,000, Usaquen; off the market"


def test_the_zones_are_grouped_by_city():
    zones = {"zones": [{"zone": "Chapinero", "city": "Bogota"}, {"zone": "El Poblado", "city": "Medellin"},
                       {"zone": "Usaquen", "city": "Bogota"}]}
    assert render("{zones}", sources(("list_zones", zones))) == "Chapinero and Usaquen in Bogota; El Poblado in Medellin"


def test_a_typed_figure_of_only_one_property_passes():
    assert check("It's $365,000, 3 bedrooms.", sources(("get_property", CASA)), set()) == []
    # Dos datos de la misma propiedad con el mismo valor tampoco son ambiguos.
    assert check("2 bedrooms, 2 baths.", sources(("get_property", {**CASA, "bedrooms": 2, "bathrooms": 2})), set()) == []


def test_a_typed_figure_two_properties_share_is_blocked_with_the_markers_to_choose_from():
    s = sources(("get_property", CASA), ("get_property", {**CASA, "id": "p2", "price": 99000.0}))
    (violation,) = check("It has 3 bedrooms.", s, set())
    assert (violation.kind, violation.value, violation.hint) == ("number", "3", "{p1.bedrooms} or {p2.bedrooms}")


def test_the_line_says_which_property_a_typed_figure_belongs_to():
    s = sources(("get_property", CASA), ("get_property", {**CASA, "id": "p2", "bedrooms": 2, "m2": 70.0}))
    # Las dos tienen 2.5 baños, pero cada línea nombra la suya.
    assert check("- {p1.title}: 180 m², 2.5 baths\n- {p2.title}: 70 m², 2.5 baths (ref. p2)", s, set()) == []
    # La línea nombra p1, y 70 es de p2: se sugiere primero lo de la propiedad que nombra, si tiene ese campo.
    (violation,) = check("{p1.title} has 70 m².", s, set())
    assert (violation.value, violation.hint) == ("70", "{p2.m2}")


def test_an_invented_figure_is_blocked_without_a_suggestion():
    (violation,) = check("It's $150,000.", sources(("get_property", CASA)), set())
    assert (violation.value, violation.hint) == ("150000", None)


def test_a_property_of_this_turn_wins_over_a_remembered_one_with_the_same_value():
    s = sources(("get_property", CASA), remembered={"p2": {"bedrooms": 3}})
    assert check("It has 3 bedrooms.", s, set()) == []
    s = sources(remembered={"p1": {"bedrooms": 3}, "p2": {"bedrooms": 3}})
    assert check("It has 3 bedrooms.", s, set())[0].hint == "{p1.bedrooms} or {p2.bedrooms}"


def test_the_statuses_to_look_up_are_the_known_properties_without_one():
    s = sources(("get_property", CASA), remembered={"p2": {"price": 1}})
    assert markers.missing_statuses("{p1.status} {p2.status} {p9.status}", s) == {"p2"}
    markers.add_statuses(s, [{"property_id": "p2", "status": "cerrado"}])
    assert render("{p2.status}", s) == "off the market"


def test_the_leads_own_numbers_and_the_numbers_of_a_list_can_be_typed():
    s = sources(("get_property", CASA))
    said = lead_numbers(["My budget is $400,000", "I need 3 bedrooms"])
    assert check("With $400,000 and 3 bedrooms:\n1. {p1.title}\n2) {p1.title}\n- 3. {p1.zone}", s, said) == []
    assert check("I found 7 options.", s, set()) == [markers.Violation("number", "7")]


def test_a_reference_no_tool_gave_is_blocked_unless_it_is_known():
    s = sources(("get_property", CASA))
    assert check("See prop_00999.", s, set()) == [markers.Violation("reference", "prop_00999")]
    assert check("See prop_00231 and {p1.title}.", s, set(), known_refs=["prop_00231"]) == []


def test_outside_is_what_the_model_wrote_without_the_markers():
    assert outside("It's ${p1.price} and {p1.status}.") == "It's   and  ."
