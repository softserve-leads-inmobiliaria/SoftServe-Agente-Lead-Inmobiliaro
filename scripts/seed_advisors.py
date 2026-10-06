"""Siembra los asesores de demo (B-5): sin ellos el panel no se puede abrir ni B-7 notificar.

Alcance deliberadamente mínimo: solo `advisors`. El inventario de propiedades y promociones es de
`C-9`, que todavía no está implementada — este script no lo toca para no chocar con esa historia.

Idempotente: se puede correr en cada `docker compose up` sin duplicar ni pisar tokens ya emitidos.
Los nombres y zonas salen del catálogo falso para que el panel y el catálogo hablen de la misma
gente; `Advisor.zone` es una sola zona, así que se toma la primera de la lista del catálogo.

Uso:  python scripts/seed_advisors.py
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.models import Advisor  # noqa: E402
from db.session import get_engine, session_scope  # noqa: E402

# Mismos ids, nombres y zonas que `services/catalog/app/data.py:ADVISORS`.
ADVISORS = [
    {"id": "adv_001", "name": "Valeria Ortiz", "zone": "El Poblado"},
    {"id": "adv_002", "name": "Andrés Núñez", "zone": "Usaquen"},
    {"id": "adv_003", "name": "Camila Reyes", "zone": "Envigado"},
]


def seed(engine=None) -> list[Advisor]:
    """Inserta los asesores que falten y devuelve los tres tal como quedaron en la base."""
    with session_scope(engine or get_engine()) as session:
        for advisor in ADVISORS:
            session.execute(
                # Hex y no urlsafe: sin `-`/`_` (rompen el deep-link de Telegram en algunos clientes)
                # y sin caracteres confundibles (l/I, O/0), porque el asesor puede tener que teclearlo.
                insert(Advisor)
                .values(access_token=secrets.token_hex(16), **advisor)
                .on_conflict_do_nothing(index_elements=["id"]))
        session.flush()
        return [session.get(Advisor, advisor["id"]) for advisor in ADVISORS]


def main() -> None:
    for advisor in seed():
        # El token es la sesión del asesor (DT-5): se imprime para poder abrir el panel en la demo.
        # El deep-link registra su chat de Telegram para recibir la tarjeta del lead (B-7).
        print(f"{advisor.id}  {advisor.name:<15} {advisor.zone:<15} /panel/{advisor.access_token}"
              f"   registro: ?start=adv_{advisor.access_token}")


if __name__ == "__main__":
    main()
