"""Engine y sesiones compartidos. Cada servicio abre sus sesiones desde aquí."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.settings import database_url


@lru_cache
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Una transacción: commit al salir sin error, rollback si hubo excepción."""
    session = sessionmaker(engine or get_engine(), expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
