"""Auth del panel por token estático en la URL (DT-5).

No hay login ni sesiones: el token *es* la sesión. Todo lo que no corresponde al asesor del token
responde 404, nunca 401/403 — un código distinto revelaría si un token con formato válido existe.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Advisor


def resolve_advisor(session: Session, token: str) -> Advisor | None:
    """El asesor dueño del token, o None si no existe. Quien llama traduce None a 404."""
    if not token:
        return None
    return session.execute(select(Advisor).where(Advisor.access_token == token)).scalar_one_or_none()
