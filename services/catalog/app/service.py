"""Lógica del catálogo (C-1): las 5 tools `catalog.*` sobre Postgres (`properties` y `promotions`).

Sin MCP ni red: `server.py` lo expone por MCP. Las reglas de `check_requirements` siguen en `app/data.py`. Cada método devuelve un dict que cumple
el `outputSchema` de contracts/mcp/<tool>.schema.json o lanza `CatalogError` con uno de los códigos
del contrato (NOT_FOUND, INVALID_INPUT, UPSTREAM_UNAVAILABLE).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import data
from db.models import PROPERTY_KINDS, Promotion, Property

# Campos públicos: `address_exact` nunca sale de aquí salvo en get_property con lead_qualified=true.
_SUMMARY_FIELDS = ("id", "title", "price", "type", "kind", "zone", "m2", "bedrooms", "bathrooms",
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
            raise CatalogError("UPSTREAM_UNAVAILABLE", "The catalog is not available right now.")

    def _find(self, db: Session, property_id: Any) -> Property:
        """Busca una propiedad en la base de datos; lanza error si es inválida o no existe."""
        if not isinstance(property_id, str) or not property_id:
            raise CatalogError("INVALID_INPUT", "property_id must be a non-empty string.")
        
        prop = db.query(Property).filter(Property.id == property_id).first()

        if not prop:
            raise CatalogError("NOT_FOUND", f"Property {property_id} does not exist.")
        
        return prop

    def search_properties(
        self,
        zone: str | None = None,
        budget_range: dict[str, float] | None = None,
        type: str | None = None,
        bedrooms: int | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Busca propiedades en PostgreSQL aplicando filtros y devolviendo los campos de resumen."""
        self._check_upstream()

        # Validamos el rango de presupuesto según las reglas de negocio
        if budget_range is not None and budget_range["min"] > budget_range["max"]:
            raise CatalogError("INVALID_INPUT", "budget_range.min cannot be greater than max.")
        # `type` es la operación (venta/renta/preventa); `kind`, el inmueble (#188).
        if kind is not None and kind not in PROPERTY_KINDS:
            raise CatalogError("INVALID_INPUT", f"kind must be one of: {', '.join(PROPERTY_KINDS)}.")

        with self._db_session_factory() as db:
            query = db.query(Property)

            # Aplicamos filtros condicionales sobre la consulta de SQLAlchemy
            # La zona es el barrio y la ciudad va en `address_approx` (DT-55): "Medellin" trae todos sus
            # barrios. `address_exact` nunca entra al filtro, para no delatarla por el resultado.
            if zone is not None:
                pattern = f"%{zone}%"
                query = query.filter(or_(Property.zone.ilike(pattern), Property.address_approx.ilike(pattern)))
            if type is not None:
                query = query.filter(Property.type == type)
            if bedrooms is not None:
                query = query.filter(Property.bedrooms == bedrooms)
            if kind is not None:
                query = query.filter(Property.kind == kind)
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
                    "price": float(prop.price) if prop.price is not None else None,
                    "type": prop.type,
                    "kind": prop.kind,
                    "zone": prop.zone,
                    "m2": float(prop.m2) if prop.m2 is not None else None,
                    "bedrooms": prop.bedrooms,
                    "bathrooms": float(prop.bathrooms) if prop.bathrooms is not None else None,
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
            "price": float(prop.price) if prop.price is not None else None,
            "type": prop.type,
            "kind": prop.kind,
            "zone": prop.zone,
            "m2": float(prop.m2) if prop.m2 is not None else None,
            "bedrooms": prop.bedrooms,
            "bathrooms": float(prop.bathrooms) if prop.bathrooms is not None else None,
            "amenities": prop.amenities,
            "status": prop.status,
            "address_approx": prop.address_approx
        }

        result["address_exact"] = prop.address_exact if lead_qualified else None
        result["photo_url"] = prop.photo_url  # B-10: opcional; sin foto el canal manda solo el texto

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

            query = db.query(Promotion).filter(Promotion.valid_until >= today_date)

            if property_id is not None:
                query = query.filter(Promotion.property_id == property_id)
            
            active_promotions = query.all()

            promotions_list = []

            for promo in active_promotions:
                promotions_list.append({
                    "id": str(promo.id),
                    "property_id": str(promo.property_id) if promo.property_id else None,
                    "title": promo.title,
                    "description": promo.description,
                    "valid_until": promo.valid_until.isoformat()
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
            income_ok = multiple is None or lead_profile.get("monthly_income", 0) >= real_price * multiple

            references_ok = (not rules["references"]) or lead_profile.get("has_references", False)

            notes = []
            if not down_ok:
                notes.append(f"A down payment of at least {int(ratio * 100)}% of the price is required.")
            if not income_ok:
                notes.append(f"A monthly income of at least {multiple} times the rent is required.")
            if not references_ok:
                notes.append("References are required.")
            
            return {
                "meets_requirements": down_ok and income_ok and references_ok,
                "details": {
                    "down_payment_ok": down_ok, 
                    "income_ok": income_ok,
                    "references_ok": references_ok,
                    "notes": " ".join(notes) or "Meets the requirements."
                },
            }
