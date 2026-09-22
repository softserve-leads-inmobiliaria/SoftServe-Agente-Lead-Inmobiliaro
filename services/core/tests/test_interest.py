"""#81: la zona del lead sale de las propiedades que mira, y se escribe una sola vez."""
import asyncio
import json
import uuid

from sqlalchemy import text

from app.interest import property_of_interest, zone_of_interest
from app.turn import process_turn
from db.repo_root import repo_root

INBOUND = json.loads((repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))

BUSQUEDA = {"search_properties": {"properties": [
    {"id": "prop_00231", "title": "Depa en Providencia", "zone": "Providencia"},
    {"id": "prop_00240", "title": "Casa en Zapopan", "zone": "Zapopan"},
]}}
FICHA = {"get_property": {"id": "prop_00240", "title": "Casa en Zapopan", "zone": "Zapopan"}}


def test_sin_tools_no_hay_zona():
    assert zone_of_interest({}) is None
    assert property_of_interest({}) is None


def test_una_busqueda_da_la_zona_del_primer_resultado():
    assert zone_of_interest(BUSQUEDA) == "Providencia"


def test_una_busqueda_sin_resultados_no_da_zona():
    assert zone_of_interest({"search_properties": {"properties": []}}) is None


def test_mirar_una_propiedad_manda_sobre_la_busqueda():
    """Pedir una propiedad concreta es señal de interés más fuerte que un listado."""
    assert zone_of_interest(BUSQUEDA | FICHA) == "Zapopan"


def test_una_propiedad_sin_zona_no_inventa_nada():
    assert zone_of_interest({"get_property": {"id": "prop_1", "zone": ""}}) is None


class StubCatalog:
    """Catálogo con una propiedad de Providencia, y que responde a la revalidación de A-4."""

    async def call(self, tool, arguments=None):
        if tool == "get_availability":
            return {"property_id": "prop_00231", "status": "disponible", "checked_at": "2026-01-01T00:00:00Z"}
        return {"properties": [{"id": "prop_00231", "title": "Depa en Providencia",
                                "zone": "Providencia", "status": "disponible"}]}

    async def tool_specs(self):
        return []


def _say(engine, conversation_id, lead_id, texto):
    event = INBOUND | {"conversation_id": conversation_id, "lead_id": lead_id,
                       "event_id": str(uuid.uuid4()), "text": texto}
    return asyncio.run(process_turn(engine, event, StubCatalog()))


def _zone(engine, lead_id):
    with engine.connect() as connection:
        return connection.execute(text("SELECT zone FROM leads WHERE id = :l"),
                                  {"l": lead_id}).scalar()


def test_un_turno_con_busqueda_completa_la_zona_del_lead(engine):
    conversation_id, lead_id = str(uuid.uuid4()), str(uuid.uuid4())

    _say(engine, conversation_id, lead_id, "Busco un departamento")

    assert _zone(engine, lead_id) == "Providencia"


def test_la_zona_no_se_pisa_en_turnos_siguientes(engine):
    """La primera zona observada manda: si el lead ya tiene una, un turno nuevo no la cambia."""
    conversation_id, lead_id = str(uuid.uuid4()), str(uuid.uuid4())
    _say(engine, conversation_id, lead_id, "Busco un departamento")
    with engine.begin() as connection:
        connection.execute(text("UPDATE leads SET zone = 'Andares' WHERE id = :l"), {"l": lead_id})

    _say(engine, conversation_id, lead_id, "Busco otra casa")

    assert _zone(engine, lead_id) == "Andares"


def test_un_turno_sin_tools_deja_la_zona_vacia(engine):
    """Limitación conocida (#81): un lead que nunca mira propiedades no entra en ninguna cola."""
    conversation_id, lead_id = str(uuid.uuid4()), str(uuid.uuid4())
    _say(engine, conversation_id, lead_id, "Hola")

    assert _zone(engine, lead_id) is None
