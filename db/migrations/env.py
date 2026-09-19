"""Entorno de Alembic: usa los modelos de db/models.py y la URL de db/settings.py."""
from alembic import context
from sqlalchemy import create_engine

from db.models import Base
from db.settings import database_url

target_metadata = Base.metadata


def run_migrations() -> None:
    # Los tests inyectan su propia URL con config.set_main_option("sqlalchemy.url", ...).
    url = context.config.get_main_option("sqlalchemy.url") or database_url()
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, compare_server_default=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
