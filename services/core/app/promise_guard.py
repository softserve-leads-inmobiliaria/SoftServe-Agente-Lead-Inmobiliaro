"""Guardrail de salida — promesas no autorizadas (A-7): frases que comprometen a la agencia con algo
que el sistema no puede garantizar hoy (`docs/DESIGN/guardrails.md` §2.2, 8 *claims*).

Dos mecanismos:
- **Frases siempre prohibidas** (*claims* 1, 2, 3, 4, 7): no hay ninguna tool ni dato del sistema que
  las respalde hoy — no existe una tool de negociar precio ni de "apartar" una propiedad, y el
  catálogo (`services/catalog/app/data.py`) no tiene ningún campo de política de mascotas. Se
  bloquean sin mirar `last_tool_results`, mismo estilo de frases que el guardrail de entrada
  (`app/input_guard.py`).
- **Amenidad no respaldada** (*claim* 5): comparación literal contra `amenities` de este turno (mismo
  principio que A-5, DT-23), acotada al vocabulario real del catálogo — no NLP.

Los dos mecanismos comparten una salvedad: negarlo ("no tiene alberca") o dudarlo ("voy a confirmar
si aceptan mascotas") no es prometer — una palabra de negación o de duda hasta 3 palabras antes de la
frase la excluye (`_unhedged`). Es el mismo límite conocido que A-5/A-6: un patrón de texto, no NLP.

De los 8 *claims*, esta historia cubre 6 con código (1, 2, 3, 4, 5, 7). El estado real de cada uno,
incluidos los dos que quedan fuera (6 y 8) y por qué, vive en `docs/DESIGN/guardrails.md` §2.2 y §3 —
no solo aquí (ver DT-26 y la nota de v1 en `docs/STORIES/A-07.md`).
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from app.factuality import Violation
from app.input_guard import normalize

# Negación o cobertura ("no", "sin", "confirmar si...") hasta 3 palabras antes de la frase: dudarlo o
# negarlo no es prometerlo. "si" cubre tanto "no sé si" como "sí, aceptamos" (sin acento, ambiguo) —
# lo segundo lleva coma inmediatamente después, que rompe la ventana y sigue contando como violación.
_HEDGE_BEFORE = re.compile(
    r"\b(?:no|sin|nunca|jamas|si|puede que|tal vez|quiza|confirmar|preguntar|consultar|verificar)\b"
    r"(?:\s+\w+){0,3}\s*$"
)


def _unhedged(pattern: re.Pattern[str], normalized: str) -> bool:
    """True si `pattern` aparece en `normalized` sin una negación/duda justo antes."""
    for match in pattern.finditer(normalized):
        before = normalized[max(0, match.start() - 40):match.start()]
        if not _HEDGE_BEFORE.search(before):
            return True
    return False


# --- claims 1, 2, 3, 4, 7: nada los respalda hoy, se bloquean siempre -------------------------------
_ALWAYS_PROHIBITED: dict[str, tuple[str, ...]] = {
    "credito_aprobado": (
        r"\bcredito (?:ya )?(?:esta |queda )?aprobad[oa]\b",
        r"\bte aprobaron el (?:credito|financiamiento|prestamo)\b",
        r"\bfinanciamiento (?:ya )?(?:esta )?aprobad[oa]\b",
        r"\btienes (?:el )?credito aprobado\b",
    ),
    "mascotas_sin_confirmar": (
        r"\b(?:se aceptan|aceptamos|acepta[n]?) mascotas\b",
        r"\bmascotas? (?:son |esta[n]? )?permitid[oa]s?\b",
        r"\bpuedes tener mascotas?\b",
    ),
    "negociacion_de_precio": (
        r"\b(?:podemos|puedo|te puedo) (?:bajar|negociar|ajustar)(?:le)? (?:el )?precio\b",
        r"\bnegociamos el precio\b",
        r"\bte hago un mejor precio\b",
    ),
    "apartado_sin_accion": (
        r"\bya te la aparto\b", r"\bqueda apartada para ti\b", r"\bte la reservo\b",
        r"\bla dejo apartada\b", r"\bconsiderala apartada\b",
    ),
    "garantia_de_cierre": (
        r"\b(?:esta compra|esto) es un hecho\b", r"\bte garantizo que (?:cierra|se cierra|compras)\b",
        r"\bseguro que cierra\b", r"\bes un trato hecho\b",
    ),
}
_PROHIBITED = {kind: re.compile("|".join(patterns)) for kind, patterns in _ALWAYS_PROHIBITED.items()}

# --- claim 5: amenidad no respaldada por `amenities` de este turno ---------------------------------
# Vocabulario cerrado: los valores que de verdad usa el catálogo falso (services/catalog/app/data.py).
_AMENITY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "alberca": ("alberca", "piscina"), "gimnasio": ("gimnasio",), "jardin": ("jardin",),
    "cochera": ("cochera",), "estacionamiento": ("estacionamiento",), "terraza": ("terraza",),
    "patio": ("patio",), "salon de eventos": ("salon de eventos", "salon social"),
}
_AMENITY = {canonical: re.compile(r"\b(?:" + "|".join(words) + r")\b")
           for canonical, words in _AMENITY_SYNONYMS.items()}


def extract_amenity_claims(text: str) -> set[str]:
    """Amenidades que `text` afirma que tiene la propiedad — no las que niega o pone en duda."""
    normalized = normalize(text)
    return {canonical for canonical, pattern in _AMENITY.items() if _unhedged(pattern, normalized)}


def _walk_amenities(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == "amenities" and isinstance(inner, list):
                found.update(normalize(str(item)) for item in inner)
            else:
                _walk_amenities(inner, found)
    elif isinstance(value, list):
        for item in value:
            _walk_amenities(item, found)


def _known_amenities(tool_results: Mapping[str, Any]) -> set[str]:
    """Amenidades que de verdad devolvieron las tools de este turno, en cualquier propiedad citada."""
    found: set[str] = set()
    _walk_amenities(dict(tool_results), found)
    return found


def check(text: str, tool_results: Mapping[str, Any], lead_text: str = "") -> list[Violation]:
    """Promesas no autorizadas en `text`: frases siempre prohibidas + amenidad sin respaldo."""
    normalized = normalize(text)
    violations = [Violation("promise", kind) for kind, pattern in _PROHIBITED.items()
                 if _unhedged(pattern, normalized)]
    exempt = extract_amenity_claims(lead_text)  # repetir lo que el lead dijo no es una promesa del agente
    allowed = _known_amenities(tool_results)
    violations += [Violation("amenity", a) for a in extract_amenity_claims(text) - exempt if a not in allowed]
    return violations
