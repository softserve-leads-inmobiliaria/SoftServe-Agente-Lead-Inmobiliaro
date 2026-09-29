"""OBS-4: los gold sets como *gate* de merge — un solo lugar donde vive el umbral de cada uno.

Cada gold set traía su umbral escrito a mano dentro de su propio archivo (`assert len(CASES) >= N` a
nivel de módulo). Eso hace que un gold set bajo su umbral rompa CI, pero no que el *equipo* sepa
cuál es el número: los umbrales estaban sueltos, tres de cuatro no coincidían con lo que dice su
propia historia, y un gold set nuevo sin `assert` pasaba inadvertido para siempre.

Este archivo es el registro. `GOLD_SETS` declara, para cada gold set, la historia que lo define y el
mínimo que esa historia pide, y hay tres cosas que se comprueban:

1. Cada gold set registrado tiene al menos los casos que su historia exige.
2. Todo gold set que exista en esta carpeta está registrado aquí — si aparece uno nuevo y nadie lo
   anotó, el CI se cae acá y dice cuál.
3. El gate se autocomprueba: se le pasa un gold set con un mínimo imposible y tiene que detectarlo.
   Un gate que no muerde no es un gate.

Dos cosas que este archivo NO hace, a propósito:

- No reimplementa las comprobaciones de cada gold set. Esas ya corren como tests propios; aquí solo
  se mira el conteo, que es lo que la historia define como umbral.
- No toca el `assert` de dentro de cada gold set. Sigue estando, como red local: si alguien lo baja,
  su propio archivo se pone rojo aunque este registro no lo note.

**Convención de nombres:** un gold set es un archivo `*gold*.py` de esta carpeta. La regla (2) se
apoya en eso, así que un gold set nuevo con otro nombre no lo encontraría — por eso el registro es
la lista explícita y la regla (2) es la red, no al revés. `test_promise_guard.py` (A-7) está
registrado aunque no lleve "gold" en el nombre: es una suite adversarial con el mismo tipo de umbral.
"""
from __future__ import annotations

import importlib
import warnings
from dataclasses import dataclass
from pathlib import Path

CARPETA = Path(__file__).parent


@dataclass(frozen=True)
class GoldSet:
    """Un gold set y el mínimo que su propia historia le exige."""

    historia: str
    modulo: str
    atributo: str
    minimo: int
    nota: str = ""
    #: Si es `False`, el gate espera a que el módulo aterrice en `main` en vez de fallar.
    requerido: bool = True


GOLD_SETS = (
    GoldSet(
        historia="A-5",
        modulo="test_factuality_gold_set",
        atributo="CASES",
        minimo=30,
        nota="≥30 preguntas (A-05.md). Gold set sintético; el de catálogo real es C-7.",
    ),
    GoldSet(
        historia="C-8",
        modulo="test_address_guard_gold_set",
        atributo="CASES",
        minimo=15,
        nota="18 casos: 9 que deben disparar el guardrail y 9 que no.",
    ),
    GoldSet(
        historia="C-6",
        modulo="test_fair_housing_gold_set",
        atributo="PAIRS",
        minimo=20,
        nota=(
            "22 pares, todos con diferencia 0 desde A-11 (DT-38). El umbral es el conteo de pares; "
            "la diferencia = 0 la vigila el propio gold set."
        ),
    ),
    GoldSet(
        historia="A-7",
        modulo="test_promise_guard",
        atributo="CASES",
        minimo=15,
        nota="Suite adversarial: los 6 claims con código, en positivo y negativo.",
    ),
    GoldSet(
        historia="C-7",
        modulo="test_factuality_catalogo_gold_set",
        atributo="CASOS",
        minimo=30,
        nota="75 casos sobre la salida real del catálogo de demo (DT-36).",
    ),
)


def _evaluar(entradas) -> list[str]:
    """Devuelve los incumplimientos: un mensaje por gold set que no llega a su mínimo."""
    fallos: list[str] = []
    for gs in entradas:
        try:
            modulo = importlib.import_module(gs.modulo)
        except ModuleNotFoundError:
            if gs.requerido:
                fallos.append(
                    f"{gs.historia}: el registro exige {gs.modulo} pero el módulo no está en esta carpeta"
                )
            continue
        except AssertionError as exc:
            fallos.append(
                f"{gs.historia}: el `assert` propio de {gs.modulo} ya está en rojo ({exc}). "
                f"El archivo se puso rojo antes de que este gate lo mirara."
            )
            continue
        casos = getattr(modulo, gs.atributo, None)
        if casos is None:
            fallos.append(
                f"{gs.historia}: {gs.modulo} no expone `{gs.atributo}`, así que su umbral "
                f"({gs.minimo}) no se puede comprobar"
            )
        elif len(casos) < gs.minimo:
            fallos.append(
                f"{gs.historia}: {gs.modulo}.{gs.atributo} tiene {len(casos)} casos y su historia "
                f"pide al menos {gs.minimo}"
            )
    return fallos


def test_umbral_de_cada_gold_set():
    """El AC de OBS-4: CI falla si un gold set no está al umbral de su propia historia."""
    fallos = _evaluar(GOLD_SETS)
    assert not fallos, "\n".join(fallos)


def test_todo_gold_set_del_repo_esta_registrado():
    """Un gold set nuevo sin registrar = umbral desconocido = el gate no lo cubre. Se cae acá."""
    registrados = {gs.modulo for gs in GOLD_SETS}
    en_la_carpeta = {p.stem for p in CARPETA.glob("*gold*.py") if p.stem != Path(__file__).stem}
    sin_registrar = sorted(en_la_carpeta - registrados)
    assert not sin_registrar, (
        "hay gold sets en esta carpeta que no están en GOLD_SETS, así que OBS-4 no les está "
        f"comprobando el umbral: {sin_registrar}. Agrégalos al registro con su mínimo, o renómbralos "
        "si no son gold sets."
    )


def test_gold_sets_que_aun_no_aterrizaron_en_main():
    """Entradas del registro cuyo módulo todavía no está: informa, no falla.

    Es lo que permite abrir el PR de OBS-4 sin esperar al de C-7. Cuando su módulo aterrice, esta
    lista se vacía sola y la entrada pasa a exigir su umbral en el mismo run.
    """
    pendientes = []
    for gs in GOLD_SETS:
        if gs.requerido:
            continue
        try:
            importlib.import_module(gs.modulo)
        except ModuleNotFoundError:
            pendientes.append(f"{gs.historia} ({gs.modulo}): el gate lo espera, todavía no está en main")
    if pendientes:
        warnings.warn(
            "entradas del registro en espera — su umbral todavía no se está comprobando:\n  - "
            + "\n  - ".join(pendientes),
            stacklevel=2,
        )


def test_el_gate_detecta_un_gold_set_bajo_su_umbral():
    """Autocomprobación: si esto no falla, el gate no muerde y OBS-4 no está cumpliendo nada."""
    imposible = GoldSet(
        historia="autocomprobación",
        modulo="test_address_guard_gold_set",
        atributo="CASES",
        minimo=10_000,
    )
    fallos = _evaluar([imposible])
    assert fallos, "el gate NO detectó un gold set de 18 casos contra un mínimo de 10.000"
    assert "10000" in fallos[0], f"el mensaje del gate no dice cuál era el mínimo: {fallos[0]!r}"
