"""A-18: "avísame si sale algo así". La alerta guarda los filtros de la búsqueda y el *scheduler* (A-17) le
escribe al lead una vez por cada propiedad nueva que los cumpla. Con reloj falso."""
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from catalog_stub import stub_call
from sqlalchemy import text

from app import alerts
from app.agent import AgentResult
from app.channels import consent
from app.interest import REMEMBERED_KEY
from app.scheduler import STOP_HINT, Scheduler
from app.turn import process_turn
from db.models import Base
from db.property_history import record_listing, set_status
from db.repo_root import repo_root
from db.session import session_scope

INBOUND = {key: value for key, value in json.loads(
    (repo_root() / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8")).items()
    if key not in ("property_id", "utm_campaign")}
MINUTE = timedelta(minutes=1)


@pytest.fixture(autouse=True)
def _clean(engine):
    """`run_due` y el `alert_scan` ven toda la base: cada test arranca sin jobs pendientes ni alertas activas."""
    with engine.begin() as connection:
        connection.execute(text("UPDATE scheduled_jobs SET status = 'cancelled' WHERE status = 'pending'"))
        connection.execute(text("UPDATE property_alerts SET active = false WHERE active"))


@pytest.fixture
def zone():
    return f"Zona {uuid.uuid4().hex[:8]}"  # cada test con su zona: las alertas de otro test no la cumplen


class Catalog:
    def __init__(self, found=()):
        self.found = list(found)

    async def call(self, tool, arguments=None):
        if tool == "search_properties":
            return {"properties": list(self.found)}
        return stub_call(tool, arguments)


class Searches:
    """Un agente que busca con filtros, como lo haría el de Bedrock."""

    name = "busca"

    def __init__(self, filters):
        self.filters = filters

    async def run(self, turn, catalog):
        found = await catalog.call("search_properties", dict(self.filters))
        return AgentResult(text="Esto es lo que encontré.", intent="busqueda_propiedad",
                           tool_results={"search_properties": found},
                           tool_calls=[{"name": "search_properties", "is_error": False}])


class Channel:
    """Solo anota los avisos de alerta: con el reloj adelantado también vencen los seguimientos de A-17."""

    def __init__(self, reachable=True):
        self.sent, self._reachable = [], reachable

    def reachable(self, chat_id):
        return self._reachable

    def send(self, chat_id, text):
        if text.startswith("Something like"):
            self.sent.append(text)


class Lead:
    def __init__(self, engine):
        self.engine = engine
        self.conversation_id, self.lead_id = str(uuid.uuid4()), str(uuid.uuid4())

    def say(self, message, agent=None, found=()):
        event = INBOUND | {"conversation_id": self.conversation_id, "lead_id": self.lead_id,
                           "event_id": str(uuid.uuid4()), "text": message}
        return asyncio.run(process_turn(self.engine, event, Catalog(found), agent=agent))["text"]

    def search(self, zone, found=(), message="I'm looking for a house"):
        return self.say(message, Searches({"zone": zone, "type": "renta", "budget_range": {"min": 0, "max": 20000}}),
                        found)

    def _rows(self, sql):
        with self.engine.connect() as connection:
            return connection.execute(text(sql), {"l": self.lead_id, "c": self.conversation_id}).all()

    def alerts(self):
        return self._rows("SELECT zone, type, bedrooms, budget_min, budget_max, active, expires_at - created_at"
                          " FROM property_alerts WHERE lead_id = :l ORDER BY created_at")

    def last_tool_results(self):
        return self._rows("SELECT last_tool_results FROM fsm_context WHERE conversation_id = :c")[0][0]

    def property_alert_jobs(self):
        return self._rows("SELECT status FROM scheduled_jobs WHERE kind = 'property_alert' AND lead_id = :l")

    def saved(self):
        return [row[0] for row in self._rows(
            "SELECT content FROM messages WHERE conversation_id = :c AND content LIKE 'Something like%'")]


def _list(engine, zone, *, price=15000, bedrooms=2, type_="renta", status="disponible", at=None):
    """Publica una propiedad nueva por `db/property_history.py`, como lo hará quien dé de alta inventario."""
    property_id = f"prop_{uuid.uuid4().hex[:8]}"
    with session_scope(engine) as session:
        session.execute(text(
            "INSERT INTO properties (id, title, price, type, kind, zone, m2, bedrooms, bathrooms, address_approx,"
            " address_exact, status) VALUES (:id, 'Depto nuevo', :price, :type, 'departamento', :zone, 80, :bedrooms, 1,"
            " 'Col. Centro', 'Calle Falsa 123', :status)"),
            {"id": property_id, "price": price, "type": type_, "zone": zone, "bedrooms": bedrooms, "status": status})
        record_listing(session, property_id, "test", at=at)
    return property_id


def _run(engine, channel=None, after=timedelta(minutes=11)):
    """Una pasada del scheduler a `after` de ahora (el `alert_scan` se programa a 10 minutos), y otra para
    los avisos que esa pasada encoló."""
    now = datetime.now(UTC) + after
    scheduler = Scheduler(engine, channel)
    return scheduler.run_due(now) + scheduler.run_due(now)


def _alerted(engine, zone, channel=None, **listing):
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    property_id = _list(engine, zone, **listing)
    channel = channel or Channel()
    _run(engine, channel)
    return lead, property_id, channel


# --- detectar el pedido --------------------------------------------------------------------------------

@pytest.mark.parametrize("message", ["notify me", "Notify me if something like that comes up", "¿me avisas?", "me avisan porfa",
                                     "notifícame", "quiero una alerta", "Avisenme cuando haya",
                                     "notify me", "Notify me if something like that comes up", "keep me posted",
                                     "let me know when there's one", "let me know if anything comes up",
                                     "can you set up an alert?"])
def test_the_lead_asking_for_an_alert_is_detected(message):
    assert alerts.is_request(message)


@pytest.mark.parametrize("message", ["Busco casa en Usaquen", "How much does it cost?", "I'd like to schedule a visit", "",
                                     "let me know the price", "I'm looking for a house in Usaquen",
                                     "I'd like to schedule a viewing"])
def test_other_messages_are_not_an_alert_request(message):
    assert not alerts.is_request(message)


# --- ofrecer y guardar --------------------------------------------------------------------------------------

def test_a_search_with_few_results_offers_the_alert(engine, zone):
    lead = Lead(engine)
    reply = lead.search(zone, found=[{"id": "prop_test", "title": "Casa", "status": "disponible"}])
    assert reply.endswith(alerts.OFFER_TEXT)
    assert lead.last_tool_results()[alerts.ALERT_KEY] == {"zone": zone, "type": "renta", "bedrooms": None,
                                                          "budget_min": 0, "budget_max": 20000}


def test_a_search_with_many_results_does_not(engine, zone):
    found = [{"id": f"prop_{n}", "title": "Casa", "status": "disponible"} for n in range(3)]
    reply = Lead(engine).search(zone, found=found)
    assert alerts.OFFER_TEXT not in reply


def test_a_search_without_filters_does_not(engine):
    lead = Lead(engine)
    reply = lead.say("I'm looking for a house")  # el agente fake busca sin filtros
    assert alerts.OFFER_TEXT not in reply and alerts.ALERT_KEY not in lead.last_tool_results()


def test_asking_after_the_offer_saves_the_filters_of_that_search(engine, zone):
    lead = Lead(engine)
    lead.search(zone)
    assert lead.say("notify me") == alerts.CREATED_TEXT
    [(saved_zone, kind, bedrooms, budget_min, budget_max, active, lasts)] = lead.alerts()
    assert (saved_zone, kind, bedrooms, budget_min, budget_max, active) == (zone, "renta", None, 0, 20000, True)
    assert lasts == timedelta(days=alerts.ALERT_DAYS)


def test_asking_in_the_same_turn_as_the_search_uses_that_search(engine, zone):
    lead = Lead(engine)
    assert lead.search(zone, message="I'm looking for a rental, notify me if something like that comes up") == alerts.CREATED_TEXT
    assert lead.alerts()[0][0] == zone


def test_asking_without_a_search_asks_what_the_lead_is_looking_for(engine):
    lead = Lead(engine)
    assert lead.say("notify me if something comes up") == alerts.NO_SEARCH_TEXT
    assert lead.alerts() == []


def test_a_new_alert_replaces_the_previous_one(engine, zone):
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    lead.search(zone + " norte")
    lead.say("notify me")
    assert [(row[0], row[5]) for row in lead.alerts()] == [(zone, False), (zone + " norte", True)]


def test_an_alert_never_stores_the_lead_text():
    columns = set(Base.metadata.tables["property_alerts"].columns.keys())
    assert not {"text", "content", "message"} & columns


# --- avisar --------------------------------------------------------------------------------------------

def test_a_new_matching_property_is_announced_once(engine, zone):
    lead, property_id, channel = _alerted(engine, zone)
    [sent] = channel.sent
    assert "Depto nuevo" in sent and property_id in sent and zone in sent and "$15,000" in sent
    assert "2 bedrooms" in sent and sent.endswith(STOP_HINT)
    assert lead.saved() == [sent]
    assert lead.last_tool_results()[REMEMBERED_KEY]["id"] == property_id  # "I'm interested" sabe cuál es
    _run(engine, channel, after=timedelta(minutes=30))
    assert len(channel.sent) == 1 and lead.property_alert_jobs() == [("done",)]


def test_a_property_that_was_already_listed_is_not_new(engine, zone):
    _list(engine, zone, at=datetime.now(UTC) - timedelta(days=1))
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    channel = Channel()
    _run(engine, channel)
    assert channel.sent == []


def test_a_property_back_to_available_is_announced(engine, zone):
    property_id = _list(engine, zone, at=datetime.now(UTC) - timedelta(days=1))
    with session_scope(engine) as session:
        set_status(session, property_id, "apartado", "test", at=datetime.now(UTC) - timedelta(hours=1))
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    with session_scope(engine) as session:
        set_status(session, property_id, "disponible", "test")
    channel = Channel()
    _run(engine, channel)
    assert len(channel.sent) == 1 and property_id in channel.sent[0]


def test_the_synthetic_history_never_makes_a_property_new(engine, zone):
    # N-9 siembra eventos en propiedades reales; un "volvió a disponible" sembrado no es noticia para nadie.
    property_id = _list(engine, zone, at=datetime.now(UTC) - timedelta(days=1))
    with session_scope(engine) as session:
        set_status(session, property_id, "apartado", "seed", at=datetime.now(UTC) - timedelta(hours=1))
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    with session_scope(engine) as session:
        set_status(session, property_id, "disponible", "seed", synthetic=True)
    channel = Channel()
    _run(engine, channel)
    assert channel.sent == []


@pytest.mark.parametrize("listing", [{"price": 25000}, {"type_": "venta"}, {"status": "apartado"}])
def test_a_property_outside_the_filters_is_not_announced(engine, zone, listing):
    _, _, channel = _alerted(engine, zone, **listing)
    assert channel.sent == []


def test_a_lead_without_a_channel_gets_nothing_saved(engine, zone):
    lead, _, channel = _alerted(engine, zone, Channel(reachable=False))
    assert channel.sent == [] and lead.saved() == [] and lead.property_alert_jobs() == [("done",)]


def test_a_property_taken_before_the_message_goes_out_is_not_announced(engine, zone):
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    property_id = _list(engine, zone)
    now, channel = datetime.now(UTC) + timedelta(minutes=11), Channel()
    one_at_a_time = Scheduler(engine, channel, batch_size=1)
    one_at_a_time.run_due(now)  # solo el scan: encola el aviso
    assert lead.property_alert_jobs() == [("pending",)]
    with session_scope(engine) as session:
        set_status(session, property_id, "apartado", "test")
    one_at_a_time.run_due(now)
    assert channel.sent == [] and lead.property_alert_jobs() == [("cancelled",)]


def test_the_message_goes_through_the_output_guardrails(engine, zone, monkeypatch, caplog):
    original = alerts.alert_text
    monkeypatch.setattr(alerts, "alert_text", lambda prop: original(prop) + " Con 30% de descuento este mes.")
    with caplog.at_level("WARNING", logger="core.scheduler"):
        lead, _, channel = _alerted(engine, zone)
    assert channel.sent == [] and lead.property_alert_jobs() == [("cancelled",)]
    assert any(record.getMessage() == "alerta bloqueada por el guardrail de salida" for record in caplog.records)


# --- baja, vencimiento y reprogramación ------------------------------------------------------------------

def test_stop_turns_the_alert_off(engine, zone):
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    lead.say("/stop")
    assert lead.alerts()[0][5] is False
    _list(engine, zone)
    channel = Channel()
    _run(engine, channel)
    assert channel.sent == []


def test_stop_in_the_channel_turns_the_alert_off(engine, zone):
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    consent.withdraw_consent(engine, uuid.UUID(lead.lead_id))
    assert lead.alerts()[0][5] is False


def test_a_withdrawn_lead_is_never_alerted_even_if_the_alert_was_left(engine, zone):
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    with engine.begin() as connection:
        connection.execute(text("UPDATE leads SET consent_status = 'withdrawn' WHERE id = :l"), {"l": lead.lead_id})
    _list(engine, zone)
    channel = Channel()
    _run(engine, channel)
    assert channel.sent == []


def test_an_expired_alert_is_turned_off_and_the_scan_stops(engine, zone):
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    _list(engine, zone)
    channel = Channel()
    _run(engine, channel, after=timedelta(days=alerts.ALERT_DAYS + 1))
    assert channel.sent == [] and lead.alerts()[0][5] is False
    with engine.connect() as connection:
        pending = connection.execute(text(
            "SELECT count(*) FROM scheduled_jobs WHERE kind = 'alert_scan' AND status = 'pending'")).scalar()
    assert pending == 0


def test_the_scan_keeps_itself_scheduled_while_there_are_active_alerts(engine, zone):
    lead = Lead(engine)
    lead.search(zone)
    lead.say("notify me")
    for minutes in (11, 22, 33):
        _run(engine, Channel(), after=timedelta(minutes=minutes))
    with engine.connect() as connection:
        scans = connection.execute(text(
            "SELECT status, count(*) FROM scheduled_jobs WHERE kind = 'alert_scan' GROUP BY status")).all()
    assert dict(scans).get("pending") == 1


def test_the_lead_answering_from_nurture_goes_back_to_discovery(engine, zone):
    lead, _, _ = _alerted(engine, zone)
    with engine.begin() as connection:
        connection.execute(text("UPDATE conversations SET fsm_state = 'NURTURE' WHERE id = :c"),
                           {"c": lead.conversation_id})
    lead.say("I'm interested")
    assert lead._rows("SELECT fsm_state FROM conversations WHERE id = :c")[0][0] == "DISCOVERY"


# --- el texto ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prop,expected", [
    ({"id": "prop_1", "title": "Casa", "zone": "Usaquen", "type": "venta", "bedrooms": 3, "price": 2350000.0},
     "Casa (ref. prop_1), in Usaquen: for sale, 3 bedrooms, $2,350,000."),
    ({"id": "prop_2", "title": "Estudio", "zone": "Centro", "type": "renta", "bedrooms": 1, "price": 8500.5},
     "Estudio (ref. prop_2), in Centro: for rent, 1 bedroom, $8,500.50."),
])
def test_the_alert_text_uses_only_catalog_data(prop, expected):
    assert expected in alerts.alert_text(prop)
