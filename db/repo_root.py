"""Único lugar que resuelve la raíz del repo (checkout local o imagen Docker).

Todo servicio que necesite `contracts/` u otra carpeta de la raíz importa `repo_root()` desde
aquí; nunca reimplementa la resolución de ruta ni indexa `Path.parents[N]` a mano.
"""
from __future__ import annotations

import os
from pathlib import Path

_HERE = Path(__file__).resolve()
# Este archivo está en <raíz>/db/, así que la raíz es parents[1] tanto en el checkout como en la
# imagen Docker (misma disposición). El resto son respaldo por si la carpeta se mueve.
_CANDIDATE_INDEXES = (1, 2, 3)
_MARKER = "contracts"


def repo_root() -> Path:
    override = os.environ.get("REPO_ROOT")
    if override:
        return Path(override).resolve()
    # Los índices se filtran ANTES de usarlos: un índice fuera de rango no debe lanzar IndexError.
    candidates = [_HERE.parents[i] for i in _CANDIDATE_INDEXES if i < len(_HERE.parents)]
    for candidate in candidates:
        if (candidate / _MARKER).is_dir():
            return candidate
    raise RuntimeError(
        f"No se encontró la raíz del repo (carpeta con '{_MARKER}/') desde {_HERE}; "
        "definí REPO_ROOT si el layout es distinto."
    )
