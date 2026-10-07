"""Datos de la propiedad por construcción (DT-60, #243): el modelo escribe marcadores y el código pone los valores.

El modelo no escribe el precio ni los dormitorios de una propiedad: escribe `{prop_00231.price}`, y este módulo lo
completa con lo que devolvieron las tools ("$185,000") antes de mandar la respuesta. Así, un dato inventado, o el de
otra propiedad, no se puede escribir: el marcador nombra la propiedad y el campo.

- **De dónde salen los valores.** De las tools del turno y de la memoria de hechos (`app/facts.py`, DT-59). El
  estado, solo de las tools del turno (A-4), igual que en la factualidad.
- **Qué se bloquea** (`check`). Un marcador que no se puede completar, un número escrito fuera de un marcador
  que no dijo el lead, y una referencia `prop_…` que ninguna tool dio. La numeración de una lista ("1.", "2)") no
  es un dato.
- **El formato** lo arma el código: el dinero con `$` y separador de miles, los valores internos en inglés
  (`apartado` → "reserved"), las listas con "and".

Lógica pura: sin red ni base. `BedrockAgent` (`app/agent.py`) decide qué hacer con las violaciones (DT-23).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

from app.factuality import _NUMBER, Violation, _normalize_number, extract_numbers

# `{prop_00231.price}`, `{prop_00231.requirements.down_payment_min}`, `{search.count}`, `{zones}`.
MARKER = re.compile(r"\{([A-Za-z][\w-]*)(?:\.([A-Za-z_][\w.]*))?\}")
REFERENCE = re.compile(r"\bprop_\w+")
# "1. Apartment in El Poblado": la numeración de una lista no es un dato.
_LIST_NUMBER = re.compile(r"^[ \t]*(?:[-*][ \t]+)?(\d+)[.)](?=\s)", re.MULTILINE)

STATUS = {"disponible": "available", "apartado": "reserved", "cerrado": "off the market",
          "sin_verificar": "unverified"}
TYPE = {"venta": "for sale", "renta": "for rent", "preventa": "pre-sale"}
KIND = {"departamento": "apartment", "casa": "house", "terreno": "land", "local": "commercial space",
        "estudio": "studio"}

MONEY = frozenset({"price", "requirements.down_payment_min", "requirements.monthly_income_min"})
PERCENT = frozenset({"requirements.down_payment_percent"})
NUMBERS = frozenset({"m2", "bedrooms", "bathrooms", "requirements.income_multiple"})
TEXTS = frozenset({"title", "zone", "address_approx", "address_exact", "description"})
# Los campos que se pueden pedir de una propiedad o una promoción. El prompt los enumera (`CHAT_SYSTEM`).
FIELDS = MONEY | PERCENT | NUMBERS | TEXTS | {"amenities", "type", "kind", "status", "valid_until"}

# Los requisitos también se pueden pedir sin el prefijo: `{prop_00231.down_payment_min}`. Así se los enumera el prompt.
ALIASES = {f.removeprefix("requirements."): f for f in FIELDS if f.startswith("requirements.")}

# Un `$` antes del marcador del dinero, o un `%` después del porcentaje: el valor ya los trae.
_MONEY_SIGN = re.compile(r"\$\s*(?=\{[^{}]*\.(?:price|down_payment_min|monthly_income_min)\})")
_PERCENT_SIGN = re.compile(r"(\{[^{}]*\.down_payment_percent\})\s*%")


@dataclass
class Sources:
    """Lo que se puede citar en el turno: los campos de cada propiedad o promoción, por su `id`."""

    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)  # solo de las tools del turno
    search_count: int | None = None
    zones: list[Mapping[str, Any]] | None = None


def _add(sources: Sources, item: Any) -> None:
    if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
        return
    record = sources.records.setdefault(item["id"], {})
    record.update({k: v for k, v in item.items() if k != "status" and v is not None})
    if isinstance(item.get("status"), str):
        sources.statuses[item["id"]] = item["status"]


def build(payloads: Iterable[tuple[str, Mapping[str, Any]]], remembered: Mapping[str, Any] | None = None) -> Sources:
    """Las fuentes del turno: `payloads` son las respuestas de las tools, en orden (`ChatResult.payloads`), y
    `remembered` los hechos de turnos anteriores (`facts.evidence`), que nunca traen el estado."""
    sources = Sources()
    for prop_id, fact in (remembered or {}).items():
        if isinstance(fact, Mapping):
            _add(sources, {**fact, "id": prop_id, "status": None})
    for name, payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        if name == "get_property":
            _add(sources, payload)
        elif name == "search_properties":
            found = [p for p in payload.get("properties") or () if isinstance(p, Mapping)]
            alternatives = [p for p in payload.get("alternatives") or () if isinstance(p, Mapping)]
            for item in (*found, *alternatives, *(payload.get("unavailable") or ())):
                _add(sources, item)
            sources.search_count = len(found or alternatives)
        elif name == "get_availability" and isinstance(payload.get("property_id"), str):
            if isinstance(payload.get("status"), str):
                sources.statuses[payload["property_id"]] = payload["status"]
        elif name == "get_promotions":
            for promo in payload.get("promotions") or ():
                _add(sources, promo)
        elif name == "list_zones" and isinstance(payload.get("zones"), list):
            sources.zones = [z for z in payload["zones"] if isinstance(z, Mapping) and z.get("zone")]
    return sources


def _number(value: Any, money: bool = False) -> str | None:
    """"185,000", "78.5"; el dinero con centavos, siempre con dos decimales ("1,150.50")."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = Decimal(str(value))
    if number == number.to_integral_value():
        return f"{number:,.0f}"
    return f"{number:,.2f}" if money else f"{number:,.2f}".rstrip("0")


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _zones(zones: list[Mapping[str, Any]]) -> str | None:
    by_city: dict[str, list[str]] = {}
    for zone in zones:
        by_city.setdefault(str(zone.get("city") or ""), []).append(str(zone["zone"]))
    parts = [f"{_join(names)} in {city}" if city else _join(names) for city, names in by_city.items()]
    return "; ".join(parts) if parts else None


def _value(sources: Sources, ref: str, path: str | None) -> str | None:
    """El valor del marcador tal como lo lee el lead, o `None` si no se puede completar."""
    if path is None:
        return _zones(sources.zones) if ref == "zones" and sources.zones else None
    if ref == "search":
        return str(sources.search_count) if path == "count" and sources.search_count is not None else None
    path = ALIASES.get(path, path)
    if path not in FIELDS:
        return None
    if path == "status":
        return STATUS.get(sources.statuses.get(ref, ""))
    record = sources.records.get(ref)
    if record is None:
        return None
    value: Any = record
    for key in path.split("."):
        value = value.get(key) if isinstance(value, Mapping) else None
    if path in MONEY:
        return f"${amount}" if (amount := _number(value, money=True)) else None
    if path in PERCENT:
        return f"{amount}%" if (amount := _number(value)) else None
    if path in NUMBERS:
        return _number(value)
    if path == "type":
        return TYPE.get(value) if isinstance(value, str) else None
    if path == "kind":
        return KIND.get(value) if isinstance(value, str) else None
    if path == "amenities":
        names = [a for a in value if isinstance(a, str) and a] if isinstance(value, list) else []
        return _join(names) if names else None
    if path == "valid_until":
        try:
            day = date.fromisoformat(str(value))
        except ValueError:
            return None
        return f"{day:%B} {day.day}, {day.year}"
    return value if isinstance(value, str) and value.strip() else None


def _tidy(text: str) -> str:
    return _PERCENT_SIGN.sub(r"\1", _MONEY_SIGN.sub("", text))


def render(text: str, sources: Sources) -> str:
    """`text` con cada marcador completado. Uno que no se puede completar queda como está: `check` lo bloquea."""
    def fill(match: re.Match[str]) -> str:
        value = _value(sources, match.group(1), match.group(2))
        return match.group() if value is None else value

    return MARKER.sub(fill, _tidy(text))


def outside(text: str) -> str:
    """Lo que escribió el modelo fuera de los marcadores. Cada marcador queda como un espacio."""
    return MARKER.sub(" ", _tidy(text))


def _suggest(number: str, sources: Sources) -> str | None:
    """El marcador que el modelo tendría que haber usado, si el número es un único campo de una única propiedad."""
    found = [f"{{{ref}.{path.removeprefix('requirements.')}}}" for ref, record in sources.records.items()
             for path in sorted(MONEY | PERCENT | NUMBERS) if _same(number, record, path)]
    if sources.search_count is not None and str(sources.search_count) == number:
        found.append("{search.count}")
    return found[0] if len(found) == 1 else None


def _same(number: str, record: Mapping[str, Any], path: str) -> bool:
    value: Any = record
    for key in path.split("."):
        value = value.get(key) if isinstance(value, Mapping) else None
    return (not isinstance(value, bool) and isinstance(value, (int, float, Decimal))
            and _normalize_number(str(value)) == number)


def lead_numbers(texts: Iterable[str]) -> set[str]:
    """Los números que escribió el lead: los suyos se pueden repetir fuera de un marcador."""
    return {n for text in texts for n in extract_numbers(text)}


def check(text: str, sources: Sources, lead_said: set[str], known_refs: Iterable[str] = ()) -> list[Violation]:
    """Lo que el modelo escribió como dato sin un marcador que lo respalde.

    `lead_said` son los números que escribió el lead en la conversación (`lead_numbers`); `known_refs`, las
    referencias que puede nombrar sin que una tool las haya dado (la del anuncio, las que escribió el lead).
    """
    violations = [Violation("marker", m.group()) for m in MARKER.finditer(text)
                  if _value(sources, m.group(1), m.group(2)) is None]
    bare = outside(text)
    listed = {m.start(1) for m in _LIST_NUMBER.finditer(bare)}
    for match in _NUMBER.finditer(bare):
        number = _normalize_number(match.group())
        if number is None or number in lead_said or match.start() in listed:
            continue
        violations.append(Violation("number", number, hint=_suggest(number, sources)))
    allowed = set(sources.records) | set(known_refs)
    violations += [Violation("reference", ref) for ref in dict.fromkeys(REFERENCE.findall(bare))
                   if ref not in allowed]
    return list(dict.fromkeys(violations))
