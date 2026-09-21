"""CT-6: el docker-compose base cumple lo que la historia y DT-3/DT-4 exigen."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]


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

