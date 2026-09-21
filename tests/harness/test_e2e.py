"""Tests de CT-4: arnés -> core (fake) -> catalog (fake, MCP real), con los servicios corriendo de verdad.

Levanta `catalog` y `core` como procesos uvicorn reales en puertos libres (mismo código que las
imágenes Docker) y reproduce los guiones del arnés por HTTP. No necesita Docker.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))

import run_all  # noqa: E402
from replay import build_events, http_responder, replay  # noqa: E402

from db.testing import temporary_database  # noqa: E402


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _start(service, port, extra_env=None):
    env = os.environ | {"PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "services" / service)])}
    env |= extra_env or {}
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(200):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return process
        except OSError:
            if process.poll() is not None:
                break
            time.sleep(0.1)
    process.kill()
    raise AssertionError(f"{service} no arrancó")


@pytest.fixture(scope="module")
def database_url():
    with temporary_database() as url:
        yield url


@pytest.fixture(scope="module")
def stack(database_url):
    catalog_port, core_port = _free_port(), _free_port()
    catalog = _start("catalog", catalog_port)
    core = _start("core", core_port, {"CATALOG_SERVICE_URL": f"http://127.0.0.1:{catalog_port}",
                                      "DATABASE_URL": database_url})
    yield f"http://127.0.0.1:{core_port}"
    for process in (core, catalog):
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("path", run_all.discover(), ids=lambda p: p.name)
def test_every_script_passes_end_to_end(stack, path):
    replay(path, http_responder(stack))


def test_search_reply_carries_data_that_only_the_catalog_has(stack, tmp_path):
    script = tmp_path / "busqueda.jsonl"
    script.write_text(json.dumps({"text": "Busco un departamento"}) + "\n", encoding="utf-8")
    (reply,) = replay(script, http_responder(stack))
    assert "Por ejemplo: Depto 2 rec. en Providencia." in reply["text"]


# Estado final en Postgres tras reproducir cada guion (A-1): lo que dejó el flujo de la FSM.
EXPECTED_FINAL_STATE = {
    "saludo_y_busqueda": "RECOMMEND",
    "deep_link_y_visita": "HUMAN_HANDOFF",
    "flujo_completo_hasta_handoff": "HANDOFF",
    "lead_que_declara_todo": "QUALIFY",
    "queja_a_humano": "HUMAN_HANDOFF",
}


@pytest.mark.parametrize("name,expected", EXPECTED_FINAL_STATE.items())
def test_fsm_state_is_persisted_in_postgres(stack, database_url, name, expected):
    from sqlalchemy import create_engine, text

    path = ROOT / "harness" / "fixtures" / f"{name}.jsonl"
    replay(path, http_responder(stack))
    conversation_id = build_events(name, [{"text": "x"}])[0]["conversation_id"]
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            state = connection.execute(text("SELECT fsm_state FROM conversations WHERE id = :c"),
                                       {"c": conversation_id}).scalar()
    finally:
        engine.dispose()
    assert state == expected


# Score BANT-E que dejó cada guion en Postgres (A-9): (total, banda). El lead que declara presupuesto,
# que decide solo y que tiene urgencia, con propiedades que coinciden, es HOT; sin señales es COLD.
EXPECTED_FINAL_SCORE = {
    "saludo_y_busqueda": (20, "COLD"),
    "lead_que_declara_todo": (80, "HOT"),
}


@pytest.mark.parametrize("name,expected", EXPECTED_FINAL_SCORE.items())
def test_score_is_persisted_in_postgres(stack, database_url, name, expected):
    from sqlalchemy import create_engine, text

    replay(ROOT / "harness" / "fixtures" / f"{name}.jsonl", http_responder(stack))
    lead_id = build_events(name, [{"text": "x"}])[0]["lead_id"]
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT total, band FROM lead_scores WHERE lead_id = :l"),
                                     {"l": lead_id}).one()
    finally:
        engine.dispose()
    assert (row.total, row.band) == expected


def test_stop_withdraws_consent_in_postgres(stack, database_url, tmp_path):
    """A-8: tras `/stop` el lead queda dado de baja y se audita, sin que el LLM intervenga.

    Guion temporal y no de `harness/fixtures`: una baja es persistente y solo existe contra el core real.
    """
    from sqlalchemy import create_engine, text

    script = tmp_path / "baja_del_lead.jsonl"
    turns = ({"text": "Hola, buenas tardes", "expect_reply_contains": "asistente"},
             {"text": "/stop", "expect_reply_contains": "baja"},
             {"text": "Busco un departamento", "expect_reply_contains": "baja"})
    script.write_text("".join(json.dumps(turn) + chr(10) for turn in turns), encoding="utf-8")
    replay(script, http_responder(stack))
    lead_id = build_events("baja_del_lead", [{"text": "x"}])[0]["lead_id"]
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            consent = connection.execute(text("SELECT consent_status FROM leads WHERE id = :l"),
                                         {"l": lead_id}).scalar()
            audited = connection.execute(text("SELECT count(*) FROM audit_log WHERE action = 'input.opt_out' "
                                              "AND payload->>'lead_id' = :l"), {"l": lead_id}).scalar()
            state = connection.execute(text("SELECT fsm_state FROM conversations WHERE lead_id = :l"),
                                       {"l": lead_id}).scalar()
    finally:
        engine.dispose()
    assert (consent, audited, state) == ("withdrawn", 1, "DISCOVERY")


def test_a_streak_of_unresolved_turns_hands_off_to_a_human(stack, database_url, tmp_path):
    """A-13: tres turnos seguidos sin resolver dejan la conversación en HUMAN_HANDOFF."""
    from sqlalchemy import create_engine, text

    script = tmp_path / "frustracion.jsonl"
    turns = ({"text": "Hola, buenas tardes", "expect_reply_contains": "asistente"},
             {"text": "ok", "expect_reply_contains": "propiedad"},
             {"text": "ok", "expect_reply_contains": "propiedad"},
             {"text": "ok", "expect_reply_contains": "asesor"})
    script.write_text("".join(json.dumps(turn) + chr(10) for turn in turns), encoding="utf-8")
    replay(script, http_responder(stack))
    conversation_id = build_events("frustracion", [{"text": "x"}])[0]["conversation_id"]
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            state = connection.execute(text("SELECT fsm_state FROM conversations WHERE id = :c"),
                                       {"c": conversation_id}).scalar()
    finally:
        engine.dispose()
    assert state == "HUMAN_HANDOFF"


def test_funnel_events_are_written_once_per_transition(stack, database_url):
    """OBS-2: el flujo completo deja una fila por etapa alcanzada, en orden, y no una por turno."""
    from sqlalchemy import create_engine, text

    name = "flujo_completo_hasta_handoff"
    replay(ROOT / "harness" / "fixtures" / f"{name}.jsonl", http_responder(stack))
    lead_id = build_events(name, [{"text": "x"}])[0]["lead_id"]
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            events = [r.event_type for r in connection.execute(
                text("SELECT event_type FROM funnel_events WHERE lead_id = :l ORDER BY id"), {"l": lead_id})]
    finally:
        engine.dispose()
    assert events == ["state.DISCOVERY", "state.QUALIFY", "state.RECOMMEND", "state.SCHEDULE", "state.HANDOFF"]


def test_every_fixture_has_a_declared_final_state():
    assert {p.stem for p in run_all.discover()} == set(EXPECTED_FINAL_STATE)


def test_core_answers_503_when_the_catalog_is_down(database_url):
    core_port = _free_port()
    core = _start("core", core_port, {"CATALOG_SERVICE_URL": f"http://127.0.0.1:{_free_port()}",
                                      "DATABASE_URL": database_url})
    try:
        (event,) = build_events("caido", [{"text": "Busco casa"}])
        with pytest.raises(urllib.error.HTTPError) as exc:
            http_responder(f"http://127.0.0.1:{core_port}")(event)
        assert exc.value.code == 503
    finally:
        core.terminate()
        core.wait(timeout=10)


# A-3: el cliente de core descubre las tools del catálogo por MCP, sin nombres cableados.
def test_core_client_discovers_the_contract_tools_from_the_catalog():
    import asyncio

    sys.path.insert(0, str(ROOT / "services" / "core"))
    from app.catalog_client import CatalogClient

    port = _free_port()
    catalog = _start("catalog", port)
    try:
        tools = asyncio.run(CatalogClient(f"http://127.0.0.1:{port}").discover())
    finally:
        catalog.terminate()
        catalog.wait(timeout=10)

    contracts = {}
    for path in (ROOT / "contracts" / "mcp").glob("*.schema.json"):
        contract = json.loads(path.read_text(encoding="utf-8"))
        if "inputSchema" in contract:
            contracts[contract["name"]] = contract
    assert len(contracts) == 5 and sorted(tools) == sorted(contracts)
    for name, contract in contracts.items():
        # Lo que publica el catálogo exige lo mismo que el contrato.
        assert set(tools[name].input_schema.get("required", [])) == set(contract["inputSchema"].get("required", []))
