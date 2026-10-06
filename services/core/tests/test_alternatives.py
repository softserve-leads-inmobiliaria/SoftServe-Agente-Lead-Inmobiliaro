"""#213: una búsqueda sin resultados trae alternativas con un filtro menos (DT-57). Sin base."""
import asyncio
from typing import Any

from app.alternatives import Alternatives
from app.availability import AvailabilityGuard
from app.demand import SearchLog
from app.factuality import check

INVENTORY = [
    {"id": "prop_1", "title": "Apartment in Laureles", "zone": "Laureles", "kind": "departamento", "type": "renta",
     "price": 1200, "status": "disponible"},
    {"id": "prop_2", "title": "House in Usaquen", "zone": "Usaquen", "kind": "casa", "type": "venta",
     "price": 365000, "status": "disponible"},
    {"id": "prop_3", "title": "Apartment in Envigado", "zone": "Envigado", "kind": "departamento", "type": "renta",
     "price": 900, "status": "apartado"},
]


class Catalog:
    """Filtra como el catálogo, por igualdad en `zone`, `kind` y `type`."""

    def __init__(self, inventory=INVENTORY, current=None):
        self.inventory, self.current = inventory, current or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def tool_specs(self):
        return []

    async def call(self, tool, arguments=None):
        arguments = dict(arguments or {})
        self.calls.append((tool, arguments))
        if tool == "search_properties":
            return {"properties": [p for p in self.inventory
                                   if all(p.get(key) == arguments[key] for key in ("zone", "kind", "type")
                                          if key in arguments)]}
        if tool == "get_availability":
            pid = arguments["property_id"]
            return {"property_id": pid, "status": self.current.get(pid, "disponible"), "checked_at": None}
        raise AssertionError(tool)


def search(catalog, **arguments):
    return asyncio.run(Alternatives(catalog).call("search_properties", arguments))


def searches(catalog):
    return [arguments for tool, arguments in catalog.calls if tool == "search_properties"]


def test_a_search_with_results_is_left_alone():
    catalog = Catalog()
    result = search(catalog, zone="Laureles", type="renta")
    assert [p["id"] for p in result["properties"]] == ["prop_1"] and "alternatives" not in result
    assert len(searches(catalog)) == 1


def test_an_empty_search_brings_what_there_is_in_other_areas():
    # "houses for sale in Laureles": no hay, pero sí en Usaquen.
    result = search(Catalog(), zone="Laureles", kind="casa", type="venta")
    assert result["properties"] == []
    assert [p["id"] for p in result["alternatives"]] == ["prop_2"] and result["alternatives_without"] == "zone"


def test_if_no_area_has_it_the_kind_is_dropped_keeping_the_area():
    # "a house for rent in Laureles" (la conversación de #213): no hay casas en renta en ningún lado.
    catalog = Catalog()
    result = search(catalog, zone="Laureles", kind="casa", type="renta")
    assert [p["id"] for p in result["alternatives"]] == ["prop_1"] and result["alternatives_without"] == "kind"
    assert searches(catalog) == [{"zone": "Laureles", "kind": "casa", "type": "renta"},
                                 {"kind": "casa", "type": "renta"}, {"zone": "Laureles", "type": "renta"}]


def test_without_zone_the_kind_is_the_filter_dropped():
    # "where do you have houses for renting?"
    result = search(Catalog(), kind="casa", type="renta")
    assert {p["id"] for p in result["alternatives"]} == {"prop_1", "prop_3"} and result["alternatives_without"] == "kind"


def test_when_nothing_comes_up_there_are_no_alternatives():
    result = search(Catalog(), zone="Laureles", type="preventa")
    assert result == {"properties": []}


def test_a_search_without_zone_or_kind_is_not_relaxed():
    catalog = Catalog()
    search(catalog, type="preventa")
    assert len(searches(catalog)) == 1


def test_only_what_the_lead_asked_counts_as_unmet_demand_and_for_the_alert():
    log = SearchLog(Alternatives(Catalog()))
    asyncio.run(log.call("search_properties", {"zone": "Laureles", "kind": "casa", "type": "renta"}))
    assert [search["zone"] for search in log.unmet] == ["Laureles"]
    assert log.asked_search[0]["zone"] == "Laureles" and log.asked_search[1] == 0


def test_the_alternatives_are_revalidated_and_the_unavailable_ones_dropped():
    catalog = Catalog(current={"prop_1": "cerrado"})
    result = asyncio.run(AvailabilityGuard(Alternatives(catalog)).call("search_properties",
                                                                       {"kind": "casa", "type": "renta"}))
    assert result["alternatives"] == []  # prop_1 cerró, prop_3 ya venía apartada
    assert result["unavailable"] == []


def test_the_agent_can_say_how_many_alternatives_there_are():
    result = asyncio.run(AvailabilityGuard(Alternatives(Catalog())).call("search_properties",
                                                                         {"kind": "casa", "type": "renta"}))
    assert check("No houses for rent, but I have 1 apartment for rent in Laureles at $1,200.",
                 {"search_properties": result}) == []
