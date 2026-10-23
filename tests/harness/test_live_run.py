"""Tests de OBS-6: la corrida real (`harness/scripts/live_run.py`) contra core, catalog y panel de verdad.

Los tres servicios corren como procesos uvicorn reales (lo mismo que `test_e2e.py`) sobre una base
temporal con el catálogo y los asesores sembrados. La herramienta de la corrida real se prueba contra
la red real y no contra mocks: en v1 un arnés con tests mockeados en verde tenía dos bugs que solo
aparecían contra los servicios (OBS-6, notas de diseño).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))

import live_run  # noqa: E402
from test_e2e import _free_port, _seed_catalog, _start  # noqa: E402

from db.testing import temporary_database  # noqa: E402


def _seed_advisors(url: str):
    from sqlalchemy import create_engine

    spec = importlib.util.spec_from_file_location("seed_advisors", ROOT / "scripts" / "seed_advisors.py")
    seed_advisors = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_advisors)
    engine = create_engine(url)
    try:
        seed_advisors.seed(engine)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def three_services():
    with temporary_database() as database_url:
        _seed_catalog(database_url)
        _seed_advisors(database_url)
        catalog_port, core_port, panel_port = _free_port(), _free_port(), _free_port()
        catalog = _start("catalog", catalog_port, {"DATABASE_URL": database_url})
        core = _start("core", core_port, {"CATALOG_SERVICE_URL": f"http://127.0.0.1:{catalog_port}",
                                          "DATABASE_URL": database_url})
        panel = _start("panel", panel_port, {"DATABASE_URL": database_url,
                                             "PANEL_SESSION_SECRET": "live-run-tests-secret"})
        yield {"core": f"http://127.0.0.1:{core_port}", "panel": f"http://127.0.0.1:{panel_port}",
               "database": database_url}
        for process in (panel, core, catalog):
            process.terminate()
            process.wait(timeout=10)


def _run(services, report, *extra):
    return live_run.main(["--core-url", services["core"], "--panel-url", services["panel"],
                          "--database-url", services["database"], "--informe", str(report), *extra])


def test_live_run_is_green_against_the_three_services_and_can_be_repeated(three_services, tmp_path):
    """Dos corridas seguidas sobre la misma base: el `run_id` evita heredar conversaciones terminadas."""
    for attempt in ("primera", "segunda"):
        report = tmp_path / f"{attempt}.md"
        assert _run(three_services, report) == 0, report.read_text(encoding="utf-8")
        content = report.read_text(encoding="utf-8")
        assert "**8/8 guiones en verde**" in content
        # El panel se comprobó de verdad: al menos un lead llegó a la cola de un asesor.
        assert "en la cola de" in content and "ficha con la transcripción" in content


def test_a_wrong_final_state_is_caught_in_postgres_and_in_the_panel(three_services, tmp_path, monkeypatch):
    monkeypatch.setitem(live_run.EXPECTED_FINAL_STATE, "deep_link_y_visita", "DISCOVERY")
    report = tmp_path / "informe.md"
    assert _run(three_services, report) == 1
    content = report.read_text(encoding="utf-8")
    assert "**7/8 guiones en verde**" in content
    assert "estado final 'HUMAN_HANDOFF' en Postgres, se esperaba 'DISCOVERY'" in content
    assert "la cola no muestra la etapa DISCOVERY" in content


def test_a_service_that_is_down_fails_the_run_with_a_report(three_services, tmp_path):
    report = tmp_path / "informe.md"
    services = three_services | {"panel": f"http://127.0.0.1:{_free_port()}"}
    assert _run(services, report) == 1
    assert "panel no responde" in report.read_text(encoding="utf-8")
