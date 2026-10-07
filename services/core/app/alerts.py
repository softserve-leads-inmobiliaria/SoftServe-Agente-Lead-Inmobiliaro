"""Alertas de propiedades nuevas (A-18, DT-45): "avísame si sale algo así".

Lo decide el código, no el LLM, como la agenda (B-8):
1. Después de una búsqueda con pocos resultados (`FEW_RESULTS` o menos), el turno deja los filtros de esa
   búsqueda en `last_tool_results` (`ALERT_KEY`) y le ofrece la alerta al lead, una sola vez por
   conversación (`OFFERED_KEY`, #213).
2. Si el lead la pide ("avísame", "me avisas"...) en ese turno o en el siguiente, se guarda la alerta en
   `property_alerts` con esos filtros, sin texto del lead. Una activa por lead, por `ALERT_DAYS` días.
3. Un *job* `alert_scan` del *scheduler* (A-17) busca propiedades `disponible` que cumplan los filtros y que
   hayan entrado a `disponible` después de crearse la alerta (`property_status_events`, N-1). Por cada una
   encola un `property_alert`, que le escribe al lead una sola vez por propiedad.
"""
from __future__ import annotations

import os
import re
import unicodedata
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import exists, func, select, text, update
from sqlalchemy.orm import Session

from app import jobs
from db.models import PropertyAlert

ALERT_SCAN_KIND = "alert_scan"
PROPERTY_ALERT_KIND = "property_alert"
SCAN_KEY = "all"  # un solo `alert_scan` pendiente para todas las alertas
ALERT_KEY = "alert_offer"  # en `last_tool_results`: los filtros de la búsqueda que se puede volver alerta
OFFERED_KEY = "alert_offered"  # en `last_tool_results`: la alerta ya se ofreció en esta conversación (#213)
FEW_RESULTS = 2
ALERT_DAYS = 90

OFFER_TEXT = "If you'd like, I can let you know when something like this comes up: just say \"notify me\"."
CREATED_TEXT = ("Done, I'll let you know here when a property matching what you're looking for comes up. "
                "If you'd rather I stop messaging you, reply STOP.")
NO_SEARCH_TEXT = ("Happy to let you know. First, tell me what you're looking for (area, whether you want to buy "
                  "or rent, and your budget) so I know which properties to notify you about.")

def carry_offered(tool_results: Mapping[str, Any], previous: Mapping[str, Any]) -> dict[str, Any]:
    """`tool_results` con la marca de que la alerta ya se ofreció, en este turno o en uno anterior (#213)."""
    if previous.get(OFFERED_KEY) or tool_results.get(OFFERED_KEY):
        return {**tool_results, OFFERED_KEY: True}
    return dict(tool_results)


# En inglés (#202, DT-55), y el español de antes. Esta regla reemplaza la respuesta del turno, así que va
# con frases que solo piden un aviso: "let me know" a secas no, porque "let me know the price" es otra cosa.
_REQUEST = re.compile(
    r"\b(avisame|avisenme|me avisas|me avisan|me avises|avisarme|notificame|alerta"
    r"|notify me|alert me|keep me posted|keep me updated|let me know (?:when|if (?:something|anything))"
    r"|send me an alert|(?:set up|create) an alert)\b")
_TYPES = {"venta": "for sale", "renta": "for rent", "preventa": "in pre-sale"}


def scan_every() -> timedelta:
    return timedelta(minutes=float(os.environ.get("ALERT_SCAN_MINUTES", "10")))


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def is_request(text: str) -> bool:
    """¿El lead pide que le avisen? Regla determinista, sin LLM."""
    return bool(_REQUEST.search(_normalize(text)))


def requested(text: str, flags: Mapping[str, bool] | None = None) -> bool:
    """¿El lead pide la alerta? Lo contesta el clasificador (`wants_alert`, DT-60), que ve también la oferta del
    turno anterior. Si no lo contestó (o el agente es `FakeAgent`), decide `is_request`."""
    answer = (flags or {}).get("wants_alert")
    return answer if isinstance(answer, bool) else is_request(text)


def has_filters(filters: Mapping[str, Any] | None) -> bool:
    """Una alerta sin ningún filtro avisaría de cualquier propiedad: no se guarda. El tipo de inmueble solo
    tampoco alcanza (#209): "cualquier apartment, en cualquier zona" avisaría de casi todo lo que entra. Se
    guarda junto con otro filtro (zona, operación, recámaras o presupuesto)."""
    return isinstance(filters, Mapping) and any(value is not None for key, value in filters.items() if key != "kind")


def create(session: Session, lead_id: uuid.UUID, conversation_id: uuid.UUID, filters: Mapping[str, Any]) -> None:
    """Guarda la alerta del lead (reemplaza la que tuviera) y se asegura de que haya un `alert_scan` pendiente."""
    deactivate_for_lead(session, lead_id)
    session.add(PropertyAlert(lead_id=lead_id, conversation_id=conversation_id,
                              expires_at=func.now() + timedelta(days=ALERT_DAYS),
                              **{key: filters.get(key) for key in
                                 ("zone", "type", "kind", "bedrooms", "budget_min", "budget_max")}))
    jobs.enqueue(session, ALERT_SCAN_KIND, SCAN_KEY, func.now() + scan_every())


def deactivate_for_lead(session: Session, lead_id: uuid.UUID) -> None:
    session.execute(update(PropertyAlert).where(PropertyAlert.lead_id == lead_id, PropertyAlert.active)
                    .values(active=False))


# Mismos criterios que `search_properties` del catálogo: zona por coincidencia parcial (en el barrio o en la
# ciudad de `address_approx`, DT-55), tipo de inmueble (#209), recámaras exactas y precio dentro del rango. "Nueva" = entró a
# `disponible` después de crearse la alerta, en el historial real: un evento de la historia sintética (N-9)
# no es una propiedad nueva para nadie.
_NEW_MATCHES = text("""
    SELECT a.id AS alert_id, a.lead_id, a.conversation_id, p.id AS property_id
    FROM property_alerts a
    JOIN leads l ON l.id = a.lead_id AND l.consent_status <> 'withdrawn'
    JOIN properties p ON p.status = 'disponible'
        AND (a.zone IS NULL OR p.zone ILIKE '%' || a.zone || '%' OR p.address_approx ILIKE '%' || a.zone || '%')
        AND (a.type IS NULL OR p.type = a.type)
        AND (a.kind IS NULL OR p.kind = a.kind)
        AND (a.bedrooms IS NULL OR p.bedrooms = a.bedrooms)
        AND (a.budget_min IS NULL OR p.price >= a.budget_min)
        AND (a.budget_max IS NULL OR p.price <= a.budget_max)
    WHERE a.active AND a.expires_at > :now
      AND EXISTS (SELECT 1 FROM property_status_events e
                  WHERE e.property_id = p.id AND e.to_status = 'disponible' AND e.changed_at > a.created_at
                    AND NOT e.synthetic)
      AND NOT EXISTS (SELECT 1 FROM scheduled_jobs j
                      WHERE j.kind = 'property_alert' AND j.dedupe_key = a.id::text || ':' || p.id)
    ORDER BY a.created_at, p.id
""")


def new_matches(session: Session, now: datetime) -> list[Any]:
    return list(session.execute(_NEW_MATCHES, {"now": now}))


def expire(session: Session, now: datetime) -> None:
    session.execute(update(PropertyAlert).where(PropertyAlert.active, PropertyAlert.expires_at <= now)
                    .values(active=False))


def any_active(session: Session, now: datetime) -> bool:
    return session.execute(select(exists().where(PropertyAlert.active, PropertyAlert.expires_at > now))).scalar()


def _price(value: Any) -> str:
    amount = Decimal(str(value))
    return f"${amount:,.0f}" if amount == amount.to_integral_value() else f"${amount:,.2f}"


def alert_text(prop: Mapping[str, Any]) -> str:
    """El aviso al lead, solo con datos del catálogo: pasa por los guardrails de salida antes de enviarse."""
    details = [_TYPES.get(prop.get("type"), "")]
    if prop.get("bedrooms") is not None:
        details.append(f"{prop['bedrooms']} bedroom" + ("" if prop["bedrooms"] == 1 else "s"))
    if prop.get("price") is not None:
        details.append(_price(prop["price"]))
    summary = ", ".join(detail for detail in details if detail)
    return (f"Something like what you were looking for just came up! {prop['title']} (ref. {prop['id']}), "
            f"in {prop['zone']}: {summary}. Would you like to hear more?")
