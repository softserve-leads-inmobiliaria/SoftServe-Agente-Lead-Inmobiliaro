"""Siembra los asesores de demo (B-5) y los usuarios del panel (B-17): sin ellos no hay panel ni avisos (B-7).

Alcance deliberadamente mínimo: solo `advisors`. El inventario de propiedades y promociones es de
`C-9`, que todavía no está implementada — este script no lo toca para no chocar con esa historia.

Idempotente: se puede correr en cada `docker compose up` sin duplicar ni pisar tokens ya emitidos.
Los usuarios del panel (uno por asesor y un `gerente`, DT-72) sí se reescriben: así una contraseña nueva en
`.env` (`PANEL_ADVISOR_PASSWORD`, `PANEL_MANAGER_PASSWORD`) vale desde el próximo seed. Solo se guarda su hash.
Los nombres y zonas salen del catálogo falso para que el panel y el catálogo hablen de la misma
gente; `Advisor.zone` es una sola zona, así que se toma la primera de la lista del catálogo.

Uso:  python scripts/seed_advisors.py
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.models import Advisor, PanelUser  # noqa: E402
from db.passwords import hash_password  # noqa: E402
from db.session import get_engine, session_scope  # noqa: E402

# Mismos ids, nombres y zonas que `services/catalog/app/data.py:ADVISORS`.
ADVISORS = [
    {"id": "adv_001", "name": "Valeria Ortiz", "zone": "El Poblado"},
    {"id": "adv_002", "name": "Andrés Núñez", "zone": "Usaquen"},
    {"id": "adv_003", "name": "Camila Reyes", "zone": "Envigado"},
]

# Usuarios del panel (B-17). Las contraseñas de demo son los defaults de docker-compose.yml; se pisan en `.env`.
ADVISOR_USERNAMES = {"adv_001": "valeria", "adv_002": "andres", "adv_003": "camila"}
MANAGER_USERNAME = "gerente"
DEMO_ADVISOR_PASSWORD = "asesor-demo"
DEMO_MANAGER_PASSWORD = "gerente-demo"


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
        _seed_panel_users(session)
        return [session.get(Advisor, advisor["id"]) for advisor in ADVISORS]


def _seed_panel_users(session) -> None:
    advisor_password = os.environ.get("PANEL_ADVISOR_PASSWORD") or DEMO_ADVISOR_PASSWORD
    manager_password = os.environ.get("PANEL_MANAGER_PASSWORD") or DEMO_MANAGER_PASSWORD
    users = [{"username": username, "role": "asesor", "advisor_id": advisor_id, "password": advisor_password}
             for advisor_id, username in ADVISOR_USERNAMES.items()]
    users.append({"username": MANAGER_USERNAME, "role": "gerente", "advisor_id": None, "password": manager_password})
    for user in users:
        values = {"username": user["username"], "role": user["role"], "advisor_id": user["advisor_id"],
                  "password_hash": hash_password(user["password"])}
        session.execute(
            insert(PanelUser).values(**values)
            .on_conflict_do_update(index_elements=["username"],
                                   set_={key: values[key] for key in ("role", "advisor_id", "password_hash")}))


def main() -> None:
    for advisor in seed():
        # El panel se abre con usuario y contraseña en /login (DT-72). El deep-link con el token registra
        # el chat de Telegram del asesor para recibir la tarjeta del lead (B-7).
        print(f"{advisor.id}  {advisor.name:<15} {advisor.zone:<15} usuario: {ADVISOR_USERNAMES[advisor.id]}"
              f"   registro: ?start=adv_{advisor.access_token}")
    print(f"gerente  usuario: {MANAGER_USERNAME}")


if __name__ == "__main__":
    main()
