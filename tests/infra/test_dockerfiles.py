"""#65: las imágenes propias comparten el mismo endurecimiento.

CI no construye las imágenes (instala `requirements-dev.txt`), así que esto se comprueba leyendo los
Dockerfiles. Que el contenedor de verdad no corre como root se verifica con Docker: ver `docs/TESTING.md`.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
DOCKERFILES = sorted({svc["build"]["dockerfile"] for svc in COMPOSE["services"].values() if "build" in svc})
SERVERS = {"services/core/Dockerfile": 8000, "services/catalog/Dockerfile": 8000, "services/panel/Dockerfile": 8080}


def instructions(dockerfile):
    """Instrucciones del Dockerfile, con las líneas partidas por `\\` ya unidas y sin comentarios."""
    text = (ROOT / dockerfile).read_text(encoding="utf-8").replace("\\\n", " ")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_every_image_built_by_the_compose_is_covered():
    assert set(SERVERS) < set(DOCKERFILES)
    assert {"harness/Dockerfile", "db/Dockerfile"} < set(DOCKERFILES)


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_base_image_is_pinned_to_an_exact_python_version(dockerfile):
    base = [i for i in instructions(dockerfile) if i.startswith("FROM ")]
    assert len(base) == 1
    assert re.fullmatch(r"FROM python:\d+\.\d+\.\d+-slim-\w+(@sha256:[0-9a-f]{64})?", base[0]), base[0]


def test_all_images_share_the_same_base():
    bases = {next(i for i in instructions(d) if i.startswith("FROM ")) for d in DOCKERFILES}
    assert len(bases) == 1, bases


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_the_process_does_not_run_as_root(dockerfile):
    lines = instructions(dockerfile)
    users = [i.split()[1] for i in lines if i.startswith("USER ")]
    assert users, "sin USER, el contenedor corre como root"
    assert users[-1] not in {"root", "0", "0:0"}
    # El último USER tiene que ir después de todo lo que necesita root (pip, useradd) y antes del CMD.
    last_user = max(n for n, i in enumerate(lines) if i.startswith("USER "))
    assert all(n < last_user for n, i in enumerate(lines) if i.startswith("RUN "))
    assert last_user < max(n for n, i in enumerate(lines) if i.startswith("CMD "))


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_pip_is_removed_after_installing(dockerfile):
    """#104: pip no se usa en ejecución y es lo único de Python con vulnerabilidades conocidas (Trivy)."""
    installs = [i for i in instructions(dockerfile) if i.startswith("RUN ") and "pip install" in i]
    assert len(installs) == 1
    assert installs[0].rstrip().endswith("&& pip uninstall --yes pip"), installs[0]
    # La continuación tiene que ser una barra al final de la línea física, no un "\n" escrito a mano.
    lines = (ROOT / dockerfile).read_text(encoding="utf-8").splitlines()
    assert any(line.strip() == "&& pip uninstall --yes pip" for line in lines)


@pytest.mark.parametrize("dockerfile, port", SERVERS.items())
def test_servers_carry_their_own_healthcheck(dockerfile, port):
    checks = [i for i in instructions(dockerfile) if i.startswith("HEALTHCHECK ")]
    assert len(checks) == 1
    assert f"http://localhost:{port}/health" in checks[0]


def test_compose_does_not_repeat_the_image_healthchecks():
    """Una sola fuente: si el compose redefine el healthcheck, el del Dockerfile deja de valer."""
    for name, svc in COMPOSE["services"].items():
        if svc.get("build", {}).get("dockerfile") in SERVERS:
            assert "healthcheck" not in svc, name


def test_tests_are_kept_out_of_the_images():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("tests/", "**/tests/", "**/conftest.py"):
        assert pattern in ignored


@pytest.mark.parametrize("service", ["core", "catalog", "panel"])
def test_log_level_reaches_the_services_that_log_json(service):
    """OBS-7: `db/logging_config.py` lee `LOG_LEVEL`; el compose tiene que pasarla."""
    assert COMPOSE["services"][service]["environment"]["LOG_LEVEL"] == "${LOG_LEVEL:-INFO}"
