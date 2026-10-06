"""Guardrail de salida — dirección reservada (A-6): ninguna dirección exacta en prosa si el lead no
está calificado, venga o no de una tool.

`address_exact` nunca sale del catálogo salvo con `lead_qualified=true` (`services/catalog/app/
service.py`): no hay ningún valor real contra el que comparar en `last_tool_results`, así que esto no
es literal como A-5 (DT-23) — detecta la FORMA de una dirección exacta (tipo de vía + nombre + número),
no si coincide con un dato real. Eso también cubre el caso que A-5 no puede: un número de calle que el
propio turno ya "permite" por casualidad (coincide con el precio, los m² u otro dato real) igual
delata una dirección si aparece pegado a un tipo de vía.

Una referencia aproximada sin número ("cerca de Avenida Nutibara") no dispara nada: así es como el
catálogo distingue `address_approx` (público) de `address_exact` (reservado) — el número es lo que
hace la diferencia, no el nombre de la calle.
"""
from __future__ import annotations

import re

from app.factuality import Violation

_STREET_TYPES = (
    "calle", "avenida", "av", "boulevard", "blvd", "circuito", "privada", "andador",
    "camino", "paseo", "prolongación", "prolongacion", "callejón", "callejon", "calzada",
    # Colombia (DT-55): "Carrera 37 # 8A-24", "Diagonal 75B # 2-30". Sin "tv" ni "cr" sueltos: chocan
    # con texto común ("TV de 55 pulgadas").
    "carrera", "cra", "kra", "kr", "diagonal", "transversal", "circular", "autopista",
)
_STREET_PREFIX = "|".join(sorted(_STREET_TYPES, key=len, reverse=True))
# Palabras que, justo después del número, revelan que en realidad es una distancia ("a 5 minutos",
# "a 10 cuadras") y no una dirección — el único falso positivo real de este patrón tan simple.
_DISTANCE_UNITS = r"min(?:uto)?s?|cuadras?|km|kil[oó]metros?|metros?"

_ADDRESS = re.compile(
    rf"\b(?:{_STREET_PREFIX})\.?\s+(?:[^\d\s,.]+\s+){{0,3}}\d+\w*"
    rf"(?!\s*(?:{_DISTANCE_UNITS})\b)",
    re.IGNORECASE,
)


def extract_addresses(text: str) -> set[str]:
    """Fragmentos con forma de dirección exacta (tipo de vía + nombre + número) en `text`."""
    return {match.group().strip() for match in _ADDRESS.finditer(text or "")}


def check(text: str, lead_qualified: bool, lead_text: str = "") -> list[Violation]:
    """Direcciones exactas en `text` si el lead no está calificado. Vacío si `lead_qualified=true`.

    Repetir una dirección que el propio lead ya escribió (p. ej. la suya, para preguntar cerca de
    dónde busca) no es una fuga — mismo criterio que A-5 con los números del lead.
    """
    if lead_qualified:
        return []
    allowed = extract_addresses(lead_text)
    return [Violation("address", value) for value in extract_addresses(text) if value not in allowed]
