"""Lógica del catálogo falso (CT-8): las 5 tools `catalog.*` sobre los datos en memoria.

Puro Python, sin MCP ni red: `server.py` lo expone por MCP. Cada método devuelve un dict que cumple
el `outputSchema` de contracts/mcp/<tool>.schema.json o lanza `CatalogError` con uno de los códigos
del contrato (NOT_FOUND, INVALID_INPUT, UPSTREAM_UNAVAILABLE).
"""
from __future__ import annotations
from sqlalchemy.engine.result import ResultInternal

from datetime import date, datetime
from typing import Any, Callable
from sqlalchemy.orm import Session
from db.models import Property, Promotion

from . import data

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
        db_session_factory: Callable[[], Session],
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        available: Callable[[], bool] = lambda: True,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._today = today
        self._now = now
        # Permite a los tests simular la caída de la fuente de datos (UPSTREAM_UNAVAILABLE).
        self._available = available

    def _check_upstream(self) -> None:
        if not self._available():
            raise CatalogError("UPSTREAM_UNAVAILABLE", "El catálogo no está disponible por ahora.")

    def _find(self, db: Session, property_id: Any) -> Property:
        """Busca una propiedad en la base de datos; lanza error si es inválida o no existe."""
        if not isinstance(property_id, str) or not property_id:
            raise CatalogError("INVALID_INPUT", "property_id debe ser un texto no vacío.")
        
        prop = db.query(Property).filter(Property.id == property_id).first()

        if not prop:
            raise CatalogError("NOT_FOUND", f"No existe la propiedad {property_id}.")
        
        return prop

    def search_properties(
        self,
        zone: str | None = None,
        budget_range: dict[str, float] | None = None,
        type: str | None = None,
        bedrooms: int | None = None
    ) -> dict[str, Any]:
        """Busca propiedades en PostgreSQL aplicando filtros y devolviendo los campos de resumen."""
        self._check_upstream()

        # Validamos el rango de presupuesto según las reglas de negocio[cite: 3]
        if budget_range is not None and budget_range["min"] > budget_range["max"]:
            raise CatalogError("INVALID_INPUT", "budget_range.min no puede ser mayor que max.")

        with self._db_session_factory() as db:
            query = db.query(Property)

            # Aplicamos filtros condicionales sobre la consulta de SQLAlchemy
            if zone is not None:
                query = query.filter(Property.zone.ilike(f"%{zone}%"))
            if type is not None:
                query = query.filter(Property.type == type)
            if bedrooms is not None:
                query = query.filter(Property.bedrooms >= bedrooms)
            if budget_range is not None:
                query = query.filter(
                    Property.price >= budget_range["min"],
                    Property.price <= budget_range["max"]
                )
            
            properties = query.all()

            found = []
            for prop in properties:
                prop_dict = {
                    "id": prop.id,
                    "title": prop.title,
                    "price": float(prop.price),
                    "type": prop.type,
                    "zone": prop.zone,
                    "m2": prop.area_built_m2,
                    "bedrooms": prop.bedrooms,
                    "bathrooms": prop.bathrooms,
                    "amenities": prop.amenities,
                    "status": prop.status,
                    "address_approx": prop.address_approx
                }
                found.append(prop_dict)
                
            return {"properties": found}
        

    def get_property(self, property_id: str, lead_qualified: bool) -> dict[str, Any]:
        """Recupera la ficha de la propiedad protegiendo la dirección exacta."""
        self._check_upstream()

        with self._db_session_factory() as db:
            prop = self._find(db, property_id)

        result = {
            "id": prop.id,
            "title": prop.title,
            "price": float(prop.price),
            "type": prop.type,
            "zone": prop.zone,
            "m2": prop.area_built_m2,
            "bedrooms": prop.bedrooms,
            "bathrooms": prop.bathrooms,
            "amenities": prop.amenities,
            "status": prop.status,
            "address_approx": prop.address_approx
        }

        result["address_exact"] = prop.address_exact if lead_qualified else None

        return result

    def get_availability(self, property_id: str) -> dict[str, Any]:
        """Consulta el estado de disponibilidad actual de la propiedad en Postgres."""
        self._check_upstream()

        with self._db_session_factory() as db:
            prop = self._find(db, property_id)

            return {
                "property_id": prop.id, 
                "status": prop.status,
                "checked_at": self._now().isoformat()
            }

    def get_promotions(self, property_id: str | None = None) -> dict[str, Any]:
        """Obtiene las promociones vigentes desde Postgres, opcionalmente filtradas por propiedad."""
        self._check_upstream()

        with self._db_session_factory() as db:

            if property_id is not None:
                self._find(db, property_id)

            today_date = self._today()

            query = db.query(Promotion).filter(Promotion.valid_to >= today_date)

            if property_id is not None:
                query = query.filter(Promotion.property_id == property_id)
            
            active_promotions = query.all()

            promotions_list = []

            for promo in active_promotions:
                promotions_list.append({
                    "id": str(promo.id),
                    "property_id": str(promo.property_id) if promo.property_id else None,
                    "type": promo.type,
                    "conditions": promo.conditions,
                    "valid_from": promo.valid_from.isoformat() if promo.valid_from else None,
                    "valid_until": promo.valid_to.isoformat()
                })

            return {"promotions": promotions_list}

    def check_requirements(self, property_id: str, lead_profile: dict[str, Any]) -> dict[str, Any]:
        """Evalúa el perfil del lead contra los requisitos financieros de la propiedad en Postgres."""
        self._check_upstream()

        with self._db_session_factory() as db:
            
            prop = self._find(db, property_id)

            rules = data.REQUIREMENTS[prop.type]

            real_price = float(prop.price)
            ratio = rules["down_payment_ratio"]
            multiple = rules["income_multiple"]

            down_ok = ratio is None or lead_profile.get("down_payment", 0) >= real_price * ratio
            income_ok = multiple is None or lead_profile["monthly_income"] >= real_price * multiple

            references_ok = (not rules["references"]) or lead_profile.get("has_references", False)

            notes = []
            if not down_ok:
                notes.append(f"Se requiere un enganche de al menos {int(ratio * 100)}% del precio.")
            if not income_ok:
                notes.append(f"Se requiere un ingreso mensual de al menos {multiple} veces la renta.")
            if not references_ok:
                notes.append("Se requieren referencias.")
            
            return {
                "meets_requirements": down_ok and income_ok and references_ok,
                "details": {
                    "down_payment_ok": down_ok, 
                    "income_ok": income_ok,
                    "references_ok": references_ok,
                    "notes": " ".join(notes) or "Cumple los requisitos."
                },
            }
