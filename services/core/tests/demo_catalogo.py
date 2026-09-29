"""C-7: los `tool_results` del gold set de factualidad, sacados del catálogo de demo de verdad.

El gold set de A-5 (`test_factuality_gold_set.py`) congela dicts escritos a mano. Al migrar el
catálogo a Postgres (C-1) y sembrar el inventario de demo (C-9) esos dicts quedaron desfasados: sus
`search_properties` no tienen `address_approx` (que el contrato exige), sus `get_property` no tienen
`type`/`zone`/`address_exact` y sus `get_promotions` no tienen `valid_until`. Nada lo señalaba,
porque ninguna fixture del gold set se validaba contra `contracts/mcp/`. Este módulo cierra ese hueco:
las fixtures salen de `CatalogService` corriendo sobre el mismo Postgres migrado que usa el resto de
la suite, sembrado con el inventario de `scripts/seed_catalog.py` (C-9).

**Por qué un subproceso y no un import.** `core` y `catalog` comparten el nombre de paquete `app`
(`services/core/conftest.py` y `services/catalog/conftest.py` lo ponen en `sys.path`; CI corre cada
carpeta en un proceso distinto por eso). Traer `app.service.CatalogService` a un test de `core` exigiría
manipular `sys.modules` a mano. Un subproceso con `PYTHONPATH=services/catalog` resuelve el choque de
formas honestas: cada proceso ve un solo `app`. La alternativa —volcar el JSON a un archivo commiteado—
se descartó porque reintroduce justo la deriva que C-7 viene a cerrar: un archivo que nadie regenera
termina dejando de ser la salida del catálogo sin que nadie lo note.

**Por qué el inventario no se duplica aquí.** Los datos son los de `services/catalog/app/data.py` y la
inserción reutiliza `scripts/seed_catalog.seed()`, el mismo del compose. Si `data.py` cambia, las
fixtures de este módulo cambian con él, que es el objetivo.

**El reloj va fijo a propósito.** `AvailabilityGuard` (A-4) estampa `availability_checked_at` en cada
propiedad revalidada, y `factuality._walk` extrae números de cualquier string del payload — un ISO8601
real deja pasar `2026`, `01` y `00` como si fueran datos de la propiedad, y los microsegundos cambian en
cada corrida, así que un caso que rozara ese número haría fallar el gold set al azar. Se le inyecta
`2026-01-01T00:00:00+00:00` al `CatalogService`, el mismo valor estable que ya usan `catalog_stub.py` y
`test_availability.py`: el comportamiento del guardrail no cambia, solo se le quita la dependencia del reloj.

**La capa de A-4 la pone el guardrail real, no una copia.** `AvailabilityGuard` (A-4) no habla con el
catálogo por MCP: solo necesita un objeto con `await call(tool, arguments)`. `_TurnoGrabado` se lo da
reproduciendo las salidas de la corrida —incluida la de `get_availability` de cada propiedad—, así que lo
que se guarda en `con_a4` es lo que A-4 haría en producción. La alternativa, replicar su lógica acá, se
descartó: una copia de `availability.py` en el test queda en verde cuando el guardrail cambia, que es
justo el modo de falla que C-7 vino a cerrar.

`Demo` expone las dos vistas de esa misma corrida: `crudo` (la salida del catálogo, la única que se
puede validar contra `contracts/mcp/`, que tiene `additionalProperties: false`) y `con_a4` (la que
realmente ve el guardrail, con los campos que agrega la revalidación).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from typing import Any

from app.availability import AvailabilityGuard
from db.repo_root import repo_root

# Timestamp de la revalidación de A-4. Ver la nota del docstring: los números de un ISO8601 real se
# colarían en el set de números permitidos de `factuality.check()`. El valor no se fija acá sino en el
# reloj que se le inyecta a `CatalogService` (ver `_GENERADOR`); se deja anotado porque es el que tiene
# que aparecer en `availability_checked_at` y así se comprueba en el test.
CHECKED_AT = "2026-01-01T00:00:00+00:00"

# Herramienta -> argumentos con los que se pide. Cada entrada es un turno posible del agente: el
# `tool_results` que A-5 recibe es exactamente el de la herramienta que el LLM llamó ese turno.
#
# Los argumentos se eligen para que entre el inventario de demo completo (12 propiedades, 3
# promociones) y para que cada caso borde de C-9 quede en algún turno: `apartado` y `cerrado` en las
# búsquedas, las tres sin recámaras en el filtro `bedrooms: 0`, la promoción vencida en
# `get_promotions`, y los tres tipos (venta/renta/preventa) en `check_requirements`, que es donde se
# nota la diferencia de enganche (10% vs 20% vs ingreso x3).
TURNOS: dict[str, tuple[str, dict[str, Any]]] = {
    # --- búsquedas -------------------------------------------------------------------------------
    "busqueda_sin_filtros": ("search_properties", {}),
    "busqueda_venta_providencia": ("search_properties", {"zone": "Providencia"}),
    "busqueda_zapopan": ("search_properties", {"zone": "Zapopan"}),
    "busqueda_renta": ("search_properties", {"type": "renta"}),
    "busqueda_preventa": ("search_properties", {"type": "preventa"}),
    "busqueda_sin_recamaras": ("search_properties", {"bedrooms": 0}),
    "busqueda_presupuesto_venta": ("search_properties", {"budget_range": {"min": 2000000, "max": 5000000}}),
    "busqueda_presupuesto_renta": ("search_properties", {"budget_range": {"min": 9000, "max": 20000}}),
    "busqueda_una_recamara": ("search_properties", {"bedrooms": 1}),
    "busqueda_zona_sin_coincidencia": ("search_properties", {"zone": "Polanco"}),
    # --- ficha ------------------------------------------------------------------------------------
    "ficha_venta": ("get_property", {"property_id": "prop_00231", "lead_qualified": False}),
    "ficha_venta_calificado": ("get_property", {"property_id": "prop_00231", "lead_qualified": True}),
    "ficha_terreno_calificado": ("get_property", {"property_id": "prop_00241", "lead_qualified": True}),
    "ficha_renta": ("get_property", {"property_id": "prop_00235", "lead_qualified": False}),
    "ficha_preventa": ("get_property", {"property_id": "prop_00237", "lead_qualified": False}),
    "ficha_cerrada": ("get_property", {"property_id": "prop_00239", "lead_qualified": False}),
    # --- disponibilidad ---------------------------------------------------------------------------
    "disponibilidad_apartada": ("get_availability", {"property_id": "prop_00234"}),
    "disponibilidad_cerrada": ("get_availability", {"property_id": "prop_00239"}),
    "disponibilidad_disponible": ("get_availability", {"property_id": "prop_00231"}),
    # --- promociones -----------------------------------------------------------------------------
    "promociones_vigentes": ("get_promotions", {}),
    "promociones_de_propiedad": ("get_promotions", {"property_id": "prop_00233"}),
    "promociones_sin_promociones": ("get_promotions", {"property_id": "prop_00235"}),
    # --- requisitos -------------------------------------------------------------------------------
    "requisitos_venta_no_cumple": ("check_requirements", {
        "property_id": "prop_00231",
        "lead_profile": {"down_payment": 100000, "monthly_income": 50000, "has_references": True}}),
    "requisitos_venta_cumple": ("check_requirements", {
        "property_id": "prop_00231",
        "lead_profile": {"down_payment": 500000, "monthly_income": 50000, "has_references": True}}),
    "requisitos_preventa_no_cumple": ("check_requirements", {
        "property_id": "prop_00237",
        "lead_profile": {"down_payment": 500000, "monthly_income": 50000, "has_references": True}}),
    "requisitos_renta_no_cumple": ("check_requirements", {
        "property_id": "prop_00235",
        "lead_profile": {"down_payment": 0, "monthly_income": 20000, "has_references": False}}),
    "requisitos_renta_cumple": ("check_requirements", {
        "property_id": "prop_00235",
        "lead_profile": {"down_payment": 0, "monthly_income": 90000, "has_references": True}}),
}


# El generador corre como `__main__` dentro del subproceso, con `services/catalog` en el PYTHONPATH.
# Vive aquí (y no en el servicio del catálogo) para que el gold set y su fuente de datos estén en el
# mismo PR. Es el único punto del repo desde donde se importa el `app` del catálogo: este proceso ve
# un solo `app`, el suyo. El de `core` (este módulo importa `app.availability`) es otro proceso.
_GENERADOR = '''
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
turnos = json.loads(sys.argv[2])
os.environ["DATABASE_URL"] = sys.argv[3]
sys.path.insert(0, str(root / "scripts"))

from seed_catalog import seed
from db.session import get_engine
from sqlalchemy.orm import sessionmaker
from app.service import CatalogService

engine = get_engine()
seed(engine)
servicio = CatalogService(
    db_session_factory=sessionmaker(autocommit=False, autoflush=False, bind=engine),
    # `get_availability` estampa `checked_at: datetime.now()`, y `factuality._walk` saca números de
    # cualquier string del payload: los microsegundos de esa hora ("35.824340") se colarían como un
    # número autorizado que cambia en cada corrida, y cualquier caso que lo rozara haría fallar el
    # gold set al azar. `CatalogService` recibe el reloj por inyección para esto. Mismo valor estable que
    # usa `catalog_stub.py` para CHECKED_AT.
    now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))

salida = {}
ids = set()
for nombre, (tool, argumentos) in turnos.items():
    try:
        payload = getattr(servicio, tool)(**argumentos)
        salida[nombre] = {tool: payload}
        if tool == "search_properties":
            ids.update(p.get("id") for p in payload.get("properties", []))
        elif tool == "get_property":
            ids.add(payload.get("id"))
    except Exception as exc:  # una tool que falla es un hallazgo, no un test que se salta
        salida[nombre] = {"__error__": f"{type(exc).__name__}: {exc}"}

# La revalidación de A-4 necesita el `get_availability` de cada propiedad que aparece en algún turno.
# Se calcula acá, con el mismo reloj fijo, para que el `AvailabilityGuard` de `core` pueda correr de
# verdad en el otro proceso sin volver a pegarle a la base.
disponibilidad = {}
for pid in sorted(i for i in ids if i):
    try:
        disponibilidad[pid] = servicio.get_availability(property_id=pid)
    except Exception as exc:
        disponibilidad[pid] = {"__error__": f"{type(exc).__name__}: {exc}"}

print(json.dumps({"turnos": salida, "disponibilidad": disponibilidad}, ensure_ascii=False))
'''


class _TurnoGrabado:
    """Un `catalog` que solo repite la salida ya calculada de **un** turno.

    `AvailabilityGuard` (A-4) no habla con el catálogo por MCP: solo necesita un objeto con
    `await call(tool, arguments)`. Por eso el guardrail de producción se puede ejecutar acá tal cual,
    sin red y sin cliente: lo que se le da son las salidas reales de la corrida, incluida la de
    `get_availability` de cada propiedad. Es lo que hace que el payload post-A-4 sea el del guardrail
    real y no una copia de su lógica — si `availability.py` cambia la forma, el gold set cambia con él.
    """

    def __init__(self, tool: str, payload: dict[str, Any], disponibilidad: dict[str, Any]) -> None:
        self._tool = tool
        self._payload = payload
        self._disponibilidad = disponibilidad

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if tool == self._tool:
            return self._payload
        if tool == "get_availability":
            property_id = (arguments or {}).get("property_id")
            if property_id not in self._disponibilidad:
                # `LookupError` y no `CatalogToolError` a propósito: el guard atrapa las de catálogo y
                # degrada a `sin_verificar`, y un id que falte es un hueco del generador, no un estado
                # de disponibilidad. Tapar eso convertiría un error del test en un payload plausible.
                raise LookupError(f"la corrida no calculó la disponibilidad de {property_id!r}")
            return self._disponibilidad[property_id]
        raise LookupError(f"la corrida no tiene la salida de {tool!r}")

    async def tool_specs(self) -> list[dict[str, Any]]:
        return []


async def _pasar_por_a4(crudo: dict[str, dict[str, Any]], disponibilidad: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Aplica el `AvailabilityGuard` real a cada turno y devuelve los payloads bajo su nombre de tool."""
    salida = {}
    for nombre, (tool, argumentos) in TURNOS.items():
        guard = AvailabilityGuard(_TurnoGrabado(tool, crudo[nombre][tool], disponibilidad))
        salida[nombre] = {tool: await guard.call(tool, argumentos)}
    return salida


def _correr_generador(database_url: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Pide al subproceso la salida cruda de `CatalogService` para cada turno de `TURNOS`."""
    root = repo_root()
    entorno = dict(os.environ)
    # `services/catalog` primero: el `app` que importa el generador es el del catálogo, no el de core.
    entorno["PYTHONPATH"] = os.pathsep.join([str(root / "services" / "catalog"), str(root), entorno.get("PYTHONPATH", "")])
    terminado = subprocess.run(
        [sys.executable, "-c", _GENERADOR, str(root), json.dumps(TURNOS), database_url],
        capture_output=True, text=True, env=entorno, cwd=str(root), check=False)
    if terminado.returncode != 0:
        raise RuntimeError(f"el generador del catálogo falló ({terminado.returncode}):\n{terminado.stderr}")
    try:
        salida = json.loads(terminado.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"el generador del catálogo no devolvió JSON: {terminado.stdout[:400]!r}") from exc
    return salida["turnos"], salida["disponibilidad"]


class Demo:
    """Las dos vistas del mismo instante: la salida cruda del catálogo y la que ve el guardrail.

    Salen de **una sola** corrida del generador a propósito. `get_availability` estampa
    `checked_at` en cada llamada, así que pedir los payloads dos veces daría dos instantes distintos y
    cualquier comparación entre las dos vistas sería mentira.
    """

    def __init__(self, crudo: dict[str, dict[str, Any]], disponibilidad: dict[str, Any]) -> None:
        self.crudo = crudo
        # Lo que respondió `get_availability` por propiedad: queda a la vista para que el test de A-4
        # pueda comprobar que lo que el guardrail estampó es esto y no el estado del listado.
        self.disponibilidad = disponibilidad
        self.con_a4 = asyncio.run(_pasar_por_a4(crudo, disponibilidad))


def demo_del_catalogo(database_url: str) -> Demo:
    """Salida real de `CatalogService` para cada turno de `TURNOS`, cruda y pasada por A-4.

    Falla (no omite) si alguna tool no se pudo ejecutar: un turno que el catálogo no puede responder es
    un hueco del gold set, no una razón para dejarlo fuera.
    """
    crudo, disponibilidad = _correr_generador(database_url)
    for nombre, (tool, _args) in TURNOS.items():
        salida = crudo.get(nombre)
        if not salida or "__error__" in salida:
            raise RuntimeError(f"el turno '{nombre}' ({tool}) no se pudo resolver: {salida}")
    return Demo(crudo, disponibilidad)
