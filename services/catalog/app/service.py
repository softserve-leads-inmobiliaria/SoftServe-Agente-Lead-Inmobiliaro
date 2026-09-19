"""Lógica del catálogo falso (CT-8): las 5 tools `catalog.*` sobre los datos en memoria.

Puro Python, sin MCP ni red: `server.py` lo expone por MCP. Cada método devuelve un dict que cumple
el `outputSchema` de contracts/mcp/<tool>.schema.json o lanza `CatalogError` con uno de los códigos
del contrato (NOT_FOUND, INVALID_INPUT, UPSTREAM_UNAVAILABLE).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from app import data

# Campos públicos: `address_exact` nunca sale de aquí salvo en get_property con lead_qualified=true.
_SUMMARY_FIELDS = ("id", "title", "price", "type", "zone", "m2", "bedrooms", "bathrooms",
                   "amenities", "status", "address_approx")


class CatalogError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"error_code": self.error_code, "message": self.message}


class CatalogService:
    def __init__(
        self,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        available: Callable[[], bool] = lambda: True,
    ) -> None:
        self._today = today
        self._now = now
        # Permite a los tests simular la caída de la fuente de datos (UPSTREAM_UNAVAILABLE).
        self._available = available

    def _check_upstream(self) -> None:
        if not self._available():
            raise CatalogError("UPSTREAM_UNAVAILABLE", "El catálogo no está disponible por ahora.")

    def _find(self, property_id: Any) -> dict[str, Any]:
        if not isinstance(property_id, str) or not property_id:
            raise CatalogError("INVALID_INPUT", "property_id debe ser un texto no vacío.")
        for prop in data.PROPERTIES:
            if prop["id"] == property_id:
                return prop
        raise CatalogError("NOT_FOUND", f"No existe la propiedad {property_id}.")

    def search_properties(self, zone: str | None = None, budget_range: dict[str, float] | None = None,
                         type: str | None = None, bedrooms: int | None = None) -> dict[str, Any]:
        self._check_upstream()
        if budget_range is not None and budget_range["min"] > budget_range["max"]:
            raise CatalogError("INVALID_INPUT", "budget_range.min no puede ser mayor que max.")
        found = []
        for prop in data.PROPERTIES:
            if zone is not None and prop["zone"].lower() != zone.lower():
                continue
            if type is not None and prop["type"] != type:
                continue
            if bedrooms is not None and prop["bedrooms"] != bedrooms:
                continue
            if budget_range is not None and not (budget_range["min"] <= prop["price"] <= budget_range["max"]):
                continue
            found.append({k: prop[k] for k in _SUMMARY_FIELDS})
        return {"properties": found}

    def get_property(self, property_id: str, lead_qualified: bool) -> dict[str, Any]:
        self._check_upstream()
        prop = self._find(property_id)
        result = {k: prop[k] for k in _SUMMARY_FIELDS}
        result["address_exact"] = prop["address_exact"] if lead_qualified else None
        return result

    def get_availability(self, property_id: str) -> dict[str, Any]:
        self._check_upstream()
        prop = self._find(property_id)
        return {"property_id": prop["id"], "status": prop["status"],
                "checked_at": self._now().isoformat()}

    def get_promotions(self, property_id: str | None = None) -> dict[str, Any]:
        self._check_upstream()
        if property_id is not None:
            self._find(property_id)
        today = self._today().isoformat()
        promotions = [
            dict(promo) for promo in data.PROMOTIONS
            if promo["valid_until"] >= today and (property_id is None or promo["property_id"] == property_id)
        ]
        return {"promotions": promotions}

    def check_requirements(self, property_id: str, lead_profile: dict[str, Any]) -> dict[str, Any]:
        self._check_upstream()
        prop = self._find(property_id)
        rules = data.REQUIREMENTS[prop["type"]]

        ratio = rules["down_payment_ratio"]
        down_ok = ratio is None or lead_profile["down_payment"] >= prop["price"] * ratio
        multiple = rules["income_multiple"]
        income_ok = multiple is None or lead_profile["monthly_income"] >= prop["price"] * multiple
        references_ok = (not rules["references"]) or lead_profile["has_references"]

        notes = []
        if not down_ok:
            notes.append(f"Se requiere un enganche de al menos {int(ratio * 100)}% del precio.")
        if not income_ok:
            notes.append(f"Se requiere un ingreso mensual de al menos {multiple} veces la renta.")
        if not references_ok:
            notes.append("Se requieren referencias.")
        return {
            "meets_requirements": down_ok and income_ok and references_ok,
            "details": {"down_payment_ok": down_ok, "income_ok": income_ok,
                        "references_ok": references_ok,
                        "notes": " ".join(notes) or "Cumple los requisitos."},
        }
