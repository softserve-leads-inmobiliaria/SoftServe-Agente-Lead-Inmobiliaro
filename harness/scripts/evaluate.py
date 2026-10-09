"""Evaluación en vivo repetible (#216): los escenarios de `scenarios.py` N veces contra el stack con el LLM real.

Cada turno va por `POST /events`, igual que Telegram. Después de cada turno se lee en Postgres qué tools
llamó el modelo (`messages.tool_calls`) y qué dejó en `audit_log` (regeneraciones y degradaciones de la
factualidad, guardrails de entrada); al final, el estado de la FSM y la banda del score. Las métricas y
los umbrales están en `evaluation.py`.

Uso (con el stack arriba y LLM_PROVIDER=bedrock: `bash scripts/demo/up.sh`):
    python harness/scripts/evaluate.py                      # todos los escenarios, una vez
    python harness/scripts/evaluate.py --reps 5             # la corrida de referencia
    python harness/scripts/evaluate.py --reps 3 08_cobertura_de_zonas
    python harness/scripts/evaluate.py O                    # un set de la prueba de jurado (`jury_scenarios.py`)
    python harness/scripts/evaluate.py J N M O              # los cuatro (~1 h a 10 RPM)

Deja tmp/eval/<fecha>_evaluacion.md y .json (tmp/ no se versiona). Sale con 0 si los totales quedan dentro
de los umbrales.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluation import (  # noqa: E402
    ScenarioRun,
    TurnObservation,
    as_json,
    breaches,
    render_report,
    summarize,
)
from jury_scenarios import JURY, jury_set  # noqa: E402
from scenarios import SCENARIOS, Scenario  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REPORTS = ROOT / "tmp" / "eval"
# El core limita a 10 llamadas por minuto y por modelo (DT-6) y un turno con tools hace 3 o 4: con esta
# pausa (la misma de conversar.py) la cola del limitador no llega a vencer.
DEFAULT_PAUSE_S = 20.0
# Los escenarios de la demo y los de la prueba de jurado (#247). Sin nombres, se corren solo los de la demo.
ALL: dict[str, Scenario] = {**SCENARIOS, **JURY}
DECISION = "turn.decision"  # por qué contestó así el turno (app/turn.py)


def send(core_url: str, conversation_id: str, lead_id: str, text: str, property_id: str | None,
         timeout: float) -> tuple[str, float, str | None]:
    event: dict[str, Any] = {
        "schema_version": 1, "event_id": str(uuid.uuid4()), "conversation_id": conversation_id,
        "lead_id": lead_id, "channel": "telegram", "direction": "inbound", "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if property_id:
        event["property_id"] = property_id
    request = urllib.request.Request(core_url.rstrip("/") + "/events", data=json.dumps(event).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
        return body.get("text", ""), time.monotonic() - started, None
    except Exception as exc:  # noqa: BLE001 — la corrida sigue y el turno cuenta como fallido
        return "", time.monotonic() - started, f"{type(exc).__name__}: {exc}"


def _audits(connection: Any, conversation_id: str) -> list[str]:
    from sqlalchemy import text

    # Sin `turn.decision` (actor `turn`): no es una detección del guardrail, la lee `jury.py`.
    return list(connection.execute(text("SELECT action FROM audit_log WHERE payload->>'conversation_id' = :c "
                                        "AND actor <> 'turn' ORDER BY id"), {"c": conversation_id}).scalars())


def _decisions(connection: Any, conversation_id: str) -> list[dict[str, Any]]:
    from sqlalchemy import text

    return list(connection.execute(text("SELECT payload FROM audit_log WHERE payload->>'conversation_id' = :c "
                                        "AND action = :a AND actor = 'turn' ORDER BY id"),
                                   {"c": conversation_id, "a": DECISION}).scalars())


def names_for(selected: list[str]) -> list[str]:
    """Los escenarios pedidos: nombres, o la letra de un set de la prueba de jurado (`O`). Sin nada, los de la demo."""
    if not selected:
        return list(SCENARIOS)
    names: list[str] = []
    for item in selected:
        found = jury_set(item) if len(item) == 1 else [item] if item in ALL else []
        if not found:
            raise SystemExit(f"desconocido: {item}; sets J, N, M, O o uno de {list(ALL)}")
        names += [name for name in found if name not in names]
    return names


def _assistant_tool_calls(connection: Any, conversation_id: str) -> list[list[dict[str, Any]] | None]:
    from sqlalchemy import text

    return list(connection.execute(text("SELECT tool_calls FROM messages WHERE conversation_id = :c "
                                        "AND role = 'assistant' ORDER BY created_at, id"),
                                   {"c": conversation_id}).scalars())


def run_scenario(name: str, scenario: Scenario, rep: int, *, core_url: str, engine: Any, pause_s: float,
                 timeout: float) -> ScenarioRun:
    from sqlalchemy import text

    conversation_id, lead_id = str(uuid.uuid4()), str(uuid.uuid4())
    run = ScenarioRun(name, rep, conversation_id=conversation_id)
    seen_audits = seen_replies = seen_decisions = 0
    for i, turn in enumerate(scenario.turns):
        reply, latency, error = send(core_url, conversation_id, lead_id, turn.text,
                                     scenario.property_id if i == 0 else None, timeout)
        with engine.connect() as connection:
            audits = _audits(connection, conversation_id)
            replies = _assistant_tool_calls(connection, conversation_id)
            decisions = _decisions(connection, conversation_id)
        tools = [call["name"] for call in (replies[-1] or [])] if len(replies) > seen_replies else []
        new_audits = audits[seen_audits:]
        decision = decisions[-1] if len(decisions) > seen_decisions else None
        run.turns.append(TurnObservation(turn.text, reply, tools, new_audits, round(latency, 2), error, decision))
        seen_audits, seen_replies, seen_decisions = len(audits), len(replies), len(decisions)
        print(f"  turno {i + 1}: {latency:.1f}s tools={tools} audits={new_audits} "
              f"ruta={(decision or {}).get('route')}"
              + (f" ERROR {error}" if error else ""), flush=True)
        if turn.text:  # el deep-link sin texto no llama al LLM
            time.sleep(pause_s)
    with engine.connect() as connection:
        run.final_state = connection.execute(text("SELECT fsm_state FROM conversations WHERE id = :c"),
                                             {"c": conversation_id}).scalar()
        run.band = connection.execute(text("SELECT band FROM lead_scores WHERE lead_id = :l"),
                                      {"l": lead_id}).scalar()
    return run


def core_settings() -> dict[str, str]:
    """Proveedor y modelos con que corre el core, para el encabezado del informe."""
    names = ("LLM_PROVIDER", "BEDROCK_MODEL_ID_CHAT", "BEDROCK_MODEL_ID_CLASSIFIER")
    try:
        out = subprocess.run(["docker", "compose", "exec", "-T", "core", "sh", "-c",
                              "; ".join(f'echo "{n}=${n}"' for n in names)],
                             capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)


def write_report(base: Path, runs: list[ScenarioRun], scenarios: dict[str, Scenario], settings: dict[str, str],
                 args: argparse.Namespace, started: datetime, planned: int) -> None:
    """El informe (.md) y los datos (.json) de lo corrido hasta ahora. Si faltan conversaciones, lo dice."""
    minutes = (datetime.now() - started).total_seconds() / 60
    header = [
        f"# Evaluación en vivo — {started:%Y-%m-%d %H:%M}",
        "",
        f"- Proveedor: `{settings.get('LLM_PROVIDER', '?')}` · conversación: `{settings.get('BEDROCK_MODEL_ID_CHAT', '?')}`"
        f" · intención: `{settings.get('BEDROCK_MODEL_ID_CLASSIFIER', '?')}`",
        f"- {args.reps} corrida(s) de {len(scenarios)} escenario(s), {minutes:.0f} min · pausa entre turnos {args.pausa:.0f}s",
    ]
    if len(runs) < planned:
        header.append(f"- **Incompleta:** {len(runs)} de {planned} conversaciones.")
    base.with_suffix(".md").write_text(render_report(runs, scenarios, header), encoding="utf-8")
    payload = {"started": started.isoformat(), "settings": settings, "reps": args.reps,
               "planned_conversations": planned, "complete": len(runs) == planned, **as_json(runs, scenarios)}
    base.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    from sqlalchemy import create_engine

    from db.settings import database_url

    # La consola de Windows (cp1252) no imprime "≤" ni "Ó": sin esto, la corrida se caía al final.
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("escenarios", nargs="*", help="nombres o sets de jurado (J, N, M, O); por defecto, los de la demo")
    parser.add_argument("--reps", type=int, default=1, help="corridas de cada escenario")
    parser.add_argument("--pausa", type=float, default=DEFAULT_PAUSE_S, help="segundos entre turnos")
    parser.add_argument("--timeout", type=float, default=120.0, help="segundos de espera por turno")
    parser.add_argument("--core-url", default=os.environ.get("CORE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--database-url", default=None, help="por defecto, la de db/settings.py")
    args = parser.parse_args(argv)

    names = names_for(args.escenarios)
    try:
        urllib.request.urlopen(args.core_url.rstrip("/") + "/health", timeout=5).close()
    except OSError as exc:
        print(f"core no responde en {args.core_url}/health ({exc}): levanta el stack con bash scripts/demo/up.sh")
        return 2
    settings = core_settings()
    if settings.get("LLM_PROVIDER") not in (None, "bedrock"):
        print(f"AVISO: el core corre con LLM_PROVIDER={settings['LLM_PROVIDER']}, no con bedrock")

    started = datetime.now()
    engine = create_engine(args.database_url or database_url())
    runs: list[ScenarioRun] = []
    REPORTS.mkdir(parents=True, exist_ok=True)
    base = REPORTS / f"{started:%Y-%m-%d_%H%M%S}_evaluacion"
    scenarios = {name: ALL[name] for name in names}
    planned = args.reps * len(names)
    complete = False
    try:
        for rep in range(1, args.reps + 1):
            for name in names:
                print(f"{name} #{rep}", flush=True)
                runs.append(run_scenario(name, ALL[name], rep, core_url=args.core_url, engine=engine,
                                         pause_s=args.pausa, timeout=args.timeout))
                # Se reescribe después de cada conversación: si la corrida se corta (Ctrl+C, el equipo se
                # suspende, se cae el core), lo medido hasta ahí queda en disco, marcado como incompleto.
                write_report(base, runs, scenarios, settings, args, started, planned)
        complete = True
    finally:
        engine.dispose()
        if runs and not complete:
            write_report(base, runs, scenarios, settings, args, started, planned)
            print(f"\nCORRIDA INCOMPLETA: {len(runs)} de {planned} conversaciones. Informe: {base.with_suffix('.md')}")

    total = summarize(runs, scenarios)
    problems = breaches(total)
    print(f"\nTurnos OK {total['turns_passed']}/{total['turns']} · regeneradas {total['regenerated']} · "
          f"degradadas {total['degraded']} · respaldo {total['fallback']} · "
          f"estado final {total['finals_passed']}/{total['finals']}")
    for problem in problems:
        print(f"FUERA DE UMBRAL {problem}")
    print(f"Informe: {base.with_suffix('.md')}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
