"""Guardrail de salida — factualidad (A-5): ningún número o estado inventado en la respuesta del LLM.

Todo número (precio, m², recámaras, porcentaje de enganche...) y todo estado de propiedad que
mencione la respuesta candidata tiene que existir literalmente en `last_tool_results` de este mismo
turno, o en lo que el propio lead escribió (repetir su presupuesto o lo que pidió no es inventar). Un
número también vale si una tool lo dio en un turno anterior de la conversación (`app/facts.py`, #217);
un estado, no.
No hay comprensión semántica: es una comparación literal de texto, deliberadamente estricta.

`BedrockAgent` (`app/agent.py`) es quien decide qué hacer con las violaciones que reporta `check()`
(regenerar, degradar); este módulo solo detecta.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

# Un número: agrupado con comas ("2,350,000") o simple ("78.5", "3"). Sin arrastrar dígitos de un id
# ("prop_00231") ni de una palabra: `\w` incluye `_`, así que el guion bajo ya bloquea esos casos.
_NUMBER = re.compile(r"(?<!\w)\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\w)|(?<!\w)\d+(?:\.\d+)?(?!\w)")

# Formas gramaticales -> estado canónico de `db.models.PROPERTY_STATUSES` (más "sin_verificar", A-4).
_STATUS_WORDS = {
    "disponible": "disponible", "disponibles": "disponible",
    "apartado": "apartado", "apartada": "apartado", "apartados": "apartado", "apartadas": "apartado",
    "cerrado": "cerrado", "cerrada": "cerrado", "cerrados": "cerrado", "cerradas": "cerrado",
    "sin verificar": "sin_verificar", "sin_verificar": "sin_verificar",
    # Inglés (#202, DT-55). "rented" solo con lo que lo vuelve un estado ("it can be rented" no lo es), y
    # sin "closed": "closed kitchen" o "closed parking" no hablan de la operación.
    "available": "disponible", "reserved": "apartado", "on hold": "apartado",
    "sold": "cerrado", "already rented": "cerrado", "been rented": "cerrado", "is rented": "cerrado",
    "was rented": "cerrado", "off the market": "cerrado",
    "unverified": "sin_verificar", "not verified": "sin_verificar",
}
# Negado no es afirmar el estado: "that one is not available, it is reserved" solo dice `apartado`.
_NOT_BEFORE = r"(?<!not )(?<!n't )(?<!n’t )(?<!no longer )"
# "Available" también es el dinero o el tiempo del lead: "how much do you have available for the down
# payment?" o "are you available on Friday?" no hablan de la propiedad (#202, visto en vivo). Tampoco "what's
# available": es el inventario en general, no el estado de una propiedad (#213, visto en vivo).
_NOT_THE_PROPERTY = re.compile(r"(?:\bhave|\bhas|\byou|\byou're|\bwe're|\bthey're|\bwhat's|\bwhat’s|\bwhat is)\s+$",
                               re.IGNORECASE)
_STATUS = re.compile(_NOT_BEFORE + r"\b(" + "|".join(sorted(_STATUS_WORDS, key=len, reverse=True)) + r")\b",
                     re.IGNORECASE)
# Tampoco el "available" que califica a otras propiedades, en plural: "other available houses", "no other 4-bedroom
# houses available". Al ofrecer alternativas de una propiedad apartada no habla de esa (#220, visto en la evaluación
# en vivo). En singular sí es el estado: "an available house" o "this one is available" siguen contando.
_INVENTORY = r"(?:houses|homes|properties|options|apartments|studios|rentals|listings|units|lots|spaces|places|ones)"
_INVENTORY_NEXT = re.compile(r"\s+" + _INVENTORY + r"\b", re.IGNORECASE)
_OTHER_INVENTORY_BEFORE = re.compile(r"\b(?:other|more|any|no)(?:\s+[\w-]+){0,3}?\s+" + _INVENTORY + r"\s+$",
                                     re.IGNORECASE)


def _not_the_property(text: str, match: re.Match[str]) -> bool:
    """Un "available" que no afirma el estado de la propiedad de la que se habla."""
    before = text[:match.start()]
    return bool(_NOT_THE_PROPERTY.search(before) or _OTHER_INVENTORY_BEFORE.search(before)
                or _INVENTORY_NEXT.match(text, match.end()))


@dataclass(frozen=True)
class Violation:
    kind: str  # "number" | "status" | "address" (app/address_guard.py, A-6) | "promise" | "amenity" (app/promise_guard.py, A-7)
    value: str  # tal como se detectó, normalizado — nunca el mensaje completo


def _normalize_number(raw: str) -> str | None:
    try:
        value = Decimal(str(raw).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None
    return format(value.normalize(), "f")


def extract_numbers(text: str) -> set[str]:
    """Números que aparecen en `text`, normalizados (sin comas, sin ceros de más)."""
    return {n for match in _NUMBER.finditer(text or "") if (n := _normalize_number(match.group())) is not None}


def extract_statuses(text: str) -> set[str]:
    """Palabras de estado de propiedad que aparecen en `text`, en su forma canónica."""
    text = text or ""
    return {_STATUS_WORDS[m.group(1).lower()] for m in _STATUS.finditer(text)
            if not (m.group(1).lower() == "available" and _not_the_property(text, m))}


# Listas cuyo TAMAÑO es un número que se puede citar ("tengo 3 opciones"). Deliberadamente acotada:
# `unavailable` (A-4) o `amenities` son metadata, no algo que el agente deba citar como una cantidad,
# y contarlas igual haría que casi cualquier "1" o "2" quedara siempre permitido en cualquier turno.
_COUNTABLE_LISTS = {"properties", "alternatives", "promotions"}


def _walk(value: Any, numbers: set[str], statuses: set[str], key: str | None = None) -> None:
    if isinstance(value, bool):  # bool es subclase de int: se descarta antes de tratarlo como número
        return
    if isinstance(value, (int, float)):
        if (n := _normalize_number(str(value))) is not None:
            numbers.add(n)
        return
    if isinstance(value, str):
        numbers.update(extract_numbers(value))
        statuses.update(extract_statuses(value))
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "status" and isinstance(v, str):
                statuses.add(v)
            _walk(v, numbers, statuses, key=k)
        return
    if isinstance(value, list):
        if key in _COUNTABLE_LISTS and (n := _normalize_number(str(len(value)))) is not None:
            numbers.add(n)
        for v in value:
            _walk(v, numbers, statuses, key=key)


def literal_values(tool_results: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """(números, estados) que de verdad salieron de las tools de este turno."""
    numbers: set[str] = set()
    statuses: set[str] = set()
    _walk(dict(tool_results), numbers, statuses)
    return numbers, statuses


def check(text: str, tool_results: Mapping[str, Any], lead_text: str = "",
          remembered: Mapping[str, Any] | None = None) -> list[Violation]:
    """Números/estados de `text` que no están respaldados por `tool_results` ni por lo que dijo el lead.

    `remembered` son los hechos que las tools dieron en turnos anteriores (`app/facts.py`, #217): respaldan
    números, nunca estados. El estado de una propiedad se exige siempre de las tools de este turno.
    """
    tool_numbers, tool_statuses = literal_values(tool_results)
    allowed_numbers = tool_numbers | literal_values(remembered or {})[0] | extract_numbers(lead_text)
    allowed_statuses = tool_statuses | extract_statuses(lead_text)
    violations = [Violation("number", n) for n in extract_numbers(text) if n not in allowed_numbers]
    violations += [Violation("status", s) for s in extract_statuses(text) if s not in allowed_statuses]
    return violations
