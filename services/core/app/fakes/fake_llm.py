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

# El orden importa: gana la primera categoría cuya palabra clave aparece en el mensaje.
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("solicitud_humano", (
        "asesor", "humano", "persona real", "hablar con alguien", "hablar con una persona",
        "supervisor", "gerente",
    )),
    # Lo que un agente de ventas no atiende (cuentas, contratos vigentes, quejas): lo ve una persona.
    ("fuera_de_alcance", (
        "queja", "reclamo", "reclamacion", "ya soy cliente", "mi contrato", "mi deposito",
    )),
    ("agendar_visita", ("visita", "agendar", "cita")),
    ("consulta_precio", ("precio", "cuesta", "costo", "cuanto")),
    ("busqueda_propiedad", (
        "busco", "departamento", "depa", "casa", "propiedad", "rentar", "comprar", "recamara",
    )),
    ("saludo", ("hola", "buenas", "buenos dias", "buenas tardes")),
)

REPLIES: dict[str, str] = {
    "solicitud_humano": "Claro, te comunico con un asesor de nuestro equipo.",
    "fuera_de_alcance": "Eso lo atiende directamente nuestro equipo: te comunico con un asesor.",
    "agendar_visita": "Con gusto coordinamos una visita. ¿Qué día te acomoda?",
    "consulta_precio": "Con gusto reviso el precio de esa propiedad y te lo comparto.",
    "busqueda_propiedad": "Cuéntame más: ¿en qué zona buscas y cuántas recámaras necesitas?",
    "saludo": "¡Hola! Soy el asistente de la inmobiliaria. ¿En qué te puedo ayudar?",
    "otro": "Gracias por tu mensaje. ¿Me cuentas qué tipo de propiedad estás buscando?",
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
    ("departamento", ("depa", "departamento", "penthouse")),
    ("casa", ("casa",)),
    ("terreno", ("terreno", "lote")),
    ("local", ("local",)),
    ("estudio", ("estudio",)),
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


class FakeLLM:
    def classify_intent(self, text: str) -> str:
        normalized = _normalize(text)
        if not normalized.strip():
            return "saludo"  # p. ej. un deep-link sin texto: el lead acaba de llegar
        for intent, keywords in _KEYWORDS:
            if any(keyword in normalized for keyword in keywords):
                return intent
        return "otro"

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
