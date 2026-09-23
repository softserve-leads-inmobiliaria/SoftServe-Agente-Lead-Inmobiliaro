"""Tests de OBS-1 de punta a punta: `core` real exporta sus trazas por OTLP/HTTP (el protocolo de Phoenix).

Un receptor mínimo en un hilo hace de Phoenix: recoge lo que `core` envía y lo decodifica. No
necesita Docker. Sirve también para revisar qué spans salen de verdad, no solo los manuales.
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))

from replay import http_responder, replay  # noqa: E402
from test_e2e import _free_port, _start  # noqa: E402

from db.testing import temporary_database  # noqa: E402


class Collector:
    """Recibe POST /v1/traces y guarda los spans decodificados junto con el cuerpo crudo."""

    def __init__(self):
        self.spans, self.resources, self.raw = [], [], []
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                request = ExportTraceServiceRequest()
                request.ParseFromString(body)
                collector.raw.append(body)
                for resource_spans in request.resource_spans:
                    collector.resources.append({a.key: a.value.string_value for a in resource_spans.resource.attributes})
                    for scope_spans in resource_spans.scope_spans:
                        collector.spans.extend(scope_spans.spans)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.end_headers()
                self.wfile.write(b"")

            def log_message(self, *args):
                pass

        self.port = _free_port()
        self.server = HTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def wait_for(self, name, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(s.name == name for s in self.spans):
                return True
            time.sleep(0.1)
        return False

    def names(self):
        return [s.name for s in self.spans]

    def close(self):
        self.server.shutdown()


@pytest.fixture(scope="module")
def traced_stack():
    from test_e2e import _seed_catalog

    collector = Collector()
    with temporary_database() as database_url:
        _seed_catalog(database_url)
        catalog_port, core_port = _free_port(), _free_port()
        catalog = _start("catalog", catalog_port, {"DATABASE_URL": database_url})
        core = _start("core", core_port, {
            "CATALOG_SERVICE_URL": f"http://127.0.0.1:{catalog_port}", "DATABASE_URL": database_url,
            "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{collector.port}",
            "OTEL_BSP_SCHEDULE_DELAY": "200"})
        yield f"http://127.0.0.1:{core_port}", collector
        for process in (core, catalog):
            process.terminate()
            process.wait(timeout=10)
    collector.close()


def test_a_real_turn_exports_its_trace_over_otlp(traced_stack, tmp_path):
    core_url, collector = traced_stack
    collector.spans.clear()
    script = tmp_path / "traza.jsonl"
    script.write_text(json.dumps({"text": "Busco un departamento, mi presupuesto es 3 millones"}) + chr(10),
                      encoding="utf-8")
    replay(script, http_responder(core_url))

    assert collector.wait_for("turn"), collector.names()
    time.sleep(0.6)
    # Revisada la primera traza real: salen exactamente los spans manuales. El instrumentador de MCP
    # solo propaga el contexto y no añade ninguno; si eso cambia, esta línea avisa.
    # A-4 añade una consulta de disponibilidad por cada propiedad que el listado da por disponible.
    names = collector.names()
    assert sorted(set(names)) == ["catalog.get_availability", "catalog.search_properties", "fsm.advance",
                                  "guardrail.input", "turn"]
    assert [names.count(n) for n in ("catalog.search_properties", "fsm.advance", "guardrail.input", "turn")] == [1] * 4
    assert {"service.name": "core", "openinference.project.name": "lead-agent"}.items() <= collector.resources[0].items()


def test_the_exported_trace_is_one_tree_rooted_at_the_turn(traced_stack, tmp_path):
    core_url, collector = traced_stack
    collector.spans.clear()
    script = tmp_path / "arbol.jsonl"
    script.write_text(json.dumps({"text": "Hola, buenas tardes"}) + chr(10), encoding="utf-8")
    replay(script, http_responder(core_url))
    assert collector.wait_for("turn")
    time.sleep(0.6)
    turn = next(s for s in collector.spans if s.name == "turn")
    assert not turn.parent_span_id
    children = {s.name for s in collector.spans if s.parent_span_id == turn.span_id}
    assert {"guardrail.input", "fsm.advance"} <= children
    assert {s.trace_id for s in collector.spans if s.name in ("guardrail.input", "fsm.advance")} == {turn.trace_id}


def test_what_the_lead_wrote_never_reaches_the_collector(traced_stack, tmp_path):
    core_url, collector = traced_stack
    secret = "Busco casa en la colonia Secretísima, mi tarjeta es 4111 1111 1111 1111"
    script = tmp_path / "secreto.jsonl"
    script.write_text(json.dumps({"text": secret}) + chr(10), encoding="utf-8")
    replay(script, http_responder(core_url))
    time.sleep(1.0)
    everything = b"".join(collector.raw)
    assert everything
    for fragment in ("Secretísima", "4111", "tarjeta es", "colonia"):
        assert fragment.encode("utf-8") not in everything, fragment
