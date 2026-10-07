"""Tests de C-3 que no necesitan base de datos: el esquema declarado coincide con diseño y contratos."""
import json

import pytest

from db import models
from db.repo_root import repo_root

CONTRACT = json.loads((repo_root() / "contracts" / "fsm_state.schema.json").read_text(encoding="utf-8"))
DEFS = CONTRACT["$defs"]

# design.md §1: las 16 entidades, más el contexto de la FSM (DT-11).
EXPECTED_TABLES = {
    "leads", "conversations", "messages", "lead_scores", "properties", "promotions", "advisors",
    "visits", "advisor_notifications", "faq_entries", "funnel_events", "audit_log", "fsm_context",
    "property_status_events", "deals", "property_interest", "unmet_searches", "scheduled_jobs", "property_alerts",
}


def test_every_design_entity_has_a_table():
    assert set(models.Base.metadata.tables) == EXPECTED_TABLES


def test_fsm_states_match_the_contract_enum():
    assert set(models.FSM_STATES) == set(DEFS["FsmStateName"]["enum"])


def test_score_bands_match_the_contract_enum():
    assert set(models.SCORE_BANDS) == set(DEFS["LeadScore"]["properties"]["band"]["enum"])


def test_fsm_state_columns_cover_the_contract():
    """FsmState = Conversation.fsm_state + fsm_context + lead_scores (DT-11)."""
    tables = models.Base.metadata.tables
    persisted = (set(tables["fsm_context"].columns.keys()) | {"fsm_state"}) - {"updated_at"}
    assert set(CONTRACT["required"]) - {"score", "lead_id"} <= persisted | {"conversation_id"}
    score_fields = set(DEFS["LeadScore"]["required"]) - {"lead_id"}
    assert score_fields <= set(tables["lead_scores"].columns.keys())


def test_conversation_columns_match_the_contract_projection():
    contract_fields = set(DEFS["Conversation"]["required"])
    assert contract_fields == set(models.Base.metadata.tables["conversations"].columns.keys())


def test_lead_scores_use_the_same_dimensions_as_bant_e():
    columns = models.Base.metadata.tables["lead_scores"].columns.keys()
    assert {"need_fit", "budget", "authority", "timing", "eligibility", "total", "band", "reasons"} <= set(columns)


@pytest.mark.parametrize("table,column", [
    ("conversations", "lead_id"), ("messages", "conversation_id"), ("visits", "lead_id"),
    ("visits", "property_id"), ("visits", "advisor_id"), ("promotions", "property_id"),
    ("lead_scores", "lead_id"), ("fsm_context", "conversation_id"), ("funnel_events", "lead_id"),
    ("property_status_events", "property_id"), ("deals", "property_id"), ("deals", "lead_id"),
    ("deals", "advisor_id"), ("property_interest", "lead_id"), ("property_interest", "property_id"),
])
def test_foreign_keys_exist(table, column):
    assert models.Base.metadata.tables[table].columns[column].foreign_keys


def test_property_ids_follow_the_catalog_contract_format():
    assert str(models.Base.metadata.tables["properties"].columns["id"].type) == "VARCHAR"


def test_reserved_and_consent_fields_are_modelled():
    leads = models.Base.metadata.tables["leads"].columns
    assert {"consent_status", "phone", "email", "telegram_user_id"} <= set(leads.keys())
    assert "address_exact" in models.Base.metadata.tables["properties"].columns


def test_commercial_intelligence_rows_can_be_marked_synthetic():
    """N-9 siembra historia sintética y la borra sin tocar datos reales (N-1)."""
    for table in ("leads", "property_status_events", "deals", "property_interest", "unmet_searches"):
        column = models.Base.metadata.tables[table].columns["synthetic"]
        assert not column.nullable and column.server_default is not None


def test_unmet_searches_never_store_the_lead():
    """Una búsqueda sin resultados guarda los filtros, nunca quién buscó ni qué escribió (N-5)."""
    columns = set(models.Base.metadata.tables["unmet_searches"].columns.keys())
    assert columns == {"id", "zone", "type", "kind", "bedrooms", "budget_min", "budget_max", "created_at",
                       "synthetic"}


def test_property_statuses_do_not_grow_with_deals():
    """Una propiedad colocada queda en `cerrado`; si fue renta o venta lo dice `deals` (N-1)."""
    assert models.PROPERTY_STATUSES == ("disponible", "apartado", "cerrado")
    assert set(models.DEAL_KINDS) == {"renta", "venta"}


def test_settings_build_the_compose_default_url(monkeypatch):
    from db.settings import database_url

    for name in ("DATABASE_URL", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST",
                 "POSTGRES_PORT", "POSTGRES_DB"):
        monkeypatch.delenv(name, raising=False)
    assert database_url() == "postgresql+psycopg://leads:leads_dev_password@127.0.0.1:5432/leads"
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    assert "@postgres:5432/" in database_url()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x@y/z")
    assert database_url() == "postgresql+psycopg://x@y/z"
