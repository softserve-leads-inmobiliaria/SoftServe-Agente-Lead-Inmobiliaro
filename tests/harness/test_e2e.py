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
def stack():
    catalog_port, core_port = _free_port(), _free_port()
    catalog = _start("catalog", catalog_port)
    core = _start("core", core_port, {"CATALOG_SERVICE_URL": f"http://127.0.0.1:{catalog_port}"})
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


def test_core_answers_503_when_the_catalog_is_down():
    core_port = _free_port()
    core = _start("core", core_port, {"CATALOG_SERVICE_URL": f"http://127.0.0.1:{_free_port()}"})
    try:
        (event,) = build_events("caido", [{"text": "Busco casa"}])
        with pytest.raises(urllib.error.HTTPError) as exc:
            http_responder(f"http://127.0.0.1:{core_port}")(event)
        assert exc.value.code == 503
    finally:
        core.terminate()
        core.wait(timeout=10)
