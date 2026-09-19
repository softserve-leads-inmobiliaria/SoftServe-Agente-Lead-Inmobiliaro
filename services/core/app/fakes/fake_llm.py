"""LLM falso (CT-7): clasifica la intención por palabras clave y responde con texto fijo.

Sirve para construir y probar el resto del sistema sin credenciales del proveedor real. No hace
ninguna llamada de red y es determinista. Las respuestas no afirman ningún dato (precios,
disponibilidad, promesas): eso es lo que los guardrails reales tendrán que verificar más adelante.
"""
from __future__ import annotations

import unicodedata

# Categorías de intención. `otro` es el cajón de sastre: NO debe disparar ningún efecto de sistema
# real (p. ej. un traspaso a humano) sin revisarlo antes — ver criterio de A-2/A-13.
INTENTS = (
    "solicitud_humano",
    "agendar_visita",
    "consulta_precio",
    "busqueda_propiedad",
    "saludo",
    "otro",
)

# El orden importa: gana la primera categoría cuya palabra clave aparece en el mensaje.
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("solicitud_humano", ("asesor", "humano", "persona real", "hablar con alguien")),
    ("agendar_visita", ("visita", "agendar", "cita")),
    ("consulta_precio", ("precio", "cuesta", "costo", "cuanto")),
    ("busqueda_propiedad", (
        "busco", "departamento", "depa", "casa", "propiedad", "rentar", "comprar", "recamara",
    )),
    ("saludo", ("hola", "buenas", "buenos dias", "buenas tardes")),
)

REPLIES: dict[str, str] = {
    "solicitud_humano": "Claro, te comunico con un asesor de nuestro equipo.",
    "agendar_visita": "Con gusto coordinamos una visita. ¿Qué día te acomoda?",
    "consulta_precio": "Con gusto reviso el precio de esa propiedad y te lo comparto.",
    "busqueda_propiedad": "Cuéntame más: ¿en qué zona buscas y cuántas recámaras necesitas?",
    "saludo": "¡Hola! Soy el asistente de la inmobiliaria. ¿En qué te puedo ayudar?",
    "otro": "Gracias por tu mensaje. ¿Me cuentas qué tipo de propiedad estás buscando?",
}


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
