"""Landing pública de propiedades y enlace a Telegram por propiedad (B-16).

Solo lectura y sin token: lista las propiedades `disponible` con datos de anuncio y arma, por cada una, el
*deep-link* `https://t.me/<bot>?start=<property_id>`. El `property_id` conserva el prefijo `prop_`, así que
el payload es el mismo que `canonical.parse_deep_link` (B-3) ya entiende: al abrir el enlace, Telegram manda
`/start prop_...` y el agente arranca con esa propiedad. La landing no escribe en la base.
"""
from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Property

# Telegram acepta en `?start=` hasta 64 caracteres de `A-Za-z0-9_-`; los ids son `prop_` + dígitos, así que
# sobran. Se deja el número en una constante para que el test del contrato no use un literal suelto.
DEEP_LINK_PAYLOAD_MAX = 64
PUBLISHED_STATUS = "disponible"


def bot_username_from_env() -> str | None:
    """`TELEGRAM_BOT_USERNAME` sin `@`; None si está vacío o ausente (canal sin configurar)."""
    raw = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    return raw or None


def available_properties(session: Session) -> list[Property]:
    """Las propiedades publicables (B-16), en orden estable. Sin `address_exact`: esa es la dirección
    reservada (`guardrails.md`) y no cruza a la landing."""
    return list(session.execute(
        select(Property)
        .where(Property.status == PUBLISHED_STATUS)
        .order_by(Property.zone, Property.price, Property.id)
    ).scalars())


def telegram_link(bot_username: str, property_id: str) -> str:
    """El enlace que abre el chat del bot con la propiedad: `https://t.me/<usuario>?start=<property_id>`."""
    return f"https://t.me/{bot_username}?start={property_id}"
