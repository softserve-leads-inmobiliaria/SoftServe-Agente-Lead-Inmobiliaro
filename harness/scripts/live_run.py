"""Corrida real (OBS-6): los guiones de harness/fixtures contra el stack levantado, comprobados en los 3 servicios.

A diferencia de `run_all.py`, que solo mira las respuestas, esta corrida verifica el recorrido completo
sin nada simulado: el arnés le habla a `core` por HTTP, `core` consulta a `catalog` por MCP y guarda en
Postgres, y el resultado se lee de vuelta en Postgres (estado final de la FSM) y en el `panel` (la
cola del asesor de la zona del lead y su ficha con la transcripción).

Cada corrida usa un `run_id` propio, así que se puede repetir sobre la misma base: las conversaciones
de la corrida anterior, que pueden haber quedado en un estado terminal, no se tocan.

Uso (con el stack arriba: `docker compose up -d --build core panel` y `docker compose run --rm seed`):
    python harness/scripts/live_run.py
    python harness/scripts/live_run.py --sin-frases   # con LLM_PROVIDER=bedrock: no exige las frases fijas

Deja un informe en tmp/harness/<fecha>_corrida_real.md (tmp/ no se versiona). Sale con 0 si todo
quedó en verde.
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from expectations import EXPECTED_FINAL_STATE, accepted_final_states  # noqa: E402
from replay import (  # noqa: E402
    ROOT,
    RUN_ERRORS,
    build_events,
    describe,
    http_responder,
    load_script,
    replay,
)
from run_all import discover  # noqa: E402

sys.path.insert(0, str(ROOT))

REPORTS = ROOT / "tmp" / "harness"


@dataclass
class ScriptRun:
    name: str
    transcript: list[tuple[str, str]] = field(default_factory=list)
    final_state: str | None = None
    zone: str | None = None
    panel: str = "sin zona: no llega a ninguna cola"
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _get(url: str, timeout: float = 10.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8")


def check_health(core_url: str, panel_url: str) -> list[str]:
    """`/health` de core y del panel. El catálogo no se publica fuera de Docker: si está caído, core responde 503."""
    problems = []
    for name, url in (("core", core_url), ("panel", panel_url)):
        try:
            _get(url.rstrip("/") + "/health", timeout=5)
        except OSError as exc:
            problems.append(f"{name} no responde en {url}/health ({describe(exc)})")
    return problems


def _row_of(listing: str, lead_id: str) -> str | None:
    """La fila `<tr>` de la cola del panel que enlaza a la ficha del lead."""
    for row in listing.split("<tr>"):
        if f"/leads/{lead_id}" in row:
            return row
    return None


def check_panel(run: ScriptRun, panel_url: str, token: str, lead_id: str, expected_state: str | None) -> list[str]:
    """El lead aparece en la cola del asesor de su zona con su etapa, y su ficha muestra la transcripción."""
    base = f"{panel_url.rstrip('/')}/panel/{token}"
    row = _row_of(_get(base), lead_id)
    if row is None:
        return [f"panel: el lead no aparece en la cola del asesor de {run.zone}"]
    problems = []
    if expected_state and f"<td>{expected_state}</td>" not in row:
        problems.append(f"panel: la cola no muestra la etapa {expected_state} para el lead")
    card = html.unescape(_get(f"{base}/leads/{lead_id}"))
    missing = [text for text, _ in run.transcript if text and text not in card]
    if missing:
        problems.append(f"panel: la ficha no muestra {missing!r}")
    return problems


def run_script(path: Path, *, run_id: str, core_url: str, panel_url: str, engine: Any,
               check_replies: bool) -> ScriptRun:
    from sqlalchemy import text

    run = ScriptRun(path.stem)
    expected = EXPECTED_FINAL_STATE.get(run.name)
    if expected is None:
        run.failures.append("sin estado final declarado en harness/scripts/expectations.py")
    turns = load_script(path)
    try:
        replies = replay(path, http_responder(core_url), run_id=run_id, check_replies=check_replies)
    except RUN_ERRORS as exc:
        run.failures.append(describe(exc))
        return run
    run.transcript = [(turn["text"], reply["text"]) for turn, reply in zip(turns, replies, strict=True)]

    event = build_events(run.name, turns, run_id)[0]
    with engine.connect() as connection:
        run.final_state = connection.execute(text("SELECT fsm_state FROM conversations WHERE id = :c"),
                                             {"c": event["conversation_id"]}).scalar()
        run.zone = connection.execute(text("SELECT zone FROM leads WHERE id = :l"),
                                      {"l": event["lead_id"]}).scalar()
        token = connection.execute(text("SELECT access_token FROM advisors WHERE zone = :z ORDER BY id LIMIT 1"),
                                   {"z": run.zone}).scalar() if run.zone else None
    accepted = accepted_final_states(run.name, real_llm=not check_replies)
    if expected and run.final_state not in accepted:
        run.failures.append(f"estado final {run.final_state!r} en Postgres, se esperaba {expected!r}")
    elif expected:
        expected = run.final_state  # el panel tiene que mostrar el estado al que llegó, si es uno aceptado
    if run.zone and token is None:
        run.failures.append(f"no hay asesor para la zona {run.zone!r} (¿falta `docker compose run --rm seed`?)")
    elif run.zone:
        try:
            panel_problems = check_panel(run, panel_url, token, event["lead_id"], expected)
        except OSError as exc:
            panel_problems = [f"panel: {describe(exc)}"]
        run.failures += panel_problems
        run.panel = ("FALLA" if panel_problems else
                     f"en la cola de {run.zone} con etapa {run.final_state}; ficha con la transcripción")
    return run


def write_report(path: Path, runs: list[ScriptRun], *, run_id: str, core_url: str, panel_url: str,
                 check_replies: bool, problems: list[str]) -> None:
    ok = sum(run.ok for run in runs)
    lines = [
        f"# Corrida real del arnés — {run_id}",
        "",
        f"- core: `{core_url}` · panel: `{panel_url}`",
        f"- Frases fijas (`expect_reply_contains`): {'exigidas' if check_replies else 'no exigidas (--sin-frases)'}",
        f"- Resultado: **{ok}/{len(runs)} guiones en verde**",
        "",
    ]
    lines += [f"- **FALLA** {problem}" for problem in problems]
    if runs:
        lines += ["| Guion | Resultado | Estado final (Postgres) | Panel |", "| :--- | :--- | :--- | :--- |"]
        for run in runs:
            result = "OK" if run.ok else "FALLA: " + "; ".join(run.failures)
            lines.append(f"| `{run.name}` | {result} | {run.final_state or '—'} | {run.panel} |")
    for run in runs:
        lines += ["", f"## {run.name}", ""]
        for lead, bot in run.transcript:
            lines += [f"- **lead:** {lead or '(deep-link sin texto)'}", f"- **bot:** {bot}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    from sqlalchemy import create_engine

    from db.settings import database_url

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--core-url", default=os.environ.get("CORE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--panel-url", default=os.environ.get("PANEL_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--database-url", default=None, help="por defecto, la de db/settings.py")
    parser.add_argument("--sin-frases", action="store_true",
                        help="no exigir expect_reply_contains (para LLM_PROVIDER=bedrock)")
    parser.add_argument("--informe", type=Path, default=None, help="ruta del informe markdown")
    args = parser.parse_args(argv)

    run_id = f"{datetime.now():%Y-%m-%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    report = args.informe or REPORTS / f"{run_id}_corrida_real.md"
    check_replies = not args.sin_frases
    problems = check_health(args.core_url, args.panel_url)
    runs: list[ScriptRun] = []
    if not problems:
        engine = create_engine(args.database_url or database_url())
        try:
            runs = [run_script(path, run_id=run_id, core_url=args.core_url, panel_url=args.panel_url,
                               engine=engine, check_replies=check_replies) for path in discover()]
        finally:
            engine.dispose()
        if not any(run.zone for run in runs):
            problems.append("ningún lead quedó con zona: el panel no se comprobó")

    for run in runs:
        print(f"{'OK   ' if run.ok else 'FALLA'} {run.name}: {run.final_state or '—'}"
              + ("" if run.ok else " — " + "; ".join(run.failures)))
    for problem in problems:
        print(f"FALLA {problem}")
    write_report(report, runs, run_id=run_id, core_url=args.core_url, panel_url=args.panel_url,
                 check_replies=check_replies, problems=problems)
    ok = sum(run.ok for run in runs)
    print(f"{ok}/{len(runs)} guiones en verde contra los servicios reales · informe: {report}")
    return 0 if runs and ok == len(runs) and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
