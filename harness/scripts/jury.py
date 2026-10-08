"""Prueba de jurado (#247): conversaciones no pautadas contra el stack con el LLM real, para leer a mano.

La evaluación en vivo (`evaluate.py`) mide los escenarios conocidos con expectativas por turno. Esta prueba es lo
contrario: preguntas como las de un jurado que no vio el guion (amenidades que el catálogo no tiene, la agencia,
opiniones, frases desordenadas, cambios de idea). No tiene umbrales: deja la transcripción con lo que el
guardrail auditó en cada turno, y en los turnos donde una corrida anterior falló, qué hay que mirar (`WATCH`).
Debajo de cada respuesta va por qué contestó así (`turn.decision`): quién la escribió, la intención, los campos del
clasificador y los tipos de violación de la salida, sin valores.

Hay cuatro sets: J (7-oct, 6 conversaciones), N y M (sin ver hasta que se corrieron, 6 y 6) y O (10). Un set nuevo se agrega
con otra letra, en `jury_scenarios.py`, con lo que se espera de cada turno (`evaluate.py J N M O` lo mide); el criterio para cerrar #247 es que ninguno invente un dato y que se vean a lo sumo 1 o 2 errores.

Uso (con el stack arriba y LLM_PROVIDER=bedrock: `bash scripts/demo/up.sh`):
    python harness/scripts/jury.py M                     # un set (~15 min a 10 RPM)
    python harness/scripts/jury.py J N M                 # todos (~45 min)
    python harness/scripts/jury.py M4_preguntas_trampa   # una conversación

Deja tmp/jury/<fecha>_<sets>.md (tmp/ no se versiona).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate import DEFAULT_PAUSE_S, core_settings, send  # noqa: E402
from jury_scenarios import JURY  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REPORTS = ROOT / "tmp" / "jury"
DECISION = "turn.decision"  # la auditoría de por qué contestó así el turno (app/turn.py)

# Conversación -> (deep-link o None, mensajes). Un mensaje vacío es el `/start` del anuncio. Las conversaciones y lo
# que se espera de cada turno están en `jury_scenarios.py`; `evaluate.py J N M O` las mide.
CONVERSATIONS: dict[str, tuple[str | None, list[str]]] = {
    name: (scenario.property_id, [turn.text for turn in scenario.turns]) for name, scenario in JURY.items()}

# (conversación, turno desde 1) -> qué salió mal en una corrida anterior y qué lo arregló. Se imprime bajo el turno.
WATCH: dict[tuple[str, int], str] = {
    ("J1_detalles_fuera_del_catalogo", 3): "J r1: 'parking' nombrado como desconocido se bloqueaba como amenidad",
    ("J2_la_agencia", 5): "J r1: el número del lead ($150,000) se leía como dato inventado (#230)",
    ("J3_apartadas_y_cerradas", 2): "J r1: pedir la apartada ofrecía horarios de la otra casa (refers_to, DT-63)",
    ("J6_ambiguo_y_fuera_de_tema", 7): "J r1: 'the cheapest one' agendaba el primer resultado (DT-63)",
    ("N1_renta_desde_el_anuncio", 7): "N r1: 'this weekend' ofrecía jueves (visit_when, DT-64)",
    ("N6_escritura_desordenada", 4): "N r1: 'ok i wan to see it' agendaba otra propiedad (DT-63)",
    ("M1_compra_desde_el_anuncio", 5): "M r1: afirmó que las HOA fees no están incluidas, sin dato",
    ("M1_compra_desde_el_anuncio", 7): "M r1: 'next Tuesday afternoon' ofrecía jueves (DT-64)",
    ("M2_primer_comprador", 3): "M r1: mezcló presupuesto y cuota inicial",
    ("M3_agente_humano", 3): "M r1: tercera pregunta neutra -> traspaso por 'frustración' (DT-65)",
    ("M4_preguntas_trampa", 3): "M r1: 'no houses for rent in Usaquen' con una en venta (DT-57, operación)",
    ("M4_preguntas_trampa", 6): "M r1: degradada por una referencia inventada (DT-63, nota al modelo)",
    ("M5_cambio_de_idea", 2): "M r1: degradada por falso positivo de dirección en la ficha (A-6)",
    ("M5_cambio_de_idea", 6): "M r2: pide confirmar la propiedad aunque venía hablando de la prop_00231",
    ("M5_cambio_de_idea", 7): "M r1: 'another day' -> 'I didn't quite get which time' (DT-64)",
    ("M6_mensajes_cortos_y_raros", 3): "M r2: no busca terrenos; pide operación y ciudad tres turnos seguidos",
    ("O2_pareja_mayor_accesibilidad", 2): "O r1: 'which property?' tres veces en vez de decir que no hay dato (DT-66)",
    ("O3_estudiante_con_poco_presupuesto", 3): "O r1: 'Yes, absolutely!' a compartir: política inventada (DT-66)",
    ("O3_estudiante_con_poco_presupuesto", 5): "O r1: degradada tras 4 turnos sin buscar (pendiente)",
    ("O5_comparador", 2): "O r1: precio por m² degradado (DT-60, price_per_m2)",
    ("O6_anuncio_de_una_apartada", 3): "O r1: '¿cuándo se libera?' degradado por 'available' futuro (A-5)",
    ("O6_anuncio_de_una_apartada", 4): "O r1: ofreció horarios de la misma casa apartada (DT-63, estado)",
    ("O6_anuncio_de_una_apartada", 6): "O r1: 'afternoon' saltó al lunes siguiente (DT-64)",
    ("O9_visita_con_restricciones", 3): "O r1: 'after 5pm' ofreció el jueves siguiente (DT-64)",
    ("O9_visita_con_restricciones", 7): "O r1: 'bring my wife' -> respuesta fija de fair housing (pendiente)",
}


def _audit_rows(connection: Any, conversation_id: str) -> list[tuple[str, dict[str, Any]]]:
    from sqlalchemy import text

    return [(action, payload or {}) for action, payload in connection.execute(
        text("SELECT action, payload FROM audit_log WHERE payload->>'conversation_id' = :c ORDER BY id"),
        {"c": conversation_id})]


def _why(rows: list[tuple[str, dict[str, Any]]]) -> tuple[list[str], str | None]:
    """Las auditorías de un turno como se leen en la transcripción: las acciones (con los tipos de violación de la
    salida) y la línea de `turn.decision`, si la hay."""
    actions, why = [], None
    for action, payload in rows:
        if action == DECISION:
            route, agent = payload.get("route", "?"), payload.get("agent_route")
            route += f" (agente: {agent})" if agent and agent != route else ""
            intent, classified = payload.get("intent", "?"), payload.get("classified")
            intent += f" (clasificador: {classified})" if classified and classified != intent else ""
            parts = [f"ruta {route}", f"intención {intent}"]
            flags = payload.get("flags") or {}
            parts += [f"{'' if value else 'no '}{name}" for name, value in sorted(flags.items()) if value is not None]
            if payload.get("refers_to"):
                parts.append(f"habla de {payload['refers_to']}")
            if payload.get("unvisitable"):
                parts.append(f"no se puede visitar {payload['unvisitable']}")
            if payload.get("visit_when"):
                parts.append(f"cuándo {payload['visit_when']}")
            why = " · ".join(parts)
        else:
            kinds = payload.get("kinds")
            actions.append(f"{action}[{','.join(kinds)}]" if kinds else action)
    return actions, why


def _names(selected: list[str]) -> list[str]:
    """Las conversaciones de los sets (`M`) o nombres (`M4_preguntas_trampa`) pedidos; todas si no se pide nada."""
    if not selected:
        return list(CONVERSATIONS)
    names: list[str] = []
    for item in selected:
        is_set = len(item) == 1
        found = [name for name in CONVERSATIONS if (name[0] == item.upper() if is_set else name == item)]
        if not found:
            raise SystemExit(f"desconocido: {item}; sets J, N, M o una de {list(CONVERSATIONS)}")
        names += [name for name in found if name not in names]
    return names


def main(argv: list[str] | None = None) -> int:
    from sqlalchemy import create_engine

    from db.settings import database_url

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("conversaciones", nargs="*", help="sets (J, N, M) o nombres; por defecto, todas")
    parser.add_argument("--pausa", type=float, default=DEFAULT_PAUSE_S, help="segundos entre turnos")
    parser.add_argument("--timeout", type=float, default=120.0, help="segundos de espera por turno")
    parser.add_argument("--core-url", default=os.environ.get("CORE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--database-url", default=None, help="por defecto, la de db/settings.py")
    args = parser.parse_args(argv)
    names = _names(args.conversaciones)
    try:
        urllib.request.urlopen(args.core_url.rstrip("/") + "/health", timeout=5).close()
    except OSError as exc:
        print(f"core no responde en {args.core_url}/health ({exc}): levanta el stack con bash scripts/demo/up.sh")
        return 2
    settings = core_settings()

    started = datetime.now()
    sets = "".join(sorted({name[0] for name in names}))
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"{started:%Y-%m-%d_%H%M%S}_{sets}.md"
    lines = [f"# Prueba de jurado — {started:%Y-%m-%d %H:%M}", "",
             f"- Conversación: `{settings.get('BEDROCK_MODEL_ID_CHAT', '?')}` · intención: "
             f"`{settings.get('BEDROCK_MODEL_ID_CLASSIFIER', '?')}` · pausa entre turnos {args.pausa:.0f}s", ""]
    engine = create_engine(args.database_url or database_url())
    try:
        for name in names:
            property_id, turns = CONVERSATIONS[name]
            conversation_id, lead_id = str(uuid.uuid4()), str(uuid.uuid4())
            lines += [f"## {name}" + (f" (anuncio {property_id})" if property_id else ""), ""]
            print(name, flush=True)
            seen = 0
            for i, message in enumerate(turns, start=1):
                reply, latency, error = send(args.core_url, conversation_id, lead_id, message,
                                             property_id if i == 1 else None, args.timeout)
                with engine.connect() as connection:
                    audits = _audit_rows(connection, conversation_id)
                (new, why), seen = _why(audits[seen:]), len(audits)
                print(f"  {i}: {latency:.1f}s {new} {why or ''}" + (f" ERROR {error}" if error else ""), flush=True)
                lines += [f"**{i}. Lead:** {message or '(/start)'}", "",
                          f"**Agente** ({latency:.1f}s{', ' + ', '.join(new) if new else ''}):", "",
                          *[f"> {row}" for row in (reply or f"(sin respuesta: {error})").splitlines()], ""]
                if why:
                    lines += [f"*Por qué:* {why}", ""]
                if (name, i) in WATCH:
                    lines += [f"*Mirar:* {WATCH[(name, i)]}", ""]
                out.write_text("\n".join(lines), encoding="utf-8")
                if message:
                    time.sleep(args.pausa)
    finally:
        engine.dispose()
    print(f"Transcripción: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
