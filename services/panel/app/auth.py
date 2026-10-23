"""Login del panel con usuario y contraseña (B-17, DT-72; reemplaza al token en la URL de DT-5).

La sesión es una cookie firmada (`SessionMiddleware` de Starlette) con el id del `PanelUser` y su token
CSRF. Lo que no corresponde al rol o al asesor de la sesión sigue respondiendo 404, nunca 401/403: un
código distinto revelaría si un lead o una vista existen (DT-5, que DT-72 conserva).
"""
from __future__ import annotations

import hmac
import os
import secrets
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import PanelUser
from db.passwords import hash_password, verify_password

SESSION_MAX_AGE = 8 * 60 * 60  # 8 horas desde el login; no se renueva con el uso
# El valor de ejemplo de docker-compose.yml y .env.example: solo vale con PANEL_DEMO=1 (config/panel.env).
EXAMPLE_SESSION_SECRET = "demo-session-secret-change-me"
# Para que un usuario inexistente tarde lo mismo que una contraseña incorrecta.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


class InvalidSessionSecret(RuntimeError):
    """`PANEL_SESSION_SECRET` falta, o es el de ejemplo fuera de la demo: el panel no arranca."""


def session_secret_from_env(environ: Mapping[str, str] = os.environ) -> str:
    secret = environ.get("PANEL_SESSION_SECRET", "").strip()
    if not secret:
        raise InvalidSessionSecret("Falta PANEL_SESSION_SECRET: sin él no se pueden firmar las sesiones del panel")
    if secret == EXAMPLE_SESSION_SECRET and environ.get("PANEL_DEMO", "").strip() != "1":
        raise InvalidSessionSecret("PANEL_SESSION_SECRET es el valor de ejemplo: fuera de la demo (PANEL_DEMO=1) "
                                   "hay que poner uno propio en .env")
    return secret


def authenticate(session: Session, username: str, password: str) -> PanelUser | None:
    """El usuario si la contraseña es la suya; None si no existe o no coincide (quien llama no distingue)."""
    user = session.execute(select(PanelUser).where(PanelUser.username == username.strip())).scalar_one_or_none()
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(session_data: Mapping[str, Any], sent: str) -> bool:
    expected = session_data.get("csrf")
    # En bytes: `compare_digest` con str rechaza lo que no es ASCII, y `sent` viene del formulario.
    return isinstance(expected, str) and bool(sent) and hmac.compare_digest(expected.encode(), sent.encode())
