"""Memoria de hechos verificados de la conversación (#217): lo que las tools ya dijeron de cada propiedad.

La factualidad (A-5) revisa la respuesta contra las tools del turno en curso. Pero el modelo ve toda la
conversación, y repetir el precio que `get_property` devolvió dos turnos antes no es inventarlo. Este módulo
guarda, de cada propiedad que devolvió una tool, los campos que se pueden citar, y se los pasa a la
factualidad como respaldo de los números en los turnos siguientes.

- Se guarda en `last_tool_results` (`FACTS_KEY`), que ya viaja de turno en turno (B-8): sin migración.
- **Nunca el estado.** Una propiedad disponible hace una hora puede estar apartada ahora: el estado se sigue
  exigiendo de las tools del turno, que el `AvailabilityGuard` revalida (A-4). Tampoco la dirección exacta
  (A-6) ni la foto: solo los campos de `CITABLE`.
- **Vencen.** Un hecho visto hace más de `TTL` deja de valer: el precio puede haber cambiado, y el modelo
  tiene que volver a consultarlo.
- Como mucho `MAX_PROPERTIES`, las vistas más recientemente, para que el JSONB no crezca sin límite.

Lógica pura: el turno le pasa la hora (`now`).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterator, Mapping

FACTS_KEY = "verified_facts"
SEEN_AT = "seen_at"
TTL = timedelta(hours=24)
MAX_PROPERTIES = 20
# Lo que se puede citar de una propiedad (`contracts/mcp/get_property.schema.json`). Sin `status`, sin
# `address_exact` y sin `photo_url` (una URL trae números que no son de la propiedad).
CITABLE = ("id", "title", "type", "kind", "zone", "price", "m2", "bedrooms", "bathrooms", "amenities",
           "address_approx", "requirements")


def _properties(tool_results: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    """Las propiedades que trajeron las tools del turno: la ficha, los resultados de la búsqueda y sus alternativas."""
    looked_up = tool_results.get("get_property")
    if isinstance(looked_up, Mapping):
        yield looked_up
    search = tool_results.get("search_properties")
    if isinstance(search, Mapping):
        for key in ("properties", "alternatives"):
            found = search.get(key)
            for prop in found if isinstance(found, list) else ():
                if isinstance(prop, Mapping):
                    yield prop


def _fresh(facts: Any, now: datetime) -> dict[str, dict[str, Any]]:
    """Los hechos guardados que no vencieron."""
    if not isinstance(facts, Mapping):
        return {}
    fresh = {}
    for prop_id, fact in facts.items():
        try:
            recent = now - datetime.fromisoformat(fact[SEEN_AT]) <= TTL
        except (KeyError, TypeError, ValueError):  # sin hora, o una hora que no se entiende: no vale
            continue
        if recent:
            fresh[prop_id] = dict(fact)
    return fresh


def carry(tool_results: Mapping[str, Any], current: Mapping[str, Any], previous: Mapping[str, Any],
          now: datetime) -> dict[str, Any]:
    """`tool_results` con la memoria al día: la del turno anterior sin lo vencido, más lo que trajo este turno.

    `current` son las tools de este turno; `previous`, el `last_tool_results` del turno anterior. Lo de este
    turno pisa lo recordado de la misma propiedad, y su hora se renueva.
    """
    facts = _fresh(previous.get(FACTS_KEY), now)
    for prop in _properties(current):
        prop_id = prop.get("id")
        if isinstance(prop_id, str) and prop_id:
            facts.pop(prop_id, None)  # al final: es la más reciente
            facts[prop_id] = {**{k: prop[k] for k in CITABLE if prop.get(k) is not None}, SEEN_AT: now.isoformat()}
    if not facts:
        return dict(tool_results)
    newest = sorted(facts.items(), key=lambda item: item[1][SEEN_AT])[-MAX_PROPERTIES:]
    return {**tool_results, FACTS_KEY: dict(newest)}


def evidence(previous: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Lo que la factualidad puede usar de los turnos anteriores: los hechos vigentes, sin la hora en que se vieron
    (sus dígitos no son de ninguna propiedad)."""
    return {prop_id: {k: v for k, v in fact.items() if k != SEEN_AT}
            for prop_id, fact in _fresh(previous.get(FACTS_KEY), now).items()}
