"""Qué propiedad y qué zona le interesan al lead, leído de las tools que se llamaron (#81).

Lógica pura sobre los `tool_results` del turno: sin base y sin red. La usa el turno para completar
`Lead.zone`, que es por donde el panel arma la cola de cada asesor (B-5), y B-7 para elegir a quién
notificar.
"""
from __future__ import annotations

from typing import Any, Mapping


def property_of_interest(tool_results: Mapping[str, Any]) -> dict[str, Any] | None:
    """La propiedad que el lead está mirando en este turno, o None.

    `get_property` manda sobre `search_properties`: pedir una propiedad concreta es una señal de
    interés más fuerte que un listado de resultados.
    """
    looked_up = tool_results.get("get_property")
    if isinstance(looked_up, Mapping) and looked_up.get("id"):
        return dict(looked_up)

    search = tool_results.get("search_properties")
    if isinstance(search, Mapping):
        found = search.get("properties")
        if isinstance(found, list) and found and isinstance(found[0], Mapping):
            return dict(found[0])
    return None


def listing_greeting(listing: Mapping[str, Any]) -> str:
    """Saludo del lead que llega por el anuncio de una propiedad (#122). Título y referencia salen del
    catálogo; la referencia queda en el historial para que el LLM sepa de qué propiedad se habla después."""
    return (f"¡Hola! Soy el asistente de la inmobiliaria. Veo que llegaste por el anuncio de "
            f"{listing['title']} (ref. {listing['id']}). ¿Qué te gustaría saber?")


def zone_of_interest(tool_results: Mapping[str, Any]) -> str | None:
    """Zona de la propiedad de interés, si el turno llamó alguna tool que la traiga."""
    found = property_of_interest(tool_results)
    zone = found.get("zone") if found else None
    return zone if isinstance(zone, str) and zone.strip() else None
