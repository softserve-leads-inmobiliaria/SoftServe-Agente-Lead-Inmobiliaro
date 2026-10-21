"""Hash de las contraseñas del panel (B-17, DT-72): scrypt de la biblioteca estándar, con sal por usuario.

Vive en `db/` porque lo usan el seed (imagen de `db/`) y el panel. Formato guardado:
`scrypt$<n>$<r>$<p>$<sal base64>$<hash base64>`, así un cambio de parámetros no invalida los hashes viejos.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_N, _R, _P, _DKLEN = 2**14, 8, 1, 64
_SALT_BYTES = 16
# scrypt usa 128·n·r bytes (16 MiB con estos parámetros); el margen evita el límite por defecto de OpenSSL.
_MAXMEM = 64 * 1024 * 1024


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_DKLEN, maxmem=_MAXMEM)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    """El hash a guardar en `panel_users.password_hash`. Dos llamadas con la misma contraseña difieren."""
    salt = secrets.token_bytes(_SALT_BYTES)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(_scrypt(password, salt, _N, _R, _P))}"


def verify_password(password: str, stored: str) -> bool:
    """True si `password` corresponde a `stored`. Un hash con formato inválido nunca valida."""
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except ValueError:
        return False
