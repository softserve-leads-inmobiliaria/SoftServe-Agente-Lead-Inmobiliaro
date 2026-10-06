"""Cómo se muestran en inglés los valores internos (#202, DT-55).

Los valores de los contratos y de la base siguen en español (`venta`, `disponible`, `nuevo`...): son claves,
no texto. Lo que lee una persona (el panel, el reporte semanal) pasa por `label`. Un valor que no está en la
tabla se muestra tal cual: una etiqueta que falta no puede romper una vista.
"""
from __future__ import annotations

from typing import Mapping

LABELS: Mapping[str, Mapping[str, str]] = {
    "property_type": {"venta": "for sale", "renta": "for rent", "preventa": "pre-sale"},
    "property_kind": {"departamento": "apartment", "casa": "house", "terreno": "land", "local": "commercial space",
                      "estudio": "studio"},
    "property_status": {"disponible": "available", "apartado": "reserved", "cerrado": "off the market"},
    "deal_kind": {"venta": "sale", "renta": "rental"},
    "lead_status": {"nuevo": "new", "mql": "qualified", "sql": "in progress", "visit_scheduled": "visit scheduled",
                    "closed_won": "deal closed", "closed_lost": "lost"},
    "notice_kind": {"calificado": "qualified lead", "traspaso": "handoff"},
}


def label(field: str, value: object) -> str:
    """La etiqueta en inglés de `value`; el valor tal cual si no tiene."""
    return LABELS.get(field, {}).get(str(value), str(value))
