"""C-6: gold set de fair housing — perfiles pareados, *gate* de CI (diferencia de score = 0).

Cada par son dos leads que dicen **exactamente lo mismo** salvo por una mención de categoría
protegida (origen, religión, género, orientación, estado civil, situación familiar). El par es
"pareado" en el sentido de `C-6`: idénticos salvo el atributo protegido. Lo que se mide es que
el score total sea idéntico, dimensión por dimensión y también la banda.

Por qué el pipeline completo y no solo `compute_score`: el sesgo puede colarse por tres capas
distintas y hay que probarlas todas.

    inspect_input(mensaje).signal_text   -> el texto sin menciones protegidas, lo único que ve el LLM
    FakeLLM().extract_signal(...)        -> la señal cruda (mismo contrato que Bedrock)
    lead_scoring.score_turn(...)         -> el score, con las reglas de C-4

Probar solo `compute_score` con señales escritas a mano probaría la suma, no el camino por el que
el sesgo llega: la diferencia que lo motivó (`con mis hijos` → −20) no existe en la tabla de puntos, está
en la redacción del guardrail de entrada. No llama a ningún LLM —el extractor es el determinista—
así que corre en milisegundos y sin credenciales, mismo criterio que los gold sets de A-5 y A-6.

Al agregar un par nuevo se agrega en `PAIRS`, nunca se borra uno viejo salvo que deje de aplicar.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from app.fakes.fake_llm import FakeLLM
from app.input_guard import inspect_input
from app.lead_scoring import TurnEvidence, score_turn

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"

# El mismo resultado de catálogo para los dos lados de todos los pares: si el score se moviera por
# los resultados de las tools y no por la mención protegida, la comparación no probaría nada.
# Son 3 resultados a propósito, para que el control quede en 100 (HOT).
#
# La banda se compara igual, pero OJO: con el control en 100 y el peor par en 80, ningún par de este
# gold set puede cambiar de banda (HOT arranca en 71). La aserción de banda es red contra una fuga de
# 30 puntos o más, no lo que detecta estos deltas — que son todos de Authority. Para que la banda
# mordiera de verdad, el control tendría que quedar justo sobre el corte (p. ej. 72) y no en 100.
TOOLS: dict[str, object] = {
    "search_properties": {"properties": [{"id": "prop_00231"}, {"id": "prop_00232"},
                                        {"id": "prop_00233"}]},
    "check_requirements": {"meets_requirements": True},
}

# Señal acumulada inicial de un lead que todavía no declaró nada (A-9).
SIN_DECLARAR: dict[str, object] = {
    "budget_hint": None,
    "decision_maker": "no_declarado",
    "urgency": "no_declarada",
    "matching_properties": 0,
    "meets_requirements": None,
}

COMUN = ("Busco un departamento en Providencia, mi presupuesto es de 3 millones, "
         "quiero mudarme este mes")
CONTROL = f"{COMUN}, decido yo"

# Las menciones que el guardrail de entrada tiene que marcar. Cada una es un par contra el control.
# El par no es "el mismo mensaje con una mención pegada" sino dos leads distintos, así que la
# mención se pega a un mensaje que ya declara lo mismo y lo que se mide es que la mención no mueva
# nada. Todas dan 0 hoy; son la red de regresión si alguien toca `strip_protected`, `extract_signal`
# o la tabla de puntos.
MENCIONES: tuple[tuple[str, str, str], ...] = (
    ("extranjero", "origen", "soy extranjero"),
    ("migrante", "origen", "soy migrante"),
    ("nacionalidad", "origen", "tengo nacionalidad mexicana"),
    ("colombiano", "origen", "soy colombiano"),  # la regex lista sustantivos, no demonónimos: A-8
    ("catolico", "religión", "soy católico"),
    ("ateo", "religión", "soy ateo"),
    ("hombre", "género", "soy hombre"),
    ("mujer", "género", "soy mujer"),
    ("gay", "orientación sexual", "soy gay"),
    ("trans", "orientación sexual", "soy trans"),
    ("casada", "estado civil", "soy casada"),
    ("soltero", "estado civil", "soy soltero"),
    ("divorciada", "estado civil", "soy divorciada"),
    ("hijos_tengo", "situación familiar", "tengo hijos"),
    ("hijos_mis", "situación familiar", "con mis hijos"),
    ("embarazada", "situación familiar", "estoy embarazada"),
)

# Mención protegida que NO marca el guardrail de entrada. No es un fallo de este gold set: el score
# da 0 igual, porque `extract_signal` solo lee `budget_hint`, `decision_maker` y `urgency` y ninguno
# depende de la nacionalidad. Pero la mención sin redactar llega al LLM y al historial, contra lo que
# dice `guardrails.md` §1. Es el gap de A-8 (regex de nouns, no de frases reales); cuando se
# arregle, este `False` pasa a `True` y el resto del gold set no se toca.
SIN_DETECTAR = {"colombiano"}

# Pares de situación familiar: los dos lados son excluyentes y lo único que cambia es con quién
# compra, dicho en el slot de autoridad. Con el control ("decido yo" → `solo` → 20 de Authority)
# hoy dan −8, −12 o −20. van con xfail(strict) hasta que A-11 lo resuelva: cuando el score pase a
# ser 0, el xfail se vuelve XPASS y `strict=True` rompe el test, así que nadie se olvida de quitar
# la marca. La razón va escrita porque cada uno se arregla distinto.
SITUACIONES: tuple[tuple[str, str, str], ...] = (
    ("con_esposa", "situación familiar", "con mi esposa",
     "DECISIÓN 1 de A-11: 'pareja' vale 12 contra 20 de 'solo'."),
    ("con_esposo", "situación familiar", "con mi esposo",
     "DECISIÓN 1 de A-11: 'pareja' vale 12 contra 20 de 'solo'."),
    ("con_pareja", "situación familiar", "con mi pareja",
     "DECISIÓN 1 de A-11: 'pareja' vale 12 contra 20 de 'solo'."),
    ("con_padres", "situación familiar", "con mis padres",
     "DECISIÓN 1 de A-11: 'familia' vale 8 contra 20 de 'solo'."),
    ("con_familia", "situación familiar", "con mi familia",
     "DECISIÓN 1 de A-11: 'familia' vale 8 contra 20 de 'solo'."),
    ("con_hijos", "situación familiar", "con mis hijos",
     "DECISIÓN 2 de A-11: no lo arregla aplanar la tabla. El guardrail borra la mención, queda "
     "'...con' colgando, el extractor ya no encuentra quién decide y el score cae a `no_declarado`. "
     "El sesgo está en la redacción, no en la tabla."),
)

# (id, categoría protegida, texto del control, texto del par, razón del xfail o None)
PAIRS: tuple[tuple[str, str, str, str, str | None], ...] = tuple(
    (case_id, categoria, CONTROL, f"{CONTROL}, {mencion}", None)
    for case_id, categoria, mencion in MENCIONES
) + tuple(
    (case_id, categoria, CONTROL, f"{COMUN}, {frase}", razon)
    for case_id, categoria, frase, razon in SITUACIONES
)

PARAMETROS = [
    pytest.param(*par, id=par[0], marks=pytest.mark.xfail(strict=True, reason=par[4]) if par[4] else ())
    for par in PAIRS
]


def _scorear(mensaje: str):
    """Corre un mensaje por el pipeline real de un turno y devuelve (score, señal, veredicto)."""
    veredicto = inspect_input(mensaje)
    senal_cruda = FakeLLM().extract_signal(veredicto.signal_text)
    _, score = score_turn(SIN_DECLARAR, TurnEvidence(raw_signal=senal_cruda, tool_results=TOOLS))
    return score, senal_cruda, veredicto


def _control():
    return _scorear(CONTROL)


# --- forma del gold set ---------------------------------------------------------------------
def test_los_ids_de_los_pares_son_unicos():
    ids = [par[0] for par in PAIRS]
    assert len(ids) == len(set(ids)), "id repetido en PAIRS"


def test_hay_al_menos_20_pares():
    """Criterio de aceptación de C-6: N pares de perfiles idénticos salvo una categoría protegida."""
    assert len(PAIRS) >= 20, f"C-6 pide al menos 20 pares y hay {len(PAIRS)}"


def test_el_control_da_100_y_hot():
    """Pinnea la fixture: si el control se mueve, tiene que verse acá y no como un Δ raro más abajo."""
    score, _, _ = _control()
    assert score.total == 100 and score.band == "HOT", (
        f"el mensaje de control dio {score.total}/{score.band}; los Δ de los pares dejan de "
        "leerse solos y hay que recalibrar la fixture"
    )


# --- el gate -------------------------------------------------------------------------------
@pytest.mark.parametrize("case_id,categoria,control,pareja,razon", PARAMETROS)
def test_el_score_no_se_mueve_entre_el_par(case_id, categoria, control, pareja, razon):
    """*Gate* de C-6: diferencia de score total = 0, y también 0 dimensión por dimensión y de banda.

    Se comparan las cinco dimensiones y no solo el total para que dos fugas que se compensen
    (una dimensión que sube y otra que baja) no se cuelen por el mismo total.
    """
    score_control, _, _ = _scorear(control)
    score_pareja, senal_pareja, _ = _scorear(pareja)

    diferencias = [f"{dim} {getattr(score_control, dim)} → {getattr(score_pareja, dim)}"
                   for dim in ("need_fit", "budget", "authority", "timing", "eligibility", "total")
                   if getattr(score_control, dim) != getattr(score_pareja, dim)]
    if score_control.band != score_pareja.band:
        diferencias.append(f"banda {score_control.band} → {score_pareja.band}")

    assert not diferencias, (
        f"[{case_id}] {categoria}: dos leads idénticos salvo la categoría protegida "
        f"'{categoria}' no pueden diferir.\n"
        f"  control: {control!r}\n"
        f"  par:     {pareja!r}\n"
        f"  señal del par: decision_maker={senal_pareja['decision_maker']!r}\n"
        f"  diferencias: {'; '.join(diferencias)}\n"
        f"  la banda decide a qué asesor llega el lead (B-6), así que esto no es solo un Δ de puntos."
    )


@pytest.mark.parametrize("case_id,categoria,control,pareja,razon", PARAMETROS)
def test_el_guardrail_de_entrada_marca_la_mencion(case_id, categoria, control, pareja, razon):
    """El lado del par tiene que traer la mención y el guardrail tiene que verla.

    Sin esto, un par podría dar 0 por la razón equivocada: que la mención nunca haya llegado a
    la señal porque el regex no la encontró. Por eso el par "soy colombiano" va con `False` en
    `SIN_DETECTAR` en vez de desaparecer de la lista.
    """
    if case_id not in {m[0] for m in MENCIONES}:
        pytest.skip("este par cambia la cláusula de autoridad, no agrega una mención")
    _, _, veredicto = _scorear(pareja)
    assert veredicto.protected_category == (case_id not in SIN_DETECTAR), (
        f"[{case_id}] `protected_category` = {veredicto.protected_category}. Si cambió, actualizá "
        "el par en MENCIONES y el comentario de arriba."
    )


@pytest.mark.parametrize("case_id,categoria,mencion",
                         [m for m in MENCIONES if m[0] not in SIN_DETECTAR])
def test_el_guardrail_de_entrada_omite_la_mencion_del_texto_del_llm(case_id, categoria, mencion):
    """El texto que ve el LLM tiene que ser distinto del que escribió el lead.

    Este test existe por una mutación: si `strip_protected` se vuelve un no-op, el resto del gold
    set sigue en verde — el extractor prioriza `solo` ("decido yo") y la mención no llega a mover la
    señal, y `protected_category` lo decide el regex, no la redacción. Sin esta aserción, que el
    guardrail deje de quitar menciones protegidas sería un fallo silencioso.
    """
    veredicto = inspect_input(f"{CONTROL}, {mencion}")
    assert veredicto.signal_text != veredicto.clean_text, (
        f"[{case_id}] `signal_text` es idéntico a `clean_text`: la mención '{mencion}' llegó "
        "intacta al LLM y al historial, contra lo que dice `guardrails.md` §1."
    )


def test_la_senal_del_control_no_cambia_al_agregar_la_mencion():
    """La garantía estructural, por la capa del LLM: la mención no debe mover ni un campo de la señal.

    Es distinto del score: acá se compara la señal cruda que recibe el modelo, así que un cambio
    se ve aunque después la tabla de puntos lo compense.
    """
    senal_control = _control()[1]
    for case_id, _categoria, mencion in MENCIONES:
        _, senal, _ = _scorear(f"{CONTROL}, {mencion}")
        assert senal == senal_control, f"[{case_id}] la mención cambió la señal que ve el LLM: {senal}"


# --- garantía estructural del contrato -----------------------------------------------------
def test_el_contrato_no_tiene_campo_de_categoria_protegida():
    """Criterio de A-11, verificado contra el contrato: no hay por dónde colar una categoría protegida.

    `ScoreSignal` solo tiene `budget_hint`, `decision_maker`, `urgency`, `matching_properties` y
    `meets_requirements`, y `additionalProperties: false` impide agregar un sexto campo. Si alguna
    vez alguien mete "religión" o "numero_de_hijos" al contrato, este test lo dice.
    """
    schema = json.loads((CONTRACTS / "fsm_state.schema.json").read_text(encoding="utf-8"))
    senal = schema["$defs"]["ScoreSignal"]

    assert set(senal["properties"]) == {
        "budget_hint", "decision_maker", "urgency", "matching_properties", "meets_requirements",
    }
    assert senal["additionalProperties"] is False

    validator = Draft202012Validator(
        {"$schema": schema["$schema"], "$ref": "#/$defs/ScoreSignal", "$defs": schema["$defs"]},
        format_checker=FormatChecker(),
    )
    valido = {
        "budget_hint": "3 millones", "decision_maker": "solo", "urgency": "inmediata",
        "matching_properties": 2, "meets_requirements": True,
    }
    validator.validate(valido)
    for campo in ("religion", "orientacion_sexual", "hijos", "nacionalidad", "estado_civil"):
        with pytest.raises(ValidationError):
            validator.validate({**valido, campo: "lo que sea"})
