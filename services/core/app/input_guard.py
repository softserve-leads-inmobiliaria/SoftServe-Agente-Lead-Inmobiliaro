"""Guardrail de entrada (A-8): revisa el mensaje del lead antes de que llegue a la FSM o al LLM.

Función pura y determinista (reglas, no LLM): sin red, sin base de datos. Los 5 detectores y la
acción de cada uno están en `docs/DESIGN/guardrails.md` §1. El veredicto solo describe; quien lo
aplica es `app/turn.py`.

Regla general: el guardrail no detiene la conversación en seco salvo `/stop`; degrada a una
respuesta segura. Nunca se copia el contenido sensible a los resultados: `clean_text` ya lleva los
datos omitidos y la auditoría solo guarda tipos, no valores.

Idioma (#202, DT-55): la experiencia es en inglés, así que cada detector reconoce inglés, y también
conserva sus patrones en español. Un mensaje en español no llega al LLM (`not_english`, ver
`app/language_guard.py`), pero la baja, la PII y fair housing se aplican antes y no pueden depender de eso,
y el resto no cuesta nada mantenerlo. Las respuestas fijas son en inglés.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.language_guard import is_spanish

REDACTED = "[redacted]"
# Lo que se guarda y se le da al LLM cuando de un mensaje no queda nada utilizable (solo PII o categoría protegida).
OMITTED_MESSAGE = "[message omitted]"

OPT_OUT_REPLY = "Done, you're unsubscribed: we won't send you any more messages."
WITHDRAWN_REPLY = "You're already unsubscribed, and we won't message you again."
PII_REMINDER = ("For your safety, please don't share ID numbers or card details here: all I need is the area, "
                "the kind of property, your approximate budget and when you'd like to move.")
OUT_OF_DOMAIN_REPLY = ("I can't help with that: I focus on properties for sale or rent, prices and viewings. "
                       "For legal, tax or formal credit matters, it's best to consult a specialist.")
HANDOFF_OFFER = " If you prefer, I can connect you with one of our agents."

# Tras este número de mensajes fuera de dominio en la conversación, la respuesta ofrece un asesor.
OUT_OF_DOMAIN_OFFER_AFTER = 2


def normalize(text: str) -> str:
    """Minúsculas y sin acentos: las reglas se escriben una sola vez."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _any(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(patterns))


# --- /stop --------------------------------------------------------------------------------------
# Apóstrofo recto o tipográfico ("don't", "don’t"): los teclados del celular mandan cualquiera de los dos.
_APOS = "['\u2019]"

_STOP_COMMAND = re.compile(
    r"(?:^|\s)/stop\b|^\s*(?:stop|alto|unsubscribe|cancelar|cancel|quit|opt[- ]?out)\s*[.!]*\s*$")
_STOP_PHRASES = _any((
    r"\bdarme de baja\b", r"\bdame de baja\b", r"\bdar de baja\b", r"\bquiero la baja\b",
    r"\bno me (?:escribas|escriban|contactes|contacten|mandes|manden)\b",
    r"(?<!no )\bdej(?:a|an|e|en) de (?:escribirme|contactarme|mandarme)\b",
    r"\bno quiero (?:recibir )?mas mensajes\b", r"\bcancelar (?:mi )?suscripcion\b",
    # Inglés. Sin "call": "don't call me, text me" no es una baja.
    r"(?<!don't )(?<!do not )\b(?:stop|quit) (?:messaging|texting|writing to|contacting|sending)(?: me)?\b",
    rf"\b(?:don{_APOS}?t|do not|never) (?:message|text|contact|write to|email) me\b",
    r"\bremove me from\b", r"\bunsubscribe me\b", r"\bopt[- ]?out\b", r"\bleave me alone\b",
    rf"\bi (?:don{_APOS}?t|do not) want (?:to (?:receive|get) )?(?:any )?more messages\b",
))

# --- anti-jailbreak -----------------------------------------------------------------------------
_JAILBREAK = _any((
    r"\b(?:ignora|olvida|omite|descarta)\b.{0,30}\b(?:instrucciones|reglas|indicaciones|prompt)\b",
    r"\b(?:system|developer)\s*prompt\b", r"\bprompt del sistema\b",
    r"\b(?:muestra|dime|repite|revela|imprime)(?:me)?\b.{0,30}\b(?:tus|sus) (?:instrucciones|reglas|prompt)\b",
    r"\bactua como\b", r"\bfinge (?:que )?(?:eres|ser)\b", r"\bahora eres\b", r"\beres ahora\b",
    r"\bmodo (?:desarrollador|developer|dios)\b", r"\bjailbreak\b", r"\bdan mode\b",
    r"\b(?:ignore|forget|disregard|override|bypass)\b.{0,30}\b(?:instructions|rules|guidelines|prompt|directives)\b",
    # Como "tus/sus" en español: "show me the rules for renting" es una pregunta legítima.
    r"\b(?:show|tell|repeat|reveal|print|give)(?: me)?\b.{0,30}\byour (?:instructions|rules|prompt|guidelines|system message)\b",
    # "act as" a secas no: "my dad will act as guarantor" es una conversación de renta.
    r"\b(?:act|behave) as (?:an? )?(?:ai|assistant|bot|chatbot|gpt|different|new|unfiltered|unrestricted|evil|hacker|developer|dan)\b",
    rf"\bpretend (?:to be|you{_APOS}?re|you are)\b", rf"\byou{_APOS}?re now\b", r"\byou are now\b",
    r"\bfrom now on,? you\b", r"\b(?:developer|god) mode\b", r"\bdo anything now\b", r"\broleplay as\b",
))

# --- fuera de dominio ---------------------------------------------------------------------------
_REAL_ESTATE = _any((
    r"\bcasa\b", r"\bdepa\w*\b", r"\bdepartamento\b", r"\bpropiedad\w*\b", r"\brent\w+\b", r"\bcomprar\b",
    r"\bvender\b", r"\bvisita\b", r"\bprecio\b", r"\brecamara\w*\b", r"\bterreno\b", r"\binmueble\b",
    r"\bzona\b", r"\bmudarme\b", r"\bmudar\b", r"\basesor\b",
    r"\bhouses?\b", r"\bhomes?\b", r"\bapartments?\b", r"\bflats?\b", r"\bcondos?\b", r"\brent\w*\b",
    r"\bbuy\w*\b", r"\bsell\w*\b", r"\bvisit\w*\b", r"\bviewing\b", r"\bprices?\b", r"\bbedrooms?\b",
    r"\blots?\b", r"\bland\b", r"\breal estate\b", r"\bneighborhoods?\b", r"\bmov(?:e|ing)\b", r"\bagents?\b",
    r"\bstudios?\b", r"\blistings?\b", r"\bmortgage\b",
))
# Dictamen formal (legal, fiscal, crediticio): se declina siempre, aunque hable de una propiedad.
_FORMAL_ADVICE = _any((
    r"\babogado\b", r"\bdemanda\w*\b", r"\bjuridic\w+\b", r"\basesoria legal\b", r"\bes legal\b",
    r"\bimpuestos?\b", r"\bdeclaracion (?:fiscal|de impuestos)\b", r"\bfiscal\b", r"\bburo de credito\b",
    r"\bdictamen\b", r"\bme (?:aprueban|autorizan) (?:el|un) (?:credito|prestamo)\b",
    r"\blawyer\b", r"\battorney\b", r"\blawsuit\b", r"\bsue\b", r"\blegal advice\b",
    r"\bis (?:it|this|that) legal\b", r"\btax(?:es)?\b", r"\btax return\b", r"\bcredit (?:bureau|report|score)\b",
    r"\blegal opinion\b", r"\b(?:approve|pre-?approve) (?:my|the|a) (?:loan|mortgage|credit)\b",
    r"\bwill (?:i|you|they) (?:get|approve) (?:my |the |a )?(?:loan|mortgage|credit)\b",
))
_OFF_TOPIC = _any((
    r"\bclima\b", r"\bfutbol\b", r"\bpartido\b", r"\breceta\b", r"\bpolitica\b", r"\bchiste\b",
    r"\bpoema\b", r"\bcancion\b", r"\bpelicula\b", r"\bbitcoin\b", r"\bhoroscopo\b", r"\btarea\b",
    r"\bprogramar\b", r"\bcodigo (?:en|de) (?:python|java)\b",
    r"\bweather\b", r"\bsoccer\b", r"\bfootball\b", r"\brecipe\b", r"\bpolitics\b", r"\bjoke\b",
    r"\bpoem\b", r"\bsong\b", r"\bmovie\b", r"\bcrypto\w*\b", r"\bhoroscope\b", r"\bhomework\b",
    r"\bprogramming\b", r"\b(?:write|code) (?:\w+ )?(?:in )?(?:python|java)\b",
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
# Raíz del gentilicio sin terminación (#200): "soy colombian", "soy venezolan", "somos argentin" son
# erratas comunes en un chat, y detrás de "soy/somos" no nombran otra cosa.
_DEMONYM_STEMS = (
    r"(?:colombian|venezolan|argentin|chilen|peruan|ecuatorian|bolivian|paraguay|uruguay|brasilen|brasiler"
    r"|cuban|puertorriquen|guatemaltec|honduren|salvadoren|panamen|haitian|nicaraguens|costarricens"
    r"|mexican|american|dominican)"
)
# Inglés (#202). Mismo criterio que en español: los gentilicios que también nombran otra cosa
# ("Colombian pesos", "Mexican food", "American kitchen", "I speak Spanish") solo cuentan detrás de
# "I'm/we're"; los demás, en cualquier parte.
_EN_SELF = rf"(?:i{_APOS}?m|i am|we{_APOS}?re|we are)\s+(?:an?\s+)?"
_EN_COPULA = (rf"(?:i{_APOS}?m|i am|we{_APOS}?re|we are|he{_APOS}?s|he is|she{_APOS}?s|she is|they{_APOS}?re"
              r"|they are|is|are)\s+(?:an?\s+)?")
_EN_DEMONYMS = (
    r"(?:venezuelans?|argentin(?:e|es|ians?)|chileans?|peruvians?|ecuadorians?|bolivians?|paraguayans?"
    r"|uruguayans?|brazilians?|cubans?|puerto ricans?|guatemalans?|hondurans?|salvador(?:a|e)ans?"
    r"|panamanians?|haitians?|nicaraguans?|costa ricans?|canadians?)"
)
_EN_AMBIGUOUS_DEMONYMS = (r"(?:colombians?|mexicans?|americans?|spanish|spaniards?|dominicans?|english|british"
                          r"|latin[oa]s?|latinx|hispanics?)")
_EN_COUNTRIES = (
    r"(?:mexico|colombia|venezuela|argentina|chile|peru|ecuador|bolivia|paraguay|uruguay|brazil|cuba"
    r"|(?:the )?dominican republic|puerto rico|guatemala|honduras|el salvador|nicaragua|costa rica|panama"
    r"|haiti|(?:the )?(?:united states|us|u\.s\.?|usa)|america|canada|spain|england|(?:the )?uk)"
)
_EN_ORIGIN_PHRASE = (rf"(?:i{_APOS}?m|i am|we{_APOS}?re|we are)(?: originally)? from|(?:i|we) (?:come|came) from"
                     r"|(?:i was|we were) born in|born in|originally from")
_PROTECTED = _any((
    rf"\b(?:{_COPULA})?{_DEMONYMS}\b",
    rf"\b{_SELF_COPULA}{_AMBIGUOUS_DEMONYMS}\b",
    rf"\b{_SELF_COPULA}{_DEMONYM_STEMS}\w*",
    rf"\b(?:{_ORIGIN_PHRASE})(?:\s+en)?\s+{_COUNTRIES}(?![\w])",
    rf"\b(?:{_EN_COPULA})?{_EN_DEMONYMS}\b",
    rf"\b{_EN_SELF}(?:{_EN_AMBIGUOUS_DEMONYMS}|{_DEMONYM_STEMS}\w*)\b",
    rf"\b(?:{_EN_ORIGIN_PHRASE})\s+{_EN_COUNTRIES}(?![\w])",
    r"\b(?:nationality|citizenship|foreigners?|immigrants?|migrants?|expats?|indigenous|ethnicity"
    r"|ethnic origin|race)\b",
    # En singular solo detrás de "I'm/we're": "Christian" también es un nombre y "Catholic school" un lugar.
    r"\b(?:religion|religious|islam|jewish|catholics|christians|muslims|jews|atheists|evangelicals|buddhists"
    r"|hindus|mormons)\b",
    rf"\b{_EN_SELF}(?:catholic|christian|muslim|jew|atheist|evangelical|buddhist|hindu|mormon)\b",
    rf"\b(?:i{_APOS}?m (?:an? )?(?:woman|man|gay|lesbian|trans|transgender|bisexual|non-?binary)|gender"
    r"|sexual orientation|pregnant|lgbt\w*)\b",
    # "We're a gay couple" no pasaba por "I'm" (#247).
    rf"\b(?:(?:we{_APOS}?re|we are) (?:an? )?(?:gay|lesbian|queer)|(?:gay|lesbian|queer|same[- ]sex) "
    r"(?:couple|partners?|family|marriage))\b",
    rf"\b(?:married|divorced|widow(?:ed|er)?|single (?:mom|mother|dad|father|parent)|marital status"
    rf"|i{_APOS}?m single|i am single|(?:i{_APOS}?m|we{_APOS}?re|i am|we are) engaged)\b",
    r"\b(?:i|we) have (?:\w+ )?(?:kids|children|a baby|a son|a daughter|sons|daughters)\b",
    r"\bmy (?:kids|children|sons?|daughters?|baby|wife|husband|girlfriend|boyfriend|fiancee?)\b",
    r"\b(?:nacionalidad|extranjer[oa]s?|migrantes?|inmigrantes?|indigenas?|raza|origen etnico)\b",
    r"\b(?:religion|catolic[oa]s?|cristian[oa]s?|musulman\w*|judi[oa]s?|ateo|atea|evangelic[oa]s?|budistas?)\b",
    r"\b(?:soy (?:mujer|hombre|gay|lesbiana|trans)|genero|orientacion sexual|embarazad[oa])\b",
    r"\b(?:somos (?:gays?|lesbianas)|pareja (?:gay|homosexual|del mismo sexo))\b",
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
    (re.compile(r"my (?:kids|children|sons?|daughters?|baby)"), "my family"),
    (re.compile(r"my (?:wife|husband|girlfriend|boyfriend|fiancee?)"), "my partner"),
)

# Lo que puede quedar de un mensaje al quitar una mención protegida ("Soy ___") sin aportar contenido.
_FILLER_WORDS = frozenset({"soy", "estoy", "somos", "estamos", "tengo", "tenemos", "y", "e", "es", "mi", "que", "con",
                           "pero", "de", "un", "una", "el", "la",
                           "i", "m", "am", "we", "re", "are", "s", "is", "have", "has", "and", "my", "that", "with",
                           "but", "of", "a", "an", "the", "also", "too"})

# --- PII no solicitada --------------------------------------------------------------------------
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
# Documentos de Colombia (DT-55) y sus nombres en inglés: cédula de ciudadanía o de extranjería (CC, CE),
# NIT, pasaporte, licencia de conducción. El número puede venir con puntos o guion ("1.023.456.789",
# "900.123.456-7"). Sin el nombre del documento delante, un número de 10 dígitos es indistinguible de un
# teléfono, y eso no se toca.
_ID_DOCUMENT = re.compile(
    r"\b(?:c[eé]dula(?: de (?:ciudadan[ií]a|extranjer[ií]a))?|c\.?c\.?|c\.?e\.?|nit|pasaporte|passport"
    r"|licencia(?: de conducci[oó]n| de conducir)?|driver['\u2019]?s? licen[cs]e|national id|id (?:number|card)"
    r"|ssn|social security(?: number)?)(?!\w)[^\w\n]{0,10}"
    r"(?:(?:es|is|n[uú]mero|number|no\.?|#)[^\w\n]{0,5}){0,2}(?=[A-Z0-9.\-]*\d)[A-Z0-9](?:[A-Z0-9.\-]{4,}[A-Z0-9])\b",
    re.IGNORECASE)


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
    not_english: bool = False  # escrito en español: se pide repetirlo en inglés (#202, DT-55)

    @property
    def flagged(self) -> bool:
        return (self.opt_out or self.jailbreak or bool(self.pii) or self.out_of_domain or self.protected_category
                or self.not_english)

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
        if self.not_english:
            kinds.append(("input.not_english", {}))
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


# El resumen del traspaso (A-16) habla del lead en tercera persona: "the lead is Colombian" no pasa por
# "I'm/we're", que es lo único que hace contar a los gentilicios ambiguos y a la religión en singular.
_EN_THIRD = rf"(?:he{_APOS}?s|he is|she{_APOS}?s|she is|they{_APOS}?re|they are|(?:the )?lead is)\s+(?:an?\s+)?"
_PROTECTED_THIRD_PERSON = _any((
    rf"\b{_EN_THIRD}{_EN_AMBIGUOUS_DEMONYMS}\b",
    rf"\b{_EN_THIRD}(?:catholic|christian|muslim|jew|jewish|atheist|evangelical|buddhist|hindu|mormon)\b",
))


def describes_protected(text: str) -> bool:
    """True si el texto dice de un tercero algo de una categoría protegida, como lo diría un resumen."""
    return mentions_protected(text) or bool(_PROTECTED_THIRD_PERSON.search(normalize(text)))


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
        not_english=is_spanish(clean),
    )
