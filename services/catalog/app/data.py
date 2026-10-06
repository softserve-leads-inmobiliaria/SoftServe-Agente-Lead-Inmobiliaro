"""Datos de demo del catálogo (CT-8): 12 propiedades, 3 asesores, promociones y requisitos.

Desde C-1 el servicio lee las propiedades y promociones de Postgres: aquí quedan como semilla de los
tests y del arnés, y `REQUIREMENTS` como las reglas de `check_requirements`. Desde C-9 esta misma
lista es la que `scripts/seed_catalog.py` baja a la base, así que es la única fuente del inventario
de demo: los tests, el arnés y la base leen las mismas filas.

Son datos ficticios y estables: los tests y el arnés dependen de ellos. Desde #202 (DT-55) el
inventario está en Medellín y Bogotá, con precios en dólares (USD) y los textos que lee el lead en
inglés. `zone` es el barrio y la ciudad va en `address_approx`, que la búsqueda por zona también
mira: "apartments in Medellin" encuentra las de todos sus barrios. Ciudades y barrios van sin
tilde, como se escriben en inglés. Las amenidades siguen el vocabulario cerrado de `promise_guard`
(DT-26). Los casos borde que se
ejercitan a propósito son: `apartado` y `cerrado`, una propiedad sin promociones, una promoción
vencida (para probar que nunca se devuelve), y tres propiedades sin recámaras (un estudio, un terreno
y un local comercial) para probar que `bedrooms: 0` no es un dato faltante. Cada propiedad trae su
`photo_url` (B-10) y su `kind` (#188): están representados los cinco tipos del vocabulario
(`departamento`, `casa`, `terreno`, `local`, `estudio`).
"""
from __future__ import annotations

from typing import Any


def _unsplash(photo_id: str) -> str:
    """Foto de demo (B-10): interiores de Unsplash, de uso libre. Ninguna muestra fachada ni número de
    calle, para no delatar la dirección exacta (A-6). Telegram la descarga de esta URL pública."""
    return f"https://images.unsplash.com/photo-{photo_id}?w=1024&q=80"


PROPERTIES: list[dict[str, Any]] = [
    {"id": "prop_00231", "title": "2-bedroom apartment in El Poblado", "price": 185000, "type": "venta",
     "kind": "departamento",
     "zone": "El Poblado", "m2": 78.5, "bedrooms": 2, "bathrooms": 2,
     "amenities": ["pool", "gym"], "status": "disponible",
     "address_approx": "El Poblado, Medellin, near Parque Lleras",
     "address_exact": "Carrera 37 # 8A-24, El Poblado",
     "photo_url": _unsplash("1502672260266-1c1ef2d93688")},
    {"id": "prop_00232", "title": "1-bedroom apartment in El Poblado", "price": 129000, "type": "venta",
     "kind": "departamento",
     "zone": "El Poblado", "m2": 52, "bedrooms": 1, "bathrooms": 1,
     "amenities": ["gym"], "status": "disponible",
     "address_approx": "El Poblado, Medellin, near Avenida El Poblado",
     "address_exact": "Calle 10 # 43-12, El Poblado",
     "photo_url": _unsplash("1493809842364-78817add7ffb")},
    {"id": "prop_00233", "title": "3-bedroom house in Usaquen", "price": 365000, "type": "venta",
     "kind": "casa",
     "zone": "Usaquen", "m2": 180, "bedrooms": 3, "bathrooms": 2.5,
     "amenities": ["garden", "garage"], "status": "disponible",
     "address_approx": "Usaquen, Bogota, gated community",
     "address_exact": "Calle 127 # 7-45, Usaquen",
     "photo_url": _unsplash("1600607687939-ce8a6c25118c")},
    {"id": "prop_00234", "title": "4-bedroom house in Usaquen", "price": 520000, "type": "venta",
     "kind": "casa",
     "zone": "Usaquen", "m2": 240, "bedrooms": 4, "bathrooms": 3.5,
     "amenities": ["pool", "garden", "garage"], "status": "apartado",
     "address_approx": "Usaquen, Bogota, residential area",
     "address_exact": "Carrera 5 # 119-30, Usaquen",
     "photo_url": _unsplash("1600210492486-724fe5c67fb0")},
    {"id": "prop_00235", "title": "2-bedroom apartment for rent in Laureles", "price": 1150, "type": "renta",
     "kind": "departamento",
     "zone": "Laureles", "m2": 70, "bedrooms": 2, "bathrooms": 1,
     "amenities": ["parking"], "status": "disponible",
     "address_approx": "Laureles, Medellin, near Avenida Nutibara",
     "address_exact": "Circular 4 # 70-12, Laureles",
     "photo_url": _unsplash("1522708323590-d24dbb6b0267")},
    {"id": "prop_00236", "title": "1-bedroom apartment for rent in Laureles", "price": 750, "type": "renta",
     "kind": "departamento",
     "zone": "Laureles", "m2": 45, "bedrooms": 1, "bathrooms": 1,
     "amenities": [], "status": "apartado",
     "address_approx": "Laureles, Medellin, shopping area",
     "address_exact": "Carrera 76 # 33-50, Laureles",
     "photo_url": _unsplash("1586023492125-27b2c045efd7")},
    {"id": "prop_00237", "title": "Pre-sale apartment in Envigado", "price": 295000, "type": "preventa",
     "kind": "departamento",
     "zone": "Envigado", "m2": 95, "bedrooms": 2, "bathrooms": 2,
     "amenities": ["pool", "gym", "event room"], "status": "disponible",
     "address_approx": "Envigado, Medellin, tower under construction",
     "address_exact": "Calle 37 Sur # 27-80, Envigado",
     "photo_url": _unsplash("1560448204-e02f11c3d0e2")},
    {"id": "prop_00238", "title": "Pre-sale penthouse in Envigado", "price": 640000, "type": "preventa",
     "kind": "departamento",
     "zone": "Envigado", "m2": 160, "bedrooms": 3, "bathrooms": 3.5,
     "amenities": ["pool", "terrace", "gym"], "status": "disponible",
     "address_approx": "Envigado, Medellin, top floor of the tower",
     "address_exact": "Calle 37 Sur # 27-80 PH, Envigado",
     "photo_url": _unsplash("1631679706909-1844bbd07221")},
    {"id": "prop_00239", "title": "3-bedroom house in Chapinero", "price": 220000, "type": "venta",
     "kind": "casa",
     "zone": "Chapinero", "m2": 130, "bedrooms": 3, "bathrooms": 2,
     "amenities": ["patio"], "status": "cerrado",
     "address_approx": "Chapinero, Bogota, historic district",
     "address_exact": "Carrera 4 # 57-21, Chapinero",
     "photo_url": _unsplash("1505691938895-1758d7feb511")},
    {"id": "prop_00240", "title": "Studio for rent in El Poblado", "price": 600, "type": "renta",
     "kind": "estudio",
     "zone": "El Poblado", "m2": 35, "bedrooms": 0, "bathrooms": 1,
     "amenities": ["gym"], "status": "disponible",
     "address_approx": "El Poblado, Medellin, near Parque Lleras",
     "address_exact": "Carrera 35 # 8A-60, El Poblado",
     "photo_url": _unsplash("1484154218962-a197022b5858")},
    {"id": "prop_00241", "title": "Lot for sale in Usaquen", "price": 160000, "type": "venta",
     "kind": "terreno",
     "zone": "Usaquen", "m2": 620, "bedrooms": 0, "bathrooms": 0,
     "amenities": [], "status": "disponible",
     "address_approx": "Usaquen, Bogota, growing area",
     "address_exact": "Calle 140 # 9-15, Usaquen",
     "photo_url": _unsplash("1500382017468-9049fed747ef")},
    {"id": "prop_00242", "title": "Commercial space for rent in Chapinero", "price": 1600, "type": "renta",
     "kind": "local",
     "zone": "Chapinero", "m2": 120, "bedrooms": 0, "bathrooms": 2,
     "amenities": ["parking"], "status": "disponible",
     "address_approx": "Chapinero, Bogota, shopping area",
     "address_exact": "Carrera 13 # 63-40, Chapinero",
     "photo_url": _unsplash("1497366216548-37526070297c")},
]

ADVISORS: list[dict[str, Any]] = [
    {"id": "adv_001", "name": "Valeria Ortiz", "zones": ["El Poblado", "Laureles"]},
    {"id": "adv_002", "name": "Andrés Núñez", "zones": ["Usaquen", "Chapinero"]},
    {"id": "adv_003", "name": "Camila Reyes", "zones": ["Envigado", "El Poblado"]},
]

# `valid_until` en ISO (YYYY-MM-DD). promo_003 está vencida a propósito.
PROMOTIONS: list[dict[str, Any]] = [
    {"id": "promo_001", "property_id": "prop_00231", "title": "Deferred down payment",
     "description": "Down payment in 6 monthly installments.", "valid_until": "2099-12-31"},
    {"id": "promo_002", "property_id": "prop_00237", "title": "Pre-sale discount",
     "description": "Discount for reserving during pre-sale.", "valid_until": "2099-12-31"},
    {"id": "promo_003", "property_id": "prop_00233", "title": "Summer promotion",
     "description": "Promotion already over.", "valid_until": "2000-01-01"},
]

# Requisitos por propiedad: fracción mínima de enganche sobre el precio (venta/preventa), múltiplo
# mínimo de ingreso mensual sobre la renta (renta) y si piden referencias.
REQUIREMENTS: dict[str, dict[str, Any]] = {
    "venta": {"down_payment_ratio": 0.10, "income_multiple": None, "references": False},
    "preventa": {"down_payment_ratio": 0.20, "income_multiple": None, "references": False},
    "renta": {"down_payment_ratio": None, "income_multiple": 3, "references": True},
}
