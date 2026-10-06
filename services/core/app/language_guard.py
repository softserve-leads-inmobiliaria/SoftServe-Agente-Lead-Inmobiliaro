"""Filtro de idioma (#202, DT-55): la experiencia es en inglés, así que un mensaje en español se contesta
con un aviso fijo y no llega al LLM, a la FSM ni al score.

Determinista y local, como el resto del guardrail de entrada: cuenta palabras que solo son de un idioma.
Un mensaje es español si tiene más palabras de español que de inglés. Las ambiguas ("a", "no", "me",
"solo", "local") no cuentan para ninguno, y ante la duda (empate, o nada que contar) el mensaje pasa: es
peor rechazar a quien escribe en inglés que dejar pasar un mensaje mezclado.

Los nombres propios no son español aunque lo parezcan: las ciudades y zonas del inventario y las vías con
número ("Calle 10", "Carrera 37 # 8A-24") se quitan antes de contar, y un artículo o preposición
("de", "la", "el") solo cuenta si la palabra que sigue va en minúscula ("near Parque de la 93", "Santa
Fe de Antioquia" no cuentan; "busco casa en la zona" sí).
"""
from __future__ import annotations

import re
import unicodedata

NOT_ENGLISH_REPLY = ("Sorry, I can only chat in English. "
                     "Could you please send your message again in English?")

# Ciudades y zonas del inventario y de alrededor (DT-55): se escriben con o sin tilde y en cualquier caja.
_PLACES = re.compile(
    r"\b(?:el poblado|laureles|envigado|sabaneta|itagui|belen|rionegro|medellin|antioquia"
    r"|usaquen|chapinero|cedritos|teusaquillo|salitre|suba|chia|bogota"
    r"|parque lleras|zona rosa|zona t|parque (?:de la )?93|colombia)\b")
# Una vía con número es una dirección, no una frase: "Calle 10", "Cra 43A # 1-50", "Avenida 80".
_STREET = re.compile(
    r"\b(?:calle|cl|carrera|cra|kra|kr|avenida|av|diagonal|dg|transversal|tv|circular|autopista)\.?\s*"
    r"\d+\w*(?:\s*(?:#|no\.?|n°)\s*\d+\w*(?:\s*-\s*\d+\w*)?)?", re.IGNORECASE)

# Solo cuentan si la palabra siguiente va en minúscula: también arman nombres propios ("La Candelaria").
_WEAK_SPANISH = frozenset({"el", "la", "los", "las", "de", "del", "y", "en", "al"})
_SPANISH = _WEAK_SPANISH | frozenset({
    "que", "por", "para", "con", "sin", "una", "uno", "unos", "unas", "pero", "como", "cuando", "donde",
    "cual", "cuales", "cuanto", "cuanta", "cuantos", "cuantas", "quien", "porque", "tambien", "muy", "mas",
    "menos", "esta", "este", "estos", "estas", "ese", "esa", "eso", "esto", "aqui", "alli", "hay", "tiene",
    "tienen", "tengo", "tenemos", "quiero", "queremos", "quisiera", "busco", "buscamos", "buscando",
    "necesito", "necesitamos", "puedo", "puede", "pueden", "podria", "podemos", "gracias", "hola", "buenas",
    "buenos", "dias", "tardes", "noches", "si", "vale", "claro", "listo", "perfecto", "bueno", "mi", "mis",
    "tu", "tus", "su", "sus", "nuestro", "nuestra", "yo", "usted", "ustedes", "nosotros", "ella", "ellos",
    "es", "son", "soy", "estoy", "estamos", "ser", "estar", "fue", "seria", "cuesta", "precio", "casa",
    "apartamento", "departamento", "apto", "depa", "estudio", "habitaciones", "habitacion", "cuartos",
    "recamaras", "banos", "alquiler", "arriendo", "arrendar", "comprar", "compra", "venta", "vender",
    "rentar", "visita", "visitar", "agendar", "cita", "manana", "hoy", "semana", "mes", "meses", "ano",
    "anos", "millones", "cerca", "lejos", "lo", "le", "les", "se", "nos", "te", "ya", "todavia", "ahora",
    "despues", "entre", "sobre", "hasta", "desde", "cada", "otro", "otra", "todo", "toda", "todos", "nada",
    "algo", "alguien", "asesor", "presupuesto", "quiere", "gustaria", "interesa", "disponible",
})
_ENGLISH = frozenset({
    "the", "an", "i", "i'm", "im", "i'd", "i'll", "i've", "my", "mine", "you", "your", "you're", "we",
    "we're", "our", "they", "it", "it's", "is", "are", "am", "was", "were", "be", "been", "do", "does",
    "did", "don't", "have", "has", "had", "can", "can't", "could", "would", "should", "will", "want",
    "need", "looking", "look", "find", "interested", "in", "on", "at", "of", "for", "to", "from", "with",
    "near", "about", "and", "or", "but", "if", "what", "what's", "where", "when", "which", "how", "much",
    "many", "any", "some", "this", "that", "these", "those", "there", "here", "please", "thanks", "thank",
    "hi", "hello", "hey", "yes", "yeah", "sure", "okay", "house", "apartment", "apartments", "flat",
    "condo", "bedroom", "bedrooms", "bathroom", "bathrooms", "rent", "buy", "price", "budget", "viewing",
    "visit", "schedule", "book", "tomorrow", "today", "week", "month", "months", "morning", "afternoon",
    "evening", "not", "just", "also", "more", "less", "than", "available", "sale", "agent", "talk",
    "speak", "someone", "person", "human", "cost", "costs", "good", "great", "works", "one", "two",
    "three", "first", "second", "third", "nope", "who", "why", "let", "know",
})
# "¿" y "¡" solo los usa el español.
_SPANISH_PUNCTUATION = re.compile("[¿¡]")
_WORD = re.compile(r"\w+(?:'\w+)?")


def _fold(text: str) -> str:
    """Sin tildes y con el apóstrofo recto, conservando la caja (la usa la regla de los nombres propios)."""
    decomposed = unicodedata.normalize("NFD", text.replace("’", "'"))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _without_names(text: str) -> str:
    folded = _fold(text)
    # Se reemplazan sobre el texto en minúsculas, pero se corta el original: misma longitud tras _fold.
    lowered = folded.lower()
    spans = [m.span() for pattern in (_PLACES, _STREET) for m in pattern.finditer(lowered)]
    chars = list(folded)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def counts(text: str) -> tuple[int, int]:
    """(palabras de español, palabras de inglés) del mensaje, sin los nombres de lugares ni las vías."""
    words = _WORD.findall(_without_names(text))
    spanish = 1 if _SPANISH_PUNCTUATION.search(text) else 0
    english = 0
    for i, word in enumerate(words):
        lower = word.lower()
        if lower in _WEAK_SPANISH:
            following = words[i + 1] if i + 1 < len(words) else ""
            spanish += bool(following[:1].islower())
        elif lower in _SPANISH:
            spanish += 1
        elif lower in _ENGLISH:
            english += 1
    return spanish, english


def is_spanish(text: str) -> bool:
    """True si el mensaje está escrito en español (ver el docstring del módulo)."""
    spanish, english = counts(text)
    return spanish > english
