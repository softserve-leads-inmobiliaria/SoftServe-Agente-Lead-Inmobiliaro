"""N-5: la campaña del primer contacto en `leads.utm_campaign` y las búsquedas sin resultados en `unmet_searches`."""
import asyncio
import json
import uuid
from decimal import Decimal

import pytest
from catalog_stub import stub_call
from sqlalchemy import text

from app.agent import AgentResult
from app.demand import SearchLog, unmet_filters
from app.state_store import StateStore
from app.turn import process_turn
from db.models import Base
from db.repo_root import repo_root

INBOUND = {key: value for key, value in json.loads(
    (repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8")).items()
    if key not in ("property_id", "utm_campaign")}
FILTROS = {"zone": "Laureles", "type": "renta", "bedrooms": 3, "budget_range": {"min": 0, "max": 15000}}


# --- unmet_filters y SearchLog: sin base -------------------------------------------------------------

def test_the_filters_are_kept_as_columns():
    assert unmet_filters(FILTROS) == {"zone": "Laureles", "type": "renta", "kind": None, "bedrooms": 3,
                                      "budget_min": 0, "budget_max": 15000}
    assert unmet_filters({**FILTROS, "kind": "departamento"})["kind"] == "departamento"  # #209


@pytest.mark.parametrize("arguments", [
    None, {}, {"zone": " ", "type": "traspaso", "kind": "castillo", "bedrooms": -1,
               "budget_range": {"min": -5, "max": "mucho"}},
    {"bedrooms": True, "budget_range": "barato"}, {"bedrooms": 2.5},
])
def test_what_the_table_would_reject_is_left_empty(arguments):
    assert unmet_filters(arguments) == {"zone": None, "type": None, "kind": None, "bedrooms": None,
                                        "budget_min": None, "budget_max": None}


class Catalog:
    def __init__(self, found=()):
        self.found = list(found)

    async def call(self, tool, arguments=None):
        if tool == "search_properties":
            return {"properties": list(self.found)}
        return stub_call(tool, arguments)


def test_only_searches_without_results_are_logged_once_each():
    async def run():
        log = SearchLog(Catalog())
        await log.call("search_properties", FILTROS)
        await log.call("search_properties", dict(FILTROS))  # repetida en el turno
        await log.call("search_properties", {"zone": "Usaquen"})
        await log.call("get_property", {"property_id": "prop_x"})
        found = SearchLog(Catalog(found=[{"id": "prop_x"}]))
        await found.call("search_properties", FILTROS)
        return log.unmet, found.unmet

    unmet, found = asyncio.run(run())
    assert [search["zone"] for search in unmet] == ["Laureles", "Usaquen"]
    assert found == []


# --- en el turno, contra Postgres --------------------------------------------------------------------

class Searches:
    """Un agente que busca con filtros, como lo haría el de Bedrock."""

    name = "busca"

    def __init__(self, *searches):
        self.searches = searches

    async def run(self, turn, catalog):
        results = {}
        for arguments in self.searches:
            results["search_properties"] = await catalog.call("search_properties", dict(arguments))
        return AgentResult(text="No encontré nada con eso.", intent="busqueda_propiedad", tool_results=results,
                           tool_calls=[{"name": "search_properties", "is_error": False}] * len(self.searches))


class Lead:
    def __init__(self, engine):
        self.engine = engine
        self.conversation_id, self.lead_id = str(uuid.uuid4()), str(uuid.uuid4())

    def say(self, message="I'm looking for something", agent=None, catalog=None, **extra):
        event = INBOUND | {"conversation_id": self.conversation_id, "lead_id": self.lead_id,
                           "event_id": str(uuid.uuid4()), "text": message, **extra}
        return asyncio.run(process_turn(self.engine, event, catalog or Catalog(), agent=agent))

    def campaign(self):
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT utm_campaign FROM leads WHERE id = :l"),
                                      {"l": self.lead_id}).scalar()

    def messages(self):
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT count(*) FROM messages WHERE conversation_id = :c"),
                                      {"c": self.conversation_id}).scalar()


def _unmet(engine, zone):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT zone, type, bedrooms, budget_min, budget_max FROM unmet_searches WHERE zone = :z"),
            {"z": zone}).all()


def test_the_first_campaign_is_saved(engine):
    lead = Lead(engine)
    lead.say("", utm_campaign="feria_octubre")
    assert lead.campaign() == "feria_octubre"


def test_a_later_campaign_does_not_overwrite_the_first(engine):
    lead = Lead(engine)
    lead.say("Hi")
    assert lead.campaign() is None  # sin campaña no se escribe nada
    lead.say("", utm_campaign="feria_octubre")
    lead.say("", utm_campaign="volante_noviembre")
    assert lead.campaign() == "feria_octubre"


def test_a_search_without_results_writes_its_filters(engine):
    zone = f"Zona {uuid.uuid4().hex[:6]}"
    Lead(engine).say(agent=Searches(FILTROS | {"zone": zone}))
    assert _unmet(engine, zone) == [(zone, "renta", 3, Decimal("0.00"), Decimal("15000.00"))]


def test_a_search_with_results_writes_nothing(engine):
    zone = f"Zona {uuid.uuid4().hex[:6]}"
    Lead(engine).say(agent=Searches({"zone": zone}), catalog=Catalog(found=[{"id": "prop_x", "status": "vendida"}]))
    assert _unmet(engine, zone) == []


def test_the_same_search_twice_in_a_turn_is_one_row_but_two_turns_are_two(engine):
    zone = f"Zona {uuid.uuid4().hex[:6]}"
    lead = Lead(engine)
    lead.say(agent=Searches({"zone": zone}, {"zone": zone}))
    assert len(_unmet(engine, zone)) == 1
    lead.say(agent=Searches({"zone": zone}))
    assert len(_unmet(engine, zone)) == 2


def test_if_saving_fails_the_whole_turn_fails(engine, monkeypatch):
    def broken(self, searches):
        raise RuntimeError("falla al guardar")

    monkeypatch.setattr(StateStore, "record_unmet_searches", broken)
    lead = Lead(engine)
    with pytest.raises(RuntimeError):
        lead.say("", utm_campaign="feria_octubre")
    assert lead.messages() == 0 and lead.campaign() is None


def test_an_unmet_search_never_stores_the_lead_or_its_text():
    columns = set(Base.metadata.tables["unmet_searches"].columns.keys())
    assert "lead_id" not in columns and not {"text", "content", "message"} & columns
