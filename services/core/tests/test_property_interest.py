"""N-4: qué propiedades vio, consultó o visitó cada lead, en `property_interest` y en la transacción del turno."""
import asyncio
import json
import uuid

import pytest
from catalog_stub import PROPERTY, stub_call
from sqlalchemy import text
from test_visits_turn import SLOTS, ContractAgenda

from app import scheduling_client
from app.agent import AgentResult
from app.interest import REMEMBERED_KEY, interactions
from app.state_store import StateStore
from app.turn import process_turn
from app.visits import OFFER_KEY, VISIT_KEY
from db.models import Base
from db.repo_root import repo_root

INBOUND = {key: value for key, value in json.loads(
    (repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8")).items()
    if key != "property_id"}
ANUNCIO = {"id": "prop_anuncio", "title": "Depto del anuncio", "zone": "Providencia", "status": "disponible"}
OTRA = {"id": "prop_otra", "title": "Casa en Zapopan", "zone": "Zapopan", "status": "disponible"}


# --- interactions: lógica pura -----------------------------------------------------------------------

def test_each_tool_gives_its_kind():
    found = interactions({"get_property": OTRA, "search_properties": {"properties": [ANUNCIO, PROPERTY]}})
    assert found == [("prop_otra", "consulta"), ("prop_anuncio", "busqueda"), ("prop_test", "busqueda")]


def test_the_listing_counts_as_anuncio_and_not_as_consulta():
    # El turno mete la propiedad del anuncio en `get_property` aunque el agente no la consultó.
    assert interactions({"get_property": ANUNCIO}, listing=ANUNCIO) == [("prop_anuncio", "anuncio")]
    assert interactions({"get_property": OTRA}, listing=ANUNCIO) == [("prop_anuncio", "anuncio"),
                                                                     ("prop_otra", "consulta")]


def test_a_booked_visit_counts_as_visita():
    assert interactions({}, booked=PROPERTY) == [("prop_test", "visita")]


def test_the_same_property_and_kind_is_one_interaction_per_turn():
    repeated = {"search_properties": {"properties": [PROPERTY, PROPERTY]}}
    assert interactions(repeated) == [("prop_test", "busqueda")]


def test_remembered_properties_and_open_offers_are_not_interactions_of_this_turn():
    carried = {REMEMBERED_KEY: OTRA, OFFER_KEY: {"property": OTRA, "slots": SLOTS},
               VISIT_KEY: {"property": OTRA, "preferred_time": "mañana"}}
    assert interactions(carried) == []


@pytest.mark.parametrize("tool_results", [
    {}, {"get_property": None}, {"get_property": {"title": "sin id"}}, {"search_properties": {"properties": None}},
    {"search_properties": {"properties": [{"title": "sin id"}, "basura"]}}, {"search_properties": "error"},
])
def test_malformed_results_give_nothing(tool_results):
    assert interactions(tool_results) == []


# --- en el turno, contra Postgres --------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _inventory(engine):
    """Las propiedades que devuelven los catálogos de prueba, en la base del módulo."""
    with engine.begin() as connection:
        for prop in (ANUNCIO, OTRA, PROPERTY):
            connection.execute(text(
                "INSERT INTO properties (id, title, price, type, zone, m2, bedrooms, bathrooms, address_approx,"
                " address_exact) VALUES (:id, :title, 1, 'renta', 'Providencia', 1, 1, 1, 'a', 'b')"
                " ON CONFLICT (id) DO NOTHING"), {"id": prop["id"], "title": prop["title"]})


class Catalog:
    """El anuncio por `get_property`; las búsquedas devuelven `found`."""

    def __init__(self, found=(PROPERTY,)):
        self.found = [dict(prop) for prop in found]

    async def call(self, tool, arguments=None):
        if tool == "get_property":
            return dict(ANUNCIO) | {"address_approx": "Providencia"}
        if tool == "search_properties":
            return {"properties": list(self.found)}
        return stub_call(tool, arguments)


class LooksUp:
    """Un agente que consulta una propiedad con `get_property`, como lo haría el de Bedrock."""

    name = "consulta"

    async def run(self, turn, catalog):
        return AgentResult(text="Te cuento de esa casa.", intent="consulta_precio",
                           tool_results={"get_property": dict(OTRA)},
                           tool_calls=[{"name": "get_property", "is_error": False}])


class Lead:
    def __init__(self, engine):
        self.engine = engine
        self.conversation_id, self.lead_id = str(uuid.uuid4()), str(uuid.uuid4())

    def say(self, message, catalog=None, agent=None, **extra):
        event = INBOUND | {"conversation_id": self.conversation_id, "lead_id": self.lead_id,
                           "event_id": str(uuid.uuid4()), "text": message, **extra}
        return asyncio.run(process_turn(self.engine, event, catalog or Catalog(), agent=agent))

    def interest(self):
        with self.engine.connect() as connection:
            return sorted(connection.execute(text(
                "SELECT property_id, kind FROM property_interest WHERE lead_id = :l"), {"l": self.lead_id}).all())

    def messages(self):
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT count(*) FROM messages WHERE conversation_id = :c"),
                                      {"c": self.conversation_id}).scalar()


def test_the_deep_link_writes_anuncio(engine):
    lead = Lead(engine)
    lead.say("", property_id="prop_anuncio")
    assert lead.interest() == [("prop_anuncio", "anuncio")]


def test_a_search_writes_busqueda_for_each_result(engine):
    lead = Lead(engine)
    lead.say("Busco un departamento", Catalog(found=(PROPERTY, OTRA)))
    assert lead.interest() == [("prop_otra", "busqueda"), ("prop_test", "busqueda")]


def test_a_get_property_writes_consulta(engine):
    lead = Lead(engine)
    lead.say("¿Y la casa de Zapopan?", agent=LooksUp())
    assert lead.interest() == [("prop_otra", "consulta")]


def test_the_same_search_in_two_turns_is_two_rows(engine):
    # Una fila por turno: dos búsquedas son dos interacciones, y N-6 las cuenta así.
    lead = Lead(engine)
    lead.say("Busco un departamento")
    lead.say("Busco otro departamento")
    assert lead.interest() == [("prop_test", "busqueda"), ("prop_test", "busqueda")]


def test_a_booked_visit_writes_visita(engine, monkeypatch):
    monkeypatch.setattr(scheduling_client, "get_scheduling", lambda: ContractAgenda())
    lead = Lead(engine)
    for message in ("Hola", "Busco un departamento", "¿Cuánto cuesta?", "Quiero agendar una visita"):
        lead.say(message)
    assert ("prop_test", "visita") not in lead.interest()  # ofrecer horarios no es una visita
    lead.say("la 2")
    assert lead.interest().count(("prop_test", "visita")) == 1


def test_a_property_missing_from_the_inventory_is_skipped_without_breaking_the_turn(engine):
    lead = Lead(engine)
    reply = lead.say("Busco un departamento", Catalog(found=({"id": "prop_no_existe", "title": "x"}, OTRA)))
    assert reply["text"] and lead.interest() == [("prop_otra", "busqueda")]


def test_if_saving_the_interest_fails_the_whole_turn_fails(engine, monkeypatch):
    def broken(self, lead_id, found):
        raise RuntimeError("falla al guardar")

    monkeypatch.setattr(StateStore, "record_interest", broken)
    lead = Lead(engine)
    with pytest.raises(RuntimeError):
        lead.say("Busco un departamento")
    assert lead.messages() == 0 and lead.interest() == []


def test_nothing_from_the_lead_text_is_stored():
    columns = set(Base.metadata.tables["property_interest"].columns.keys())
    assert columns == {"id", "lead_id", "property_id", "kind", "created_at", "synthetic"}
