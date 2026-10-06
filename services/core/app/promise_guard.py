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
# En inglés (#202, DT-55) lo mismo: "not", "if/whether", "let me check"... El LLM escribe en inglés; las
# palabras en español se quedan porque no cuestan nada.
_HEDGE_BEFORE = re.compile(
    r"\b(?:no|sin|nunca|jamas|si|puede que|tal vez|quiza|confirmar|preguntar|consultar|verificar"
    r"|not|never|without|don['\u2019]?t|doesn['\u2019]?t|isn['\u2019]?t|aren['\u2019]?t|can['\u2019]?t|cannot|if|whether"
    r"|may|might|maybe|perhaps|confirm|check|ask|verify)\b"
    r"(?:\s+[\w'\u2019]+){0,3}\s*$"
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
        # "pre-approved" no: es lo que dice el lead de su hipoteca, y repetirlo no promete nada.
        r"\b(?:loan|mortgage|credit|financing) (?:is |has been |was |got )?(?:already )?(?<!pre-)(?<!pre)approved\b",
        r"\byou(?:['\u2019]re| are) (?:already )?(?<!pre-)(?<!pre)approved\b",
        r"\bthey approved your (?:loan|mortgage|credit|financing)\b",
        r"\byou have (?:the |your )?(?:loan|mortgage|credit) approved\b",
    ),
    "mascotas_sin_confirmar": (
        r"\b(?:se aceptan|aceptamos|acepta[n]?) mascotas\b",
        r"\bmascotas? (?:son |esta[n]? )?permitid[oa]s?\b",
        r"\bpuedes tener mascotas?\b",
        r"\bpets? (?:are |is )?(?:allowed|welcome|accepted|permitted)\b",
        r"\b(?:we|they|it) (?:accept|allows?|accepts) pets\b",
        r"\byou can (?:have|bring|keep) (?:a |your )?pets?\b",
        r"\bpet[- ]friendly\b",
    ),
    "negociacion_de_precio": (
        r"\b(?:podemos|puedo|te puedo) (?:bajar|negociar|ajustar)(?:le)? (?:el )?precio\b",
        r"\bnegociamos el precio\b",
        r"\bte hago un mejor precio\b",
        r"\b(?:we|i) can (?:lower|negotiate|reduce|drop|adjust) the price\b",
        r"\b(?:i['\u2019]ll|i will|let me) (?:give|get) you a (?:better|lower) price\b",
        r"\b(?:the )?price is negotiable\b", r"\bwe can negotiate\b",
    ),
    "apartado_sin_accion": (
        r"\bya te la aparto\b", r"\bqueda apartada para ti\b", r"\bte la reservo\b",
        r"\bla dejo apartada\b", r"\bconsiderala apartada\b",
        r"\bi(?:['\u2019]ll| will) (?:reserve|hold|save) it for you\b", r"\bit(?:['\u2019]s| is) reserved for you\b",
        r"\bconsider it reserved\b", r"\bi(?:['\u2019]ve| have) reserved it for you\b",
    ),
    "garantia_de_cierre": (
        r"\b(?:esta compra|esto) es un hecho\b", r"\bte garantizo que (?:cierra|se cierra|compras)\b",
        r"\bseguro que cierra\b", r"\bes un trato hecho\b",
        r"\bit(?:['\u2019]s| is) a done deal\b", r"\bit(?:['\u2019]s| is) a sure thing\b",
        r"\bi guarantee (?:that )?(?:it(?:['\u2019]ll| will)? close|you(?:['\u2019]ll| will) (?:get|buy))\w*\b",
        r"\b(?:the|this|your) (?:sale|purchase|deal) is guaranteed\b",
    ),
}
_PROHIBITED = {kind: re.compile("|".join(patterns)) for kind, patterns in _ALWAYS_PROHIBITED.items()}

# --- claim 5: amenidad no respaldada por `amenities` de este turno ---------------------------------
# Vocabulario cerrado: la clave es el valor que usa el catálogo de demo (services/catalog/app/data.py), en
# inglés desde #202 (DT-55). Los sinónimos en español siguen: un texto que los use se reconoce igual.
_AMENITY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "pool": ("pool", "swimming pool", "alberca", "piscina"),
    "gym": ("gym", "gymnasium", "fitness center", "gimnasio"),
    "garden": ("garden", "jardin"),
    "garage": ("garage", "cochera"),
    "parking": ("parking", "parking lot", "parking space", "estacionamiento"),
    "terrace": ("terrace", "terraza"),
    "patio": ("patio",),
    "event room": ("event room", "party room", "event hall", "salon de eventos", "salon social"),
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
                for item in inner:
                    # El valor tal cual y su forma canónica: un catálogo que diga "alberca" respalda "pool".
                    item_text = normalize(str(item))
                    found.add(item_text)
                    found.update(canonical for canonical, pattern in _AMENITY.items() if pattern.search(item_text))
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
