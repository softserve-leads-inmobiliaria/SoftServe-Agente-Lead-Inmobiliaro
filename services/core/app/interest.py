"""Qué propiedad y qué zona le interesan al lead, leído de las tools que se llamaron (#81).

Lógica pura sobre los `tool_results` del turno: sin base y sin red. La usa el turno para completar
`Lead.zone`, que es por donde el panel arma la cola de cada asesor (B-5), y B-7 para elegir a quién
notificar. También dice qué propiedades tocó el lead en el turno, para `property_interest` (N-4).
"""
from __future__ import annotations

from typing import Any, Mapping

from app.markers import KIND, TYPE
from app.visits import OFFER_KEY, VISIT_KEY

# La propiedad de interés más reciente, arrastrada de turno en turno en `last_tool_results` (B-8): ese
# campo se sobrescribe cada turno, y la visita se pide varios turnos después de ver la propiedad. El
# guardrail de factualidad (A-5) no lo mira: usa las tools del turno en curso y la memoria de hechos
# verificados (`app/facts.py`, #217), que guarda más campos y vence.
REMEMBERED_KEY = "property_of_interest"
# Marca de la recordada que salió de una búsqueda con varios resultados: el primero no es necesariamente el que
# mira el lead. Para la zona sirve; para agendar no (#247: "ok i wan to see it" agendaba otra propiedad).
GUESSED_KEY = "from_several"

# --- de qué propiedad habla el mensaje (`refers_to`, #247) ---------------------------------------------------
# "the cheaper one", "the studio", "ok i wan to see it": el código adivinaba con los resultados de las tools, y
# cada redacción nueva era un caso nuevo. Ahora lo decide el clasificador, en la llamada que ya hace cada turno,
# pero solo puede elegir entre las propiedades que la conversación ya mostró (la memoria de hechos, `facts.py`),
# y el código valida que la que eligió esté en la lista. Si no sabe cuál, contesta `unclear` y se pregunta.
UNCLEAR = "unclear"
MAX_SHOWN = 8  # las más recientes: la lista va en cada llamada al clasificador


def shown_properties(remembered: Mapping[str, Any],
                     listing: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Las propiedades que ya vio la conversación, de la más vieja a la más nueva: la del anuncio y la memoria."""
    shown: dict[str, dict[str, Any]] = {}
    for prop in [listing or {}, *remembered.values()]:
        prop_id = prop.get("id") if isinstance(prop, Mapping) else None
        if isinstance(prop_id, str) and prop_id and prop.get("title"):
            shown.pop(prop_id, None)
            shown[prop_id] = dict(prop)
    return list(shown.values())[-MAX_SHOWN:]


def shown_lines(shown: list[Mapping[str, Any]]) -> str:
    """Una línea por propiedad para el clasificador, con lo que distingue a una de otra ("the cheaper one",
    "the house", "the one in Laureles"). Los valores internos en español van en inglés (DT-55)."""
    lines = []
    for prop in shown:
        details = [KIND.get(prop.get("kind"), ""), TYPE.get(prop.get("type"), ""), prop.get("zone") or ""]
        if isinstance(prop.get("price"), (int, float)):
            details.append(f"${prop['price']:,.0f}")
        if isinstance(prop.get("bedrooms"), (int, float)):
            details.append(f"{prop['bedrooms']:g} bedrooms")
        lines.append(f"- {prop['id']}: {prop['title']} ({', '.join(d for d in details if d)})")
    return "\n".join(lines)


def refers_to(raw: Any, shown: list[Mapping[str, Any]]) -> str | None:
    """El `refers_to` del clasificador, validado: el id de una propiedad mostrada, `UNCLEAR`, o None.

    Un id que no está en la lista no se usa: el modelo no puede traer una propiedad que el lead nunca vio.
    """
    if raw == UNCLEAR:
        return UNCLEAR
    return raw if isinstance(raw, str) and any(prop.get("id") == raw for prop in shown) else None


def focused_property(prop_id: str | None, shown: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """La propiedad mostrada a la que se refiere el mensaje, con lo que se guarda de ella (sin estado), o None."""
    found = next((prop for prop in shown if prop_id and prop.get("id") == prop_id), None)
    if found is None:
        return None
    return {key: found[key] for key in ("id", "title", "zone") if found.get(key)}


def property_of_interest(tool_results: Mapping[str, Any], *, sure: bool = False) -> dict[str, Any] | None:
    """La propiedad que el lead está mirando en este turno, o None.

    `get_property` manda sobre `search_properties`: pedir una propiedad concreta es una señal de
    interés más fuerte que un listado de resultados. La de una visita (B-8) va entre las dos, y la
    recordada de turnos anteriores (`REMEMBERED_KEY`) solo cuenta si el turno no trae ninguna.

    Con `sure`, una búsqueda con varios resultados no dice cuál es (ni la recordada que salió de una): None.
    """
    looked_up = tool_results.get("get_property")
    if isinstance(looked_up, Mapping) and looked_up.get("id"):
        return dict(looked_up)
    for key in (VISIT_KEY, OFFER_KEY):
        visit = tool_results.get(key)
        prop = visit.get("property") if isinstance(visit, Mapping) else None
        if isinstance(prop, Mapping) and prop.get("id"):
            return dict(prop)

    search = tool_results.get("search_properties")
    if isinstance(search, Mapping):
        found = search.get("properties")
        if isinstance(found, list) and found and isinstance(found[0], Mapping):
            return None if sure and len(found) > 1 else dict(found[0])
    remembered = tool_results.get(REMEMBERED_KEY)
    if not isinstance(remembered, Mapping) or not remembered.get("id") or (sure and remembered.get(GUESSED_KEY)):
        return None
    return {key: value for key, value in remembered.items() if key != GUESSED_KEY}


def remember_property(tool_results: Mapping[str, Any], previous: Mapping[str, Any],
                      focus: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`tool_results` con la propiedad de interés más reciente: la de este turno o la que ya se recordaba.

    `focus` es la propiedad mostrada a la que se refirió el mensaje (`refers_to`): manda sobre una búsqueda del
    turno y sobre la recordada, no sobre una ficha que el agente consultó en el turno. Se guarda sin `status`: en
    un turno posterior ya puede estar viejo.
    """
    looked_up = tool_results.get("get_property")
    if focus and focus.get("id") and not (isinstance(looked_up, Mapping) and looked_up.get("id")):
        return {**tool_results, REMEMBERED_KEY: dict(focus)}
    source = tool_results if property_of_interest(tool_results) else previous
    found = property_of_interest(source)
    if not found or not found.get("title"):
        return dict(tool_results)
    entry: dict[str, Any] = {key: found[key] for key in ("id", "title", "zone") if found.get(key)}
    if not property_of_interest(source, sure=True):
        entry[GUESSED_KEY] = True
    return {**tool_results, REMEMBERED_KEY: entry}


def listing_greeting(listing: Mapping[str, Any]) -> str:
    """Saludo del lead que llega por el anuncio de una propiedad (#122). Título y referencia salen del
    catálogo; la referencia queda en el historial para que el LLM sepa de qué propiedad se habla después."""
    return (f"Hi! I'm the agency's assistant. I see you came from the listing for "
            f"{listing['title']} (ref. {listing['id']}). What would you like to know?")


def zone_of_interest(tool_results: Mapping[str, Any]) -> str | None:
    """Zona de la propiedad de interés, si el turno llamó alguna tool que la traiga."""
    found = property_of_interest(tool_results)
    zone = found.get("zone") if found else None
    return zone if isinstance(zone, str) and zone.strip() else None


def _id(prop: Any) -> str | None:
    found = prop.get("id") if isinstance(prop, Mapping) else None
    return found if isinstance(found, str) and found else None


def interactions(tool_results: Mapping[str, Any], listing: Mapping[str, Any] | None = None,
                 booked: Mapping[str, Any] | None = None) -> list[tuple[str, str]]:
    """Las propiedades que el lead tocó en el turno, como `(property_id, tipo)` y sin repetidos (N-4).

    - `anuncio`: la del *deep-link* (`listing`, #122).
    - `consulta`: la de `get_property`. Si es la misma del anuncio, ya cuenta como `anuncio`: el turno la
      suma a los resultados aunque el agente no la haya consultado (`turn._with_listing`).
    - `busqueda`: cada propiedad que devolvió `search_properties`.
    - `visita`: la de la visita que el turno reservó (`booked`, B-8).

    `tool_results` guarda el último resultado de cada tool, así que dos `get_property` en un mismo turno
    dejan solo el último. No mira la propiedad recordada de turnos anteriores (`REMEMBERED_KEY`): eso no
    es una interacción de este turno.
    """
    found: list[tuple[str, str]] = []
    listed = _id(listing)
    if listed:
        found.append((listed, "anuncio"))
    looked_up = _id(tool_results.get("get_property"))
    if looked_up and looked_up != listed:
        found.append((looked_up, "consulta"))
    search = tool_results.get("search_properties")
    results = search.get("properties") if isinstance(search, Mapping) else None
    for prop in results if isinstance(results, list) else ():
        if _id(prop):
            found.append((_id(prop), "busqueda"))
    visited = _id(booked)
    if visited:
        found.append((visited, "visita"))
    return list(dict.fromkeys(found))
