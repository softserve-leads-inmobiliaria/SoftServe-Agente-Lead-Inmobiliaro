"""Datos de demo del catálogo (CT-8): 12 propiedades, 3 asesores, promociones y requisitos.

Desde C-1 el servicio lee las propiedades y promociones de Postgres: aquí quedan como semilla de los
tests y del arnés, y `REQUIREMENTS` como las reglas de `check_requirements`. Desde C-9 esta misma
lista es la que `scripts/seed_catalog.py` baja a la base, así que es la única fuente del inventario
de demo: los tests, el arnés y la base leen las mismas filas.

Son datos ficticios y estables: los tests y el arnés dependen de ellos. Los casos borde que se
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
    {"id": "prop_00231", "title": "Depto 2 rec. en Providencia", "price": 2350000, "type": "venta",
     "kind": "departamento",
     "zone": "Providencia", "m2": 78.5, "bedrooms": 2, "bathrooms": 2,
     "amenities": ["alberca", "gimnasio"], "status": "disponible",
     "address_approx": "Providencia, cerca de Av. Vallarta",
     "address_exact": "Calle Lerdo 1204, Providencia",
     "photo_url": _unsplash("1502672260266-1c1ef2d93688")},
    {"id": "prop_00232", "title": "Depto 1 rec. en Providencia", "price": 1650000, "type": "venta",
     "kind": "departamento",
     "zone": "Providencia", "m2": 52, "bedrooms": 1, "bathrooms": 1,
     "amenities": ["gimnasio"], "status": "disponible",
     "address_approx": "Providencia, cerca de Av. Marina",
     "address_exact": "Calle Sorolla 88, Providencia",
     "photo_url": _unsplash("1493809842364-78817add7ffb")},
    {"id": "prop_00233", "title": "Casa 3 rec. en Zapopan", "price": 4800000, "type": "venta",
     "kind": "casa",
     "zone": "Zapopan", "m2": 180, "bedrooms": 3, "bathrooms": 2.5,
     "amenities": ["jardín", "cochera"], "status": "disponible",
     "address_approx": "Zapopan, fraccionamiento cerrado",
     "address_exact": "Circuito Los Pinos 45, Zapopan",
     "photo_url": _unsplash("1600607687939-ce8a6c25118c")},
    {"id": "prop_00234", "title": "Casa 4 rec. en Zapopan", "price": 6900000, "type": "venta",
     "kind": "casa",
     "zone": "Zapopan", "m2": 240, "bedrooms": 4, "bathrooms": 3.5,
     "amenities": ["alberca", "jardín", "cochera"], "status": "apartado",
     "address_approx": "Zapopan, zona residencial",
     "address_exact": "Av. Los Cedros 310, Zapopan",
     "photo_url": _unsplash("1600210492486-724fe5c67fb0")},
    {"id": "prop_00235", "title": "Depto 2 rec. en renta en Chapalita", "price": 18500, "type": "renta",
     "kind": "departamento",
     "zone": "Chapalita", "m2": 70, "bedrooms": 2, "bathrooms": 1,
     "amenities": ["estacionamiento"], "status": "disponible",
     "address_approx": "Chapalita, cerca de Glorieta Chapalita",
     "address_exact": "Calle Paseo del Prado 12, Chapalita",
     "photo_url": _unsplash("1522708323590-d24dbb6b0267")},
    {"id": "prop_00236", "title": "Depto 1 rec. en renta en Chapalita", "price": 12000, "type": "renta",
     "kind": "departamento",
     "zone": "Chapalita", "m2": 45, "bedrooms": 1, "bathrooms": 1,
     "amenities": [], "status": "apartado",
     "address_approx": "Chapalita, zona comercial",
     "address_exact": "Av. Guadalupe 501, Chapalita",
     "photo_url": _unsplash("1586023492125-27b2c045efd7")},
    {"id": "prop_00237", "title": "Depto en preventa en Andares", "price": 3900000, "type": "preventa",
     "kind": "departamento",
     "zone": "Andares", "m2": 95, "bedrooms": 2, "bathrooms": 2,
     "amenities": ["alberca", "gimnasio", "salón de eventos"], "status": "disponible",
     "address_approx": "Andares, torre en construcción",
     "address_exact": "Blvd. Puerta de Hierro 4965, Andares",
     "photo_url": _unsplash("1560448204-e02f11c3d0e2")},
    {"id": "prop_00238", "title": "Penthouse en preventa en Andares", "price": 8500000, "type": "preventa",
     "kind": "departamento",
     "zone": "Andares", "m2": 160, "bedrooms": 3, "bathrooms": 3.5,
     "amenities": ["alberca", "terraza", "gimnasio"], "status": "disponible",
     "address_approx": "Andares, último piso de torre",
     "address_exact": "Blvd. Puerta de Hierro 4965 PH, Andares",
     "photo_url": _unsplash("1631679706909-1844bbd07221")},
    {"id": "prop_00239", "title": "Casa 3 rec. en Tlaquepaque", "price": 2900000, "type": "venta",
     "kind": "casa",
     "zone": "Tlaquepaque", "m2": 130, "bedrooms": 3, "bathrooms": 2,
     "amenities": ["patio"], "status": "cerrado",
     "address_approx": "Tlaquepaque, centro histórico",
     "address_exact": "Calle Independencia 77, Tlaquepaque",
     "photo_url": _unsplash("1505691938895-1758d7feb511")},
    {"id": "prop_00240", "title": "Estudio en renta en Providencia", "price": 9500, "type": "renta",
     "kind": "estudio",
     "zone": "Providencia", "m2": 35, "bedrooms": 0, "bathrooms": 1,
     "amenities": ["gimnasio"], "status": "disponible",
     "address_approx": "Providencia, cerca de Av. Vallarta",
     "address_exact": "Calle Gante 19, Providencia",
     "photo_url": _unsplash("1484154218962-a197022b5858")},
    {"id": "prop_00241", "title": "Terreno en venta en Zapopan", "price": 2100000, "type": "venta",
     "kind": "terreno",
     "zone": "Zapopan", "m2": 620, "bedrooms": 0, "bathrooms": 0,
     "amenities": [], "status": "disponible",
     "address_approx": "Zapopan, zona de crecimiento",
     "address_exact": "Camino Real 880, Zapopan",
     "photo_url": _unsplash("1500382017468-9049fed747ef")},
    {"id": "prop_00242", "title": "Local comercial en renta en Tlaquepaque", "price": 22000, "type": "renta",
     "kind": "local",
     "zone": "Tlaquepaque", "m2": 120, "bedrooms": 0, "bathrooms": 2,
     "amenities": ["estacionamiento"], "status": "disponible",
     "address_approx": "Tlaquepaque, zona comercial",
     "address_exact": "Calle Morelos 210, Tlaquepaque",
     "photo_url": _unsplash("1497366216548-37526070297c")},
]

ADVISORS: list[dict[str, Any]] = [
    {"id": "adv_001", "name": "Valeria Ortiz", "zones": ["Providencia", "Chapalita"]},
    {"id": "adv_002", "name": "Andrés Núñez", "zones": ["Zapopan", "Tlaquepaque"]},
    {"id": "adv_003", "name": "Camila Reyes", "zones": ["Andares", "Providencia"]},
]

# `valid_until` en ISO (YYYY-MM-DD). promo_003 está vencida a propósito.
PROMOTIONS: list[dict[str, Any]] = [
    {"id": "promo_001", "property_id": "prop_00231", "title": "Enganche diferido",
     "description": "Enganche en 6 mensualidades.", "valid_until": "2099-12-31"},
    {"id": "promo_002", "property_id": "prop_00237", "title": "Preventa con descuento",
     "description": "Descuento por apartar en preventa.", "valid_until": "2099-12-31"},
    {"id": "promo_003", "property_id": "prop_00233", "title": "Promoción de verano",
     "description": "Promoción ya terminada.", "valid_until": "2000-01-01"},
]

# Requisitos por propiedad: fracción mínima de enganche sobre el precio (venta/preventa), múltiplo
# mínimo de ingreso mensual sobre la renta (renta) y si piden referencias.
REQUIREMENTS: dict[str, dict[str, Any]] = {
    "venta": {"down_payment_ratio": 0.10, "income_multiple": None, "references": False},
    "preventa": {"down_payment_ratio": 0.20, "income_multiple": None, "references": False},
    "renta": {"down_payment_ratio": None, "income_multiple": 3, "references": True},
}
