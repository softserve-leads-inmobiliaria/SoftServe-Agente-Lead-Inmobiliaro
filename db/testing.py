"""Ayudas para tests que necesitan un Postgres real: una base temporal ya migrada.

Solo lo importan los tests (requiere pytest); ningún servicio lo usa en ejecución. Sin Postgres
alcanzable los tests se omiten; con `REQUIRE_DB=1` (CI) fallan en lugar de omitirse.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from db.repo_root import repo_root
from db.settings import database_url

ROOT = repo_root()


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "db" / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _admin_engine():
    return create_engine(make_url(database_url()).set(database="postgres"), isolation_level="AUTOCOMMIT")


@contextmanager
def temporary_database(migrate: bool = True) -> Iterator[str]:
    """Crea una base temporal (migrada a `head` si `migrate`) y la borra al salir. Rinde su URL."""
    admin = _admin_engine()
    try:
        with admin.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        admin.dispose()
        if os.environ.get("REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Postgres no disponible ({type(exc).__name__}); levantarlo con "
                    "`docker compose up -d postgres`")
    name = f"leads_test_{uuid.uuid4().hex[:10]}"
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(database_url()).set(database=name).render_as_string(hide_password=False)
    try:
        if migrate:
            command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
