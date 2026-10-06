"""LLM falso (CT-7): clasifica la intención por palabras clave y responde con texto fijo.

Sirve para construir y probar el resto del sistema sin credenciales del proveedor real. No hace
ninguna llamada de red y es determinista. Las respuestas no afirman ningún dato (precios,
disponibilidad, promesas): eso es lo que los guardrails reales tendrán que verificar más adelante.
"""
from __future__ import annotations

import re
import unicodedata

# Categorías de intención. `otro` es el cajón de sastre: NO debe disparar ningún efecto de sistema
# real (p. ej. un traspaso a humano) sin revisarlo antes — ver criterio de A-2/A-13.
INTENTS = (
    "solicitud_humano",
    "fuera_de_alcance",
    "agendar_visita",
    "consulta_precio",
    "busqueda_propiedad",
    "saludo",
    "otro",
)

# El orden importa: gana la primera categoría cuya palabra clave aparece en el mensaje. En español y en
# inglés (#202): el filtro de idioma no deja llegar el español al agente, pero el fake lo sigue
# entendiendo. Las palabras clave son expresiones regulares que empiezan en un límite de palabra ("depa"
# también es "departamento", pero "hi" no es "this").
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("solicitud_humano", (
        "asesor", "humano", "persona real", "hablar con alguien", "hablar con una persona",
        "supervisor", "gerente",
        r"agent\b", "agents", "human", "real person", r"(?:talk|speak) (?:to|with) (?:someone|somebody|a person)",
        "manager",
    )),
    # Lo que un agente de ventas no atiende (cuentas, contratos vigentes, quejas): lo ve una persona.
    ("fuera_de_alcance", (
        "queja", "reclamo", "reclamacion", "ya soy cliente", "mi contrato", "mi deposito",
        "complaint", "i(?:'m| am) already (?:a )?(?:client|customer)", "my contract", "my deposit",
    )),
    ("agendar_visita", ("visita", "agendar", "cita", "visit", "viewing", "schedule", "appointment", "book a")),
    ("consulta_precio", ("precio", "cuesta", "costo", "cuanto", "price", "cost", "how much")),
    ("busqueda_propiedad", (
        "busco", "departamento", "depa", "casa", "propiedad", "rentar", "comprar", "recamara",
        "looking for", "apartment", "flat", "condo", r"house\b", "property", "rent", "buy", "bedroom",
    )),
    ("saludo", ("hola", "buenas", "buenos dias", "buenas tardes",
                r"hi\b", "hello", r"hey\b", "good (?:morning|afternoon|evening)")),
)
_PATTERNS = tuple((intent, re.compile("|".join(rf"\b(?:{k})" for k in words))) for intent, words in _KEYWORDS)

# Lo que lee el lead, en inglés (#202, DT-55).
REPLIES: dict[str, str] = {
    "solicitud_humano": "Sure, I'll put you in touch with one of our agents.",
    "fuera_de_alcance": "Our team handles that directly: I'll put you in touch with an agent.",
    "agendar_visita": "Happy to set up a visit. What day works for you?",
    "consulta_precio": "Happy to check the price of that property and share it with you.",
    "busqueda_propiedad": "Tell me more: which area are you looking in and how many bedrooms do you need?",
    "saludo": "Hi! I'm the agency's assistant. How can I help you?",
    "otro": "Thanks for your message. What kind of property are you looking for?",
}


# Señal cruda por turno (A-9): solo los tres campos que el contrato deja emitir al LLM. Un `None`
# significa "no lo dijo en este mensaje"; el número final del score nunca sale de aquí.
# `decision_maker` solo dice si el lead declaró cómo se decide, no con quién (#119, DT-40).
# En español y en inglés (#202): el gold set de fair housing corre las dos versiones por este extractor.
_DECISION_MAKER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("declarado", ("decido yo", "yo decido", "solo yo", "soy yo quien", "mi pareja", "mi familia",
                   "mis padres", "mis papas",
                   "i decide", "i make the decision", "i'm the one who decides", "my decision", "decide myself",
                   "my partner", "my family", "my parents")),
)
_URGENCY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("inmediata", ("urgente", "lo antes posible", "cuanto antes", "de inmediato", "este mes", "ya mismo",
                   "urgent", "asap", "as soon as possible", "right away", "immediately", "this month")),
    ("3_meses", ("tres meses", "3 meses", "en unos meses", "pronto",
                 "three months", "3 months", "in a few months", "soon")),
    ("explorando", ("explorando", "solo estoy viendo", "curiosidad", "mas adelante", "sin prisa",
                    "exploring", "just looking", "browsing", "curious", "later on", "no rush")),
)
_BUDGET = re.compile(
    r"presupuesto[^.,;!?\n]*|budget[^.,;!?\n]*"
    r"|\$\s?[\d.,]+(?:\s?(?:mil|millones|k|m|thousand|million))?"
    r"|\b\d[\d.,]*\s?(?:mil|millones|k|thousand|million)\b")

# Tipo de inmueble que nombra el lead (#188). "depa" es un prefijo de "departamento", así que caen en
# el mismo valor. El fake solo mapea `kind`; la zona y el presupuesto los extrae el LLM real.
_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("departamento", ("depa", "departamento", "penthouse", "apartment", "flat", "condo")),
    ("casa", ("casa", "house", "townhouse")),
    ("terreno", ("terreno", "lote", "land", "plot")),
    ("local", ("local", "commercial", "storefront", "retail space")),
    ("estudio", ("estudio", "studio")),
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


class FakeLLM:
    def classify_intent(self, text: str) -> str:
        normalized = _normalize(text)
        if not normalized.strip():
            return "saludo"  # p. ej. un deep-link sin texto: el lead acaba de llegar
        return next((intent for intent, pattern in _PATTERNS if pattern.search(normalized)), "otro")

    def generate_reply(self, intent: str) -> str:
        try:
            return REPLIES[intent]
        except KeyError:
            raise ValueError(f"intención desconocida: {intent!r} (válidas: {INTENTS})") from None

    def extract_property_kind(self, text: str) -> str | None:
        """El tipo de inmueble que nombra el mensaje (#188), o None si no dice ninguno."""
        normalized = _normalize(text)
        return next((kind for kind, words in _KINDS if any(word in normalized for word in words)), None)

    def extract_signal(self, text: str) -> dict[str, str | None]:
        """Lo que el mensaje declara sobre presupuesto, quién decide y urgencia (None = no dicho)."""
        normalized = _normalize(text)

        def first(table: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
            return next((value for value, words in table if any(w in normalized for w in words)), None)

        budget = _BUDGET.search(normalized)
        return {
            "budget_hint": budget.group(0).strip() if budget else None,
            "decision_maker": first(_DECISION_MAKER),
            "urgency": first(_URGENCY),
        }
