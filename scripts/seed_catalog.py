"""Siembra el inventario de demo del catálogo (C-9): propiedades y promociones.

Sin esto el catálogo del stack arranca vacío y toda búsqueda devuelve cero propiedades: la demo y los
guardrails no tienen nada que ejercitar.

Importa la lista de `services/catalog/app/data.py` en vez de copiarla a mano, para que la base, los
tests y el arnés no puedan separarse: si el seed duplicara los datos, cambiar un precio en el catálogo
dejaría la base con el valor viejo sin avisar (misma trampa que `scripts/seed_advisors.py`, que sí
duplica los asesores).

Las amenidades de estas propiedades están dentro del vocabulario cerrado de
`services/core/app/promise_guard.py`: si aparece una amenidad fuera de las canónicas, el guardrail la
marca como alucinación sobre una propiedad que sí la tiene.

Idempotente: se puede correr en cada `docker compose up` sin duplicar. Ojo — una propiedad que ya
existe no se actualiza, así que este script siembra, no repara: para corregir un dato hay que editarlo
en la base o tirar el volumen de datos. La única excepción es `photo_url` (B-10): una base sembrada
antes de que existieran las fotos la recibe, pero una foto que ya tiene la fila nunca se pisa.

Uso:  python scripts/seed_catalog.py
"""
from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.models import Promotion, Property  # noqa: E402
from db.session import get_engine, session_scope  # noqa: E402
from services.catalog.app.data import PROMOTIONS, PROPERTIES  # noqa: E402


def _to_row(prop: dict[str, object]) -> dict[str, object]:
    """Convierte una entrada de `data.py` a los tipos que espera la columna.

    `price`, `m2` y `bathrooms` son `Numeric` en la base: se pasan como `Decimal` y no como `float`
    para no arrastrar error de punto flotante al comparar o al formatear precios.
    """
    return {
        **prop,
        "price": Decimal(str(prop["price"])),
        "m2": Decimal(str(prop["m2"])),
        "bathrooms": Decimal(str(prop["bathrooms"])),
    }


def seed(engine=None) -> tuple[list[Property], list[Promotion]]:
    """Inserta el inventario que falte y devuelve las filas tal como quedaron en la base."""
    with session_scope(engine or get_engine()) as session:
        for prop in PROPERTIES:
            row = insert(Property).values(**_to_row(prop))
            session.execute(row.on_conflict_do_update(
                index_elements=["id"],
                set_={"photo_url": func.coalesce(Property.photo_url, row.excluded.photo_url)}))
        session.flush()

        for promo in PROMOTIONS:
            session.execute(
                insert(Promotion)
                .values(**{**promo, "valid_until": date.fromisoformat(promo["valid_until"])})
                .on_conflict_do_nothing(index_elements=["id"]))
        session.flush()

        props = [session.get(Property, prop["id"]) for prop in PROPERTIES]
        promos = [session.get(Promotion, promo["id"]) for promo in PROMOTIONS]
        return props, promos


def main() -> None:
    props, promos = seed()
    for prop in props:
        bedrooms = f"{prop.bedrooms} rec" if prop.bedrooms else "sin recámaras"
        print(f"{prop.id}  {prop.type:<8} {prop.status:<10} {prop.zone:<12} {bedrooms:<14} {prop.title}")
    vencidas = [p for p in promos if p.valid_until < date.today()]
    print(f"\n{len(props)} propiedades, {len(promos)} promociones "
          f"({len(vencidas)} vencida(s), no se devuelven: {', '.join(p.id for p in vencidas) or 'ninguna'})")


if __name__ == "__main__":
    main()
