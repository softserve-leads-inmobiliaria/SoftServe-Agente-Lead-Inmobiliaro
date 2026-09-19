"""Datos en memoria del catálogo falso (CT-8): ~10 propiedades, 3 asesores, promociones y requisitos.

Son datos ficticios y estables: los tests y el arnés dependen de ellos. Incluyen casos `apartado` y
`cerrado`, una propiedad sin promociones y una promoción vencida (para probar que nunca se devuelve).
"""
from __future__ import annotations

from typing import Any

PROPERTIES: list[dict[str, Any]] = [
    {"id": "prop_00231", "title": "Depto 2 rec. en Providencia", "price": 2350000, "type": "venta",
     "zone": "Providencia", "m2": 78.5, "bedrooms": 2, "bathrooms": 2,
     "amenities": ["alberca", "gimnasio"], "status": "disponible",
     "address_approx": "Providencia, cerca de Av. Vallarta",
     "address_exact": "Calle Lerdo 1204, Providencia"},
    {"id": "prop_00232", "title": "Depto 1 rec. en Providencia", "price": 1650000, "type": "venta",
     "zone": "Providencia", "m2": 52, "bedrooms": 1, "bathrooms": 1,
     "amenities": ["gimnasio"], "status": "disponible",
     "address_approx": "Providencia, cerca de Av. Marina",
     "address_exact": "Calle Sorolla 88, Providencia"},
    {"id": "prop_00233", "title": "Casa 3 rec. en Zapopan", "price": 4800000, "type": "venta",
     "zone": "Zapopan", "m2": 180, "bedrooms": 3, "bathrooms": 2.5,
     "amenities": ["jardín", "cochera"], "status": "disponible",
     "address_approx": "Zapopan, fraccionamiento cerrado",
     "address_exact": "Circuito Los Pinos 45, Zapopan"},
    {"id": "prop_00234", "title": "Casa 4 rec. en Zapopan", "price": 6900000, "type": "venta",
     "zone": "Zapopan", "m2": 240, "bedrooms": 4, "bathrooms": 3.5,
     "amenities": ["alberca", "jardín", "cochera"], "status": "apartado",
     "address_approx": "Zapopan, zona residencial",
     "address_exact": "Av. Los Cedros 310, Zapopan"},
    {"id": "prop_00235", "title": "Depto 2 rec. en renta en Chapalita", "price": 18500, "type": "renta",
     "zone": "Chapalita", "m2": 70, "bedrooms": 2, "bathrooms": 1,
     "amenities": ["estacionamiento"], "status": "disponible",
     "address_approx": "Chapalita, cerca de Glorieta Chapalita",
     "address_exact": "Calle Paseo del Prado 12, Chapalita"},
    {"id": "prop_00236", "title": "Depto 1 rec. en renta en Chapalita", "price": 12000, "type": "renta",
     "zone": "Chapalita", "m2": 45, "bedrooms": 1, "bathrooms": 1,
     "amenities": [], "status": "apartado",
     "address_approx": "Chapalita, zona comercial",
     "address_exact": "Av. Guadalupe 501, Chapalita"},
    {"id": "prop_00237", "title": "Depto en preventa en Andares", "price": 3900000, "type": "preventa",
     "zone": "Andares", "m2": 95, "bedrooms": 2, "bathrooms": 2,
     "amenities": ["alberca", "gimnasio", "salón de eventos"], "status": "disponible",
     "address_approx": "Andares, torre en construcción",
     "address_exact": "Blvd. Puerta de Hierro 4965, Andares"},
    {"id": "prop_00238", "title": "Penthouse en preventa en Andares", "price": 8500000, "type": "preventa",
     "zone": "Andares", "m2": 160, "bedrooms": 3, "bathrooms": 3.5,
     "amenities": ["alberca", "terraza", "gimnasio"], "status": "disponible",
     "address_approx": "Andares, último piso de torre",
     "address_exact": "Blvd. Puerta de Hierro 4965 PH, Andares"},
    {"id": "prop_00239", "title": "Casa 3 rec. en Tlaquepaque", "price": 2900000, "type": "venta",
     "zone": "Tlaquepaque", "m2": 130, "bedrooms": 3, "bathrooms": 2,
     "amenities": ["patio"], "status": "cerrado",
     "address_approx": "Tlaquepaque, centro histórico",
     "address_exact": "Calle Independencia 77, Tlaquepaque"},
    {"id": "prop_00240", "title": "Estudio en renta en Providencia", "price": 9500, "type": "renta",
     "zone": "Providencia", "m2": 35, "bedrooms": 0, "bathrooms": 1,
     "amenities": ["gimnasio"], "status": "disponible",
     "address_approx": "Providencia, cerca de Av. Vallarta",
     "address_exact": "Calle Gante 19, Providencia"},
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
