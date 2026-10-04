"""CT-6: el docker-compose base cumple lo que la historia y DT-3/DT-4 exigen."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
CONFIG_FILES = sorted((ROOT / "config").glob("*.env"))
# Nombres que delatan un secreto: nunca van en config/, que está versionado (#174).
SECRET_HINTS = ("TOKEN", "PASSWORD", "SECRET", "KEY", "AWS_")


def _assignments(path: Path) -> dict[str, str]:
    """Las líneas CLAVE=valor de un archivo de entorno, sin comentarios ni líneas vacías."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))


def test_base_stack_has_postgres_observability_and_panel():
    assert {"postgres", "phoenix", "panel"} <= set(SERVICES)


def test_no_redis_anywhere():
    assert not any("redis" in name.lower() or "redis" in str(svc.get("image", "")).lower()
                   for name, svc in SERVICES.items())


def test_postgres_uses_pgvector_image_with_healthcheck_and_volume():
    pg = SERVICES["postgres"]
    assert "pgvector" in pg["image"]
    assert "healthcheck" in pg
    assert any(v.startswith("pgdata:") for v in pg["volumes"])


@pytest.mark.parametrize("name", ["postgres", "phoenix"])
def test_images_are_pinned_not_latest(name):
    image = SERVICES[name]["image"]
    assert ":" in image and not image.endswith(":latest")


def test_panel_builds_from_repo_root_and_waits_for_healthy_postgres():
    panel = SERVICES["panel"]
    assert panel["build"]["context"] == "."
    assert (ROOT / panel["build"]["dockerfile"]).is_file()
    assert panel["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_env_vars_have_defaults_so_up_works_without_dotenv():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    bare = re.findall(r"\$\{[A-Z_]+\}", text)
    assert bare == []


def test_env_example_documents_every_compose_variable():
    used = set(re.findall(r"\$\{([A-Z_]+):-", (ROOT / "docker-compose.yml").read_text(encoding="utf-8")))
    documented = {
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    }
    assert used <= documented


def test_each_service_config_file_exists():
    """#174: los ajustes del producto van en config/<servicio>.env, cargado con env_file."""
    referenced = {SERVICES[name]["env_file"] for name in SERVICES if "env_file" in SERVICES[name]}
    assert referenced == {"config/core.env", "config/panel.env"}
    assert {f"config/{path.name}" for path in CONFIG_FILES} == referenced


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: p.name)
def test_config_values_keep_a_default_and_can_be_overridden(path):
    """Cada valor es `${CLAVE:-valor}` con su misma CLAVE: sale de este archivo salvo que `.env` o la shell
    la pisen (Compose interpola los env_file)."""
    for name, value in _assignments(path).items():
        assert re.fullmatch(rf"\$\{{{name}:-[^}}]+\}}", value), f"{name}={value}"


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: p.name)
def test_config_files_hold_no_secrets(path):
    for name in _assignments(path):
        assert not any(hint in name for hint in SECRET_HINTS), name


def test_each_variable_lives_in_one_place():
    """Un ajuste en config/ no se repite en .env.example ni en `environment:`, que lo pisaría sin avisar."""
    in_example = set(_assignments(ROOT / ".env.example"))
    for path in CONFIG_FILES:
        in_config = set(_assignments(path))
        service = SERVICES[path.stem]
        assert not in_config & in_example
        assert not in_config & set(service.get("environment", {}))


def test_e2e_skeleton_services_are_wired_in_order():
    """CT-4: harness -> core -> catalog, cada uno esperando a que el siguiente esté sano."""
    assert {"catalog", "core", "harness"} <= set(SERVICES)
    for name in ("catalog", "core", "harness"):
        build = SERVICES[name]["build"]
        assert build["context"] == "."
        assert (ROOT / build["dockerfile"]).is_file()
    assert SERVICES["core"]["depends_on"]["catalog"]["condition"] == "service_healthy"
    assert SERVICES["harness"]["depends_on"]["core"]["condition"] == "service_healthy"
    assert SERVICES["core"]["environment"]["CATALOG_SERVICE_URL"] == "http://catalog:8000"
    assert SERVICES["harness"]["environment"]["CORE_URL"] == "http://core:8000"


def test_harness_runs_once_and_does_not_restart():
    assert SERVICES["harness"]["restart"] == "no"


def test_migrate_runs_alembic_after_postgres_is_healthy():
    """C-3: las migraciones se aplican solas con `docker compose up`."""
    migrate = SERVICES["migrate"]
    assert (ROOT / migrate["build"]["dockerfile"]).is_file()
    assert migrate["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert migrate["environment"]["POSTGRES_HOST"] == "postgres"
    assert migrate["restart"] == "no"


def test_core_waits_for_the_migrations_and_can_reach_postgres():
    """A-1: el estado de la FSM vive en Postgres, así que core espera al esquema migrado."""
    core = SERVICES["core"]
    assert core["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert core["environment"]["POSTGRES_HOST"] == "postgres"


def test_core_sends_its_traces_to_phoenix_without_depending_on_it():
    """OBS-1: las trazas van a Phoenix por OTLP, pero un Phoenix caído no debe tumbar al bot."""
    core = SERVICES["core"]
    assert core["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://phoenix:6006"
    assert "phoenix" not in core["depends_on"]
    assert "phoenix" in SERVICES


def test_message_content_is_kept_out_of_the_traces_by_default():
    assert SERVICES["core"]["environment"]["TRACE_CONTENT"] == "${TRACE_CONTENT:-}"


def test_core_uses_the_fake_llm_unless_told_otherwise():
    """A-2: sin credenciales el stack sigue funcionando; el LLM real se activa por variable de entorno."""
    env = SERVICES["core"]["environment"]
    assert env["LLM_PROVIDER"] == "${LLM_PROVIDER:-fake}"
    assert _assignments(ROOT / "config" / "core.env")["LLM_RATE_LIMIT_PER_MINUTE"] == "${LLM_RATE_LIMIT_PER_MINUTE:-10}"
    for name in ("BEDROCK_MODEL_ID_CHAT", "BEDROCK_MODEL_ID_CLASSIFIER", "AWS_ACCESS_KEY_ID"):
        assert env[name].endswith(":-}")  # sin valor por defecto: nunca hay un ID ni una credencial en el repo



OWN_SERVICES = ["panel", "migrate", "seed", "catalog", "core", "harness"]


@pytest.mark.parametrize("name", [n for n, svc in SERVICES.items() if "ports" in svc])
def test_ports_are_published_only_on_the_bind_address(name):
    """#104: por defecto solo esta máquina llega a la base, al core y al panel, no toda la red local."""
    for port in SERVICES[name]["ports"]:
        assert port.startswith("${BIND_ADDRESS:-127.0.0.1}:"), port


@pytest.mark.parametrize("name", OWN_SERVICES)
def test_own_containers_run_with_least_privilege(name):
    """#104: sin capacidades del kernel, sin ganar privilegios y con el sistema de archivos de solo lectura."""
    svc = SERVICES[name]
    assert "build" in svc
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["read_only"] is True
    assert "/tmp" in svc["tmpfs"]


def test_every_image_built_here_is_hardened():
    assert sorted(n for n, svc in SERVICES.items() if "build" in svc) == sorted(OWN_SERVICES)
