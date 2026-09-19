"""Configuración compartida leída del entorno."""
from __future__ import annotations

import os


def database_url() -> str:
    """URL de SQLAlchemy (driver psycopg 3). `DATABASE_URL` manda; si no, se arma con POSTGRES_*.

    Los defaults son los de docker-compose.yml, así que contra `docker compose up` funciona sin
    configurar nada. Dentro de Docker, POSTGRES_HOST=postgres (lo define el compose).
    """
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    user = os.environ.get("POSTGRES_USER", "leads")
    password = os.environ.get("POSTGRES_PASSWORD", "leads_dev_password")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    name = os.environ.get("POSTGRES_DB", "leads")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"
