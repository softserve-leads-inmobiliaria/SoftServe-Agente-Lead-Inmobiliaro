"""Guardrail de entrada (A-8): revisa el mensaje del lead antes de que llegue a la FSM o al LLM.

Función pura y determinista (reglas, no LLM): sin red, sin base de datos. Los 5 detectores y la
acción de cada uno están en `docs/DESIGN/guardrails.md` §1. El veredicto solo describe; quien lo
aplica es `app/turn.py`.

Regla general: el guardrail no detiene la conversación en seco salvo `/stop`; degrada a una
respuesta segura. Nunca se copia el contenido sensible a los resultados: `clean_text` ya lleva los
datos omitidos y la auditoría solo guarda tipos, no valores.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

REDACTED = "[dato omitido]"
# Lo que se guarda y se le da al LLM cuando de un mensaje no queda nada utilizable (solo PII o categoría protegida).
OMITTED_MESSAGE = "[mensaje omitido]"

OPT_OUT_REPLY = "Listo, registramos tu baja: no te enviaremos más mensajes."
WITHDRAWN_REPLY = "Tu baja ya está registrada y no te escribiremos más."
PII_REMINDER = ("Por tu seguridad no compartas identificaciones ni datos de tarjeta por aquí: "
                "solo necesito la zona, el tipo de propiedad, tu presupuesto aproximado y cuándo quieres mudarte.")
OUT_OF_DOMAIN_REPLY = ("Con eso no puedo ayudarte: me enfoco en propiedades en venta o renta, precios y visitas. "
                       "Para temas legales, fiscales o de crédito formal conviene consultar a un especialista.")
HANDOFF_OFFER = " Si prefieres, te comunico con un asesor de nuestro equipo."

# Tras este número de mensajes fuera de dominio en la conversación, la respuesta ofrece un asesor.
OUT_OF_DOMAIN_OFFER_AFTER = 2


def normalize(text: str) -> str:
    """Minúsculas y sin acentos: las reglas se escriben una sola vez."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _any(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(patterns))


# --- /stop --------------------------------------------------------------------------------------
_STOP_COMMAND = re.compile(r"(?:^|\s)/stop\b|^\s*(?:stop|alto|unsubscribe|cancelar)\s*[.!]*\s*$")
_STOP_PHRASES = _any((
    r"\bdarme de baja\b", r"\bdame de baja\b", r"\bdar de baja\b", r"\bquiero la baja\b",
    r"\bno me (?:escribas|escriban|contactes|contacten|mandes|manden)\b",
    r"(?<!no )\bdej(?:a|an|e|en) de (?:escribirme|contactarme|mandarme)\b",
    r"\bno quiero (?:recibir )?mas mensajes\b", r"\bcancelar (?:mi )?suscripcion\b",
))

# --- anti-jailbreak -----------------------------------------------------------------------------
_JAILBREAK = _any((
    r"\b(?:ignora|olvida|omite|descarta)\b.{0,30}\b(?:instrucciones|reglas|indicaciones|prompt)\b",
    r"\b(?:system|developer)\s*prompt\b", r"\bprompt del sistema\b",
    r"\b(?:muestra|dime|repite|revela|imprime)(?:me)?\b.{0,30}\b(?:tus|sus) (?:instrucciones|reglas|prompt)\b",
    r"\bactua como\b", r"\bfinge (?:que )?(?:eres|ser)\b", r"\bahora eres\b", r"\beres ahora\b",
    r"\bmodo (?:desarrollador|developer|dios)\b", r"\bjailbreak\b", r"\bdan mode\b",
))

# --- fuera de dominio ---------------------------------------------------------------------------
_REAL_ESTATE = _any((
    r"\bcasa\b", r"\bdepa\w*\b", r"\bdepartamento\b", r"\bpropiedad\w*\b", r"\brent\w+\b", r"\bcomprar\b",
    r"\bvender\b", r"\bvisita\b", r"\bprecio\b", r"\brecamara\w*\b", r"\bterreno\b", r"\binmueble\b",
    r"\bzona\b", r"\bmudarme\b", r"\bmudar\b", r"\basesor\b",
))
# Dictamen formal (legal, fiscal, crediticio): se declina siempre, aunque hable de una propiedad.
_FORMAL_ADVICE = _any((
    r"\babogado\b", r"\bdemanda\w*\b", r"\bjuridic\w+\b", r"\basesoria legal\b", r"\bes legal\b",
    r"\bimpuestos?\b", r"\bdeclaracion (?:fiscal|de impuestos)\b", r"\bfiscal\b", r"\bburo de credito\b",
    r"\bdictamen\b", r"\bme (?:aprueban|autorizan) (?:el|un) (?:credito|prestamo)\b",
))
_OFF_TOPIC = _any((
    r"\bclima\b", r"\bfutbol\b", r"\bpartido\b", r"\breceta\b", r"\bpolitica\b", r"\bchiste\b",
    r"\bpoema\b", r"\bcancion\b", r"\bpelicula\b", r"\bbitcoin\b", r"\bhoroscopo\b", r"\btarea\b",
    r"\bprogramar\b", r"\bcodigo (?:en|de) (?:python|java)\b",
))

# --- categoría protegida (fair housing) ---------------------------------------------------------
# Origen dicho como lo dice un lead: gentilicios y frases de procedencia (#116). Lista cerrada de
# países para no marcar "soy de El Poblado". Los gentilicios que también nombran otra cosa (pesos
# mexicanos, colonia Americana, cocina americana, hablar español) solo cuentan detrás de "soy/somos":
# con "es/son" hablan de la propiedad tanto como de una persona.
_COPULA = r"(?:soy|somos|eres|es|son)\s+(?:de nacionalidad\s+)?"
_SELF_COPULA = r"(?:soy|somos)\s+(?:de nacionalidad\s+)?"
_DEMONYMS = (
    r"(?:colombian[oa]s?|venezolan[oa]s?|argentin[oa]s?|chilen[oa]s?|peruan[oa]s?|ecuatorian[oa]s?"
    r"|bolivian[oa]s?|paraguay[oa]s?|uruguay[oa]s?|brasilen[oa]s?|brasiler[oa]s?|cuban[oa]s?"
    r"|puertorriquen[oa]s?|boricuas?|guatemaltec[oa]s?|honduren[oa]s?|salvadoren[oa]s?|panamen[oa]s?"
    r"|haitian[oa]s?|gring[oa]s?|nicaraguenses?|costarricenses?|estadounidenses?|canadienses?)"
)
_AMBIGUOUS_DEMONYMS = r"(?:mexican[oa]s?|(?:norte)?american[oa]s?|espanol(?:a|es|as)?|dominican[oa]s?)"
_COUNTRIES = (
    r"(?:mexico|colombia|venezuela|argentina|chile|peru|ecuador|bolivia|paraguay|uruguay|brasil|cuba"
    r"|(?:la )?republica dominicana|puerto rico|guatemala|honduras|el salvador|nicaragua|costa rica|panama"
    r"|haiti|(?:los )?estados unidos|ee\.? ?uu\.?|usa|canada|espana)"
)
_ORIGIN_PHRASE = (r"(?:soy|somos|es|son|vengo|venimos|vine|vinimos|llegue|llegamos)\s+(?:de|desde)"
                  r"|naci|nacimos|originari[oa]s? de|oriund[oa]s? de")
_PROTECTED = _any((
    rf"\b(?:{_COPULA})?{_DEMONYMS}\b",
    rf"\b{_SELF_COPULA}{_AMBIGUOUS_DEMONYMS}\b",
    rf"\b(?:{_ORIGIN_PHRASE})(?:\s+en)?\s+{_COUNTRIES}(?![\w])",
    r"\b(?:nacionalidad|extranjer[oa]s?|migrantes?|inmigrantes?|indigenas?|raza|origen etnico)\b",
    r"\b(?:religion|catolic[oa]s?|cristian[oa]s?|musulman\w*|judi[oa]s?|ateo|atea|evangelic[oa]s?|budistas?)\b",
    r"\b(?:soy (?:mujer|hombre|gay|lesbiana|trans)|genero|orientacion sexual|embarazad[oa])\b",
    r"\b(?:casad[oa]s?|solter[oa]s?|divorciad[oa]s?|viud[oa]s?|madre soltera|padre soltero|estado civil)\b",
    r"\b(?:tengo hij[oa]s|mis hij[oa]s)\b",
    r"\bmi (?:espos[oa]|marido|mujer|novi[oa])\b",
))
# Menciones protegidas que además dicen que la decisión se comparte ("lo hablo con mis hijos", "con mi
# esposa"): se generalizan en vez de borrarse. Borrarlas dejaba "lo hablo con" y el lead perdía los puntos
# de Authority (A-11, DT-38). "mi familia" y "mi pareja" conservan el rol sin revelar la situación
# familiar ni el estado civil (#119, DT-40).
_GENERALIZED: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"mis hij[oa]s"), "mi familia"),
    (re.compile(r"mi (?:espos[oa]|marido|mujer|novi[oa])"), "mi pareja"),
)

# Lo que puede quedar de un mensaje al quitar una mención protegida ("Soy ___") sin aportar contenido.
_FILLER_WORDS = frozenset({"soy", "estoy", "somos", "estamos", "tengo", "tenemos", "y", "e", "es", "mi", "que", "con",
                           "pero", "de", "un", "una", "el", "la"})

# --- PII no solicitada --------------------------------------------------------------------------
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_CURP = re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b", re.IGNORECASE)
_RFC = re.compile(r"\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b", re.IGNORECASE)
_ID_DOCUMENT = re.compile(
    r"\b(?:ine|ife|pasaporte|licencia(?: de conducir)?|cedula profesional|nss)\b[^\w\n]{0,10}"
    r"(?:(?:es|n[uú]mero|no\.?|#)[^\w\n]{0,5})?[A-Z0-9]{6,}\b", re.IGNORECASE)


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value * 2 > 9 else value * 2
        total += value
    return total % 10 == 0


@dataclass(frozen=True)
class InputVerdict:
    """Lo que el guardrail encontró en un mensaje. Sin valores sensibles: solo tipos y banderas."""

    clean_text: str
    # Lo único que ven el LLM y el historial: sin PII y sin los fragmentos de categoría protegida
    # (conserva mayúsculas y acentos). Así mencionarla no cambia el score ni orienta las respuestas.
    signal_text: str = ""
    opt_out: bool = False
    jailbreak: bool = False
    pii: tuple[str, ...] = ()
    out_of_domain: bool = False
    protected_category: bool = False

    @property
    def flagged(self) -> bool:
        return self.opt_out or self.jailbreak or bool(self.pii) or self.out_of_domain or self.protected_category

    def audit_kinds(self) -> list[tuple[str, dict[str, object]]]:
        """(acción, detalle) de cada detección para `AuditLog`; el detalle nunca lleva el contenido."""
        kinds: list[tuple[str, dict[str, object]]] = []
        if self.opt_out:
            kinds.append(("input.opt_out", {}))
        if self.jailbreak:
            kinds.append(("input.jailbreak", {}))
        if self.pii:
            kinds.append(("input.pii", {"types": list(self.pii)}))
        if self.out_of_domain:
            kinds.append(("input.out_of_domain", {}))
        if self.protected_category:
            kinds.append(("input.protected_category", {}))
        return kinds


def _redact_pii(text: str) -> tuple[str, tuple[str, ...]]:
    found: list[str] = []

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found.append("tarjeta")
            return REDACTED
        return match.group(0)

    def tagged(kind: str):
        def replace(_: re.Match[str]) -> str:
            found.append(kind)
            return REDACTED
        return replace

    text = _CARD.sub(card, text)
    text = _CURP.sub(tagged("curp"), text)
    text = _RFC.sub(tagged("rfc"), text)
    text = _ID_DOCUMENT.sub(tagged("identificacion"), text)
    return text, tuple(dict.fromkeys(found))


def strip_protected(text: str) -> str:
    """Quita del texto los fragmentos de categoría protegida sin perder mayúsculas ni acentos.

    Las reglas trabajan sobre el texto normalizado (minúsculas, sin acentos); aquí se recuerda a qué
    posición del original corresponde cada carácter normalizado para poder recortar el original.
    Las menciones de `_GENERALIZED` no se borran: se reemplazan por su forma neutra.
    """
    normalized: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        for base in unicodedata.normalize("NFD", char.lower()):
            if unicodedata.category(base) != "Mn":
                normalized.append(base)
                origin.append(index)
    keep = [True] * len(text)
    replacement: dict[int, str] = {}
    for match in _PROTECTED.finditer("".join(normalized)):
        for position in range(match.start(), match.end()):
            keep[origin[position]] = False
        neutral = next((word for role, word in _GENERALIZED if role.fullmatch(match.group())), None)
        if neutral:
            replacement[origin[match.start()]] = neutral
    if all(keep):
        return text
    kept_text = "".join(replacement.get(index, "") + (char if kept else "")
                        for index, (char, kept) in enumerate(zip(text, keep, strict=True)))
    stripped = re.sub(r"\s{2,}", " ", kept_text).strip()
    words = re.findall(r"\w+", stripped.lower())
    # Si al quitar la mención solo queda relleno ("Soy", "Tengo y"), no queda nada utilizable.
    return "" if all(word in _FILLER_WORDS for word in words) else stripped


def mentions_protected(text: str) -> bool:
    """True si el texto menciona una categoría protegida (`guardrails.md` §1). Lo usa también la salida (A-16)."""
    return bool(_PROTECTED.search(normalize(text)))


def is_opt_out(text: str) -> bool:
    normalized = normalize(text)
    return bool(_STOP_COMMAND.search(normalized) or _STOP_PHRASES.search(normalized))


def inspect_input(text: str) -> InputVerdict:
    """Analiza el mensaje. `/stop` manda: si hay baja, no se analiza nada más."""
    if is_opt_out(text):
        return InputVerdict(clean_text=text, signal_text="", opt_out=True)
    clean, pii = _redact_pii(text)
    normalized = normalize(clean)
    domain = bool(_REAL_ESTATE.search(normalized))
    return InputVerdict(
        clean_text=clean,
        signal_text=strip_protected(clean),
        jailbreak=bool(_JAILBREAK.search(normalized)),
        pii=pii,
        out_of_domain=bool(_FORMAL_ADVICE.search(normalized)) or (not domain and bool(_OFF_TOPIC.search(normalized))),
        protected_category=bool(_PROTECTED.search(normalized)),
    )
