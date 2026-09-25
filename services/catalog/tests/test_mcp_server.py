"""Tests de C-1b: el catálogo real como servidor MCP, hablado por streamable HTTP.

El mismo cliente MCP de `core` (CT-8) y el mismo transporte, pero contra la fuente real: desde
C-1 la lógica vive en Postgres. Se levanta el servidor de verdad sobre un puerto libre.
"""
import asyncio
import json
import socket
import threading
import time
import urllib.request

import pytest
import uvicorn
from jsonschema import Draft202012Validator, FormatChecker
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.main import app
from db.repo_root import repo_root

CONTRACTS = repo_root() / "contracts"
TOOLS = ["search_properties", "get_property", "get_availability", "get_promotions", "check_requirements"]


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))

@pytest.fixture(scope="module")
def base_url():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "el servidor no arrancó"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _with_session(base_url, action):
    async def go():
        async with streamable_http_client(f"{base_url}/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await action(session)

    return asyncio.run(go())


def call(base_url, name, arguments):
    return _with_session(base_url, lambda session: session.call_tool(name, arguments))


def test_health_answers_next_to_mcp(base_url):
    assert json.loads(urllib.request.urlopen(f"{base_url}/health").read()) == {"status": "ok"}


def test_lists_the_five_tools_over_streamable_http(base_url):
    listed = _with_session(base_url, lambda session: session.list_tools())
    assert sorted(t.name for t in listed.tools) == sorted(TOOLS)


@pytest.mark.parametrize("tool", TOOLS)
def test_valid_example_input_returns_a_contract_valid_output(base_url, tool):
    contract = _load(CONTRACTS / "mcp" / f"{tool}.schema.json")
    arguments = _load(CONTRACTS / "examples" / f"{tool}.input.valid.json")
    result = call(base_url, tool, arguments)
    assert not result.isError
    validator = Draft202012Validator(contract["outputSchema"], format_checker=FormatChecker())
    validator.validate(result.structuredContent)


@pytest.mark.parametrize("tool", TOOLS)
def test_invalid_example_input_returns_invalid_input_error(base_url, tool):
    arguments = _load(CONTRACTS / "examples" / f"{tool}.input.invalid.json")
    result = call(base_url, tool, arguments)
    assert result.isError
    error = json.loads(result.content[0].text)
    Draft202012Validator(_load(CONTRACTS / "mcp" / "catalog_error.schema.json")).validate(error)
    assert error["error_code"] == "INVALID_INPUT"


def test_not_found_travels_as_a_contract_error(base_url):
    result = call(base_url, "get_availability", {"property_id": "no_existe"})
    assert result.isError
    assert json.loads(result.content[0].text)["error_code"] == "NOT_FOUND"

def test_apartado_case_is_served(base_url):
    result = call(base_url, "get_availability", {"property_id": "prop_00234"})
    assert not result.isError, f"El servidor MCP devolvió un error inesperado: {result.content}"
    assert result.structuredContent["status"] == "apartado"

def test_exact_address_needs_a_qualified_lead(base_url):
    hidden = call(base_url, "get_property", {"property_id": "prop_00231", "lead_qualified": False})
    shown = call(base_url, "get_property", {"property_id": "prop_00231", "lead_qualified": True})
    
    assert not hidden.isError, f"Falló al obtener propiedad (oculta): {hidden.content}"
    assert not shown.isError, f"Falló al obtener propiedad (visible): {shown.content}"
    
    assert hidden.structuredContent["address_exact"] is None
    assert shown.structuredContent["address_exact"] is not None
