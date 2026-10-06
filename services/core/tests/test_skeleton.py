"""Tests de CT-4: el esqueleto del core (evento -> fakes -> catálogo -> evento), sin red real."""
import asyncio
import json

import pytest
from catalog_stub import PROPERTY, stub_call
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from app import main
from app.catalog_client import CatalogToolError
from app.skeleton import handle_event
from db.repo_root import repo_root

ROOT = repo_root()
VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "contracts" / "event.schema.json").read_text(encoding="utf-8")),
    format_checker=FormatChecker(),
)
INBOUND = json.loads((ROOT / "contracts" / "examples" / "event.valid.json").read_text(encoding="utf-8"))


class StubCatalog:
    def __init__(self, properties=None, error=None):
        self.calls = []
        self._properties = properties if properties is not None else [dict(PROPERTY)]
        self._error = error

    async def call(self, tool, arguments=None):
        self.calls.append((tool, arguments))
        if self._error:
            raise self._error
        if tool == "get_availability":
            return stub_call(tool, arguments)
        return {"properties": self._properties}


@pytest.fixture
def client(monkeypatch, engine):
    monkeypatch.setattr(main, "catalog", StubCatalog())
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    return TestClient(main.app)


def test_search_message_consults_the_catalog_over_mcp():
    catalog = StubCatalog()
    out = asyncio.run(handle_event(INBOUND | {"text": "Busco un departamento"}, catalog))
    assert catalog.calls == [("search_properties", {"kind": "departamento"})]
    assert out["text"].endswith("Por ejemplo: Casa de prueba.")
    VALIDATOR.validate(out)


def test_search_without_results_keeps_the_fixed_reply():
    # Sin tipo de inmueble, una búsqueda sin resultados no cambia la respuesta fija.
    out = asyncio.run(handle_event(INBOUND | {"text": "Busco algo"}, StubCatalog(properties=[])))
    assert "Por ejemplo" not in out["text"]


def test_search_of_a_kind_without_results_says_so():
    # #188: "busco depa" no debe ofrecer casas; si no hay del tipo pedido, lo dice.
    out = asyncio.run(handle_event(INBOUND | {"text": "Busco un depa"}, StubCatalog(properties=[])))
    assert "departamentos" in out["text"]


def test_search_without_a_kind_sends_no_kind_filter():
    catalog = StubCatalog()
    asyncio.run(handle_event(INBOUND | {"text": "Busco algo en El Poblado"}, catalog))
    assert catalog.calls == [("search_properties", {})]


def test_other_intents_do_not_touch_the_catalog():
    catalog = StubCatalog()
    asyncio.run(handle_event(INBOUND | {"text": "Hola"}, catalog))
    assert catalog.calls == []


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_post_event_returns_a_canonical_outbound_event(client):
    response = client.post("/events", json=INBOUND | {"text": "Busco casa"})
    assert response.status_code == 200
    VALIDATOR.validate(response.json())
    assert response.json()["direction"] == "outbound"


def test_invalid_event_is_422(client):
    assert client.post("/events", json=INBOUND | {"direction": "outbound"}).status_code == 422
    assert client.post("/events", json={"text": "x"}).status_code == 422


@pytest.mark.parametrize("error", [ConnectionError("caído"),
                                   CatalogToolError({"error_code": "UPSTREAM_UNAVAILABLE", "message": "x"})])
def test_catalog_failure_is_503_without_details(monkeypatch, engine, error):
    monkeypatch.setattr(main, "catalog", StubCatalog(error=error))
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    response = TestClient(main.app).post("/events", json=INBOUND | {"text": "Busco casa"})
    assert response.status_code == 503
    assert "caído" not in response.text


def test_database_failure_is_503_without_details(monkeypatch):
    from sqlalchemy import create_engine

    dead = create_engine("postgresql+psycopg://x:secreto@127.0.0.1:1/x", connect_args={"connect_timeout": 1})
    monkeypatch.setattr(main, "catalog", StubCatalog())
    monkeypatch.setattr(main, "get_engine", lambda: dead)
    response = TestClient(main.app).post("/events", json=INBOUND | {"text": "Hola"})
    assert response.status_code == 503
    assert "secreto" not in response.text


def test_post_event_persists_the_new_state(client, engine):
    from sqlalchemy import text

    client.post("/events", json=INBOUND | {"text": "Hola"})
    with engine.connect() as connection:
        state = connection.execute(text("SELECT fsm_state FROM conversations WHERE id = :c"),
                                   {"c": INBOUND["conversation_id"]}).scalar()
    assert state == "DISCOVERY"
