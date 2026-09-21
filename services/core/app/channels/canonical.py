"""Traducción entre Telegram y el evento canónico CT-1 (B-3).

Módulo puro: no importa `telegram` ni toca la base. Recibe primitivos (los extrae `telegram.py`) y
devuelve dicts que cumplen `contracts/event.schema.json`, para que `core` nunca vea un tipo del SDK.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

CHANNEL = "telegram"
SCHEMA_VERSION = 1

_DEEP_LINK_PREFIXES = {"prop_": "property_id", "camp_": "utm_campaign"}


def parse_deep_link(payload: str | None) -> dict[str, str]:
    """`?start=prop_00231` -> {"property_id": "prop_00231"}. Payload desconocido o vacío -> {}.

    El `property_id` conserva el prefijo `prop_` porque así son los ids en `properties` (C-3).
    """
    if not payload:
        return {}
    for prefix, field in _DEEP_LINK_PREFIXES.items():
        if payload.startswith(prefix) and len(payload) > len(prefix):
            return {field: payload if field == "property_id" else payload[len(prefix):]}
    return {}


def to_canonical(
    *,
    lead_id: uuid.UUID,
    conversation_id: uuid.UUID,
    text: str,
    property_id: str | None = None,
    utm_campaign: str | None = None,
    timestamp: datetime | None = None,
    event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Evento canónico inbound. Los opcionales se omiten si son None (additionalProperties: false)."""
    moment = timestamp or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(event_id or uuid.uuid4()),
        "conversation_id": str(conversation_id),
        "lead_id": str(lead_id),
        "channel": CHANNEL,
        "direction": "inbound",
        "text": text,
        "timestamp": moment.isoformat(),
    }
    if property_id:
        event["property_id"] = property_id
    if utm_campaign:
        event["utm_campaign"] = utm_campaign
    return event


def from_canonical(event: Mapping[str, Any]) -> str:
    """Texto a enviar por Telegram desde un evento outbound."""
    if event.get("direction") != "outbound":
        raise ValueError("solo se puede responder por el canal un evento outbound")
    if event.get("channel") != CHANNEL:
        raise ValueError(f"el evento no es del canal {CHANNEL}: {event.get('channel')!r}")
    return str(event["text"])
