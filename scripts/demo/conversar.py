"""Conversaciones de ensayo contra core (POST /events) con el LLM real, guardando cada transcripción.

Uso:  python scripts/demo/conversar.py [nombre_escenario ...]   (con el stack arriba: bash scripts/demo/up.sh)
Cada escenario deja tmp/demo/transcripciones/<fecha>_<escenario>.txt con los turnos y el estado final
(FSM, score, zona, asesor) leído de Postgres. Los escenarios están en `harness/scripts/scenarios.py`;
para medirlos con varias corridas y umbrales, `harness/scripts/evaluate.py` (#216).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

CORE = "http://localhost:8000/events"
PAUSA_S = 20  # cupo local de 10 llamadas por minuto y modelo; un turno con herramientas hace 3 o 4
REPO = Path(__file__).resolve().parents[2]
SALIDA = REPO / "tmp" / "demo" / "transcripciones"  # tmp/ no se versiona

sys.path.insert(0, str(REPO / "harness" / "scripts"))
# Los mismos escenarios que la evaluación en vivo (#216).
from scenarios import SCENARIOS  # noqa: E402

ESCENARIOS: dict[str, dict] = {
    name: {"descripcion": s.description, "property_id": s.property_id, "turnos": [t.text for t in s.turns]}
    for name, s in SCENARIOS.items()
}


def enviar(conv: uuid.UUID, lead: uuid.UUID, texto: str, property_id: str | None) -> tuple[str, float]:
    evento = {
        "schema_version": 1, "event_id": str(uuid.uuid4()), "conversation_id": str(conv),
        "lead_id": str(lead), "channel": "telegram", "direction": "inbound", "text": texto,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if property_id:
        evento["property_id"] = property_id
    req = urllib.request.Request(CORE, data=json.dumps(evento).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    inicio = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            cuerpo = json.loads(resp.read())
        return cuerpo.get("text", json.dumps(cuerpo, ensure_ascii=False)), time.monotonic() - inicio
    except Exception as exc:  # noqa: BLE001 — el ensayo debe seguir y dejar constancia del fallo
        return f"[ERROR] {exc}", time.monotonic() - inicio


def estado(conv: uuid.UUID, lead: uuid.UUID) -> str:
    sql = (f"select fsm_state from conversations where id='{conv}';"
           f"select status, zone, advisor_id, qualified_at is not null as calificado from leads where id='{lead}';"
           f"select need_fit, budget, authority, timing, eligibility, total, band from lead_scores where lead_id='{lead}';")
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "leads", "-d", "leads",
                          "-c", sql], capture_output=True, text=True, encoding="utf-8", cwd=REPO)
    return out.stdout or out.stderr


def correr(nombre: str) -> Path:
    esc = ESCENARIOS[nombre]
    conv, lead = uuid.uuid4(), uuid.uuid4()
    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = SALIDA / f"{datetime.now():%Y-%m-%d_%H%M}_{nombre}.txt"
    with ruta.open("w", encoding="utf-8") as f:
        def w(linea: str = "") -> None:
            f.write(linea + "\n")
            f.flush()
            print(linea)
        w(f"Escenario: {nombre} — {esc['descripcion']}")
        w(f"Fecha: {datetime.now():%Y-%m-%d %H:%M}   Canal: POST /events (mismo turno que Telegram)")
        w(f"conversation_id={conv}  lead_id={lead}  property_id={esc['property_id']}")
        w("=" * 80)
        for i, texto in enumerate(esc["turnos"], 1):
            respuesta, dur = enviar(conv, lead, texto, esc["property_id"] if i == 1 else None)
            w(f"\n[{i}] LEAD:   {texto or '/start ' + str(esc['property_id'])}")
            w(f"[{i}] AGENTE ({dur:.1f}s): {respuesta}")
            time.sleep(PAUSA_S)
        w("\n" + "=" * 80)
        w("Estado final en Postgres:")
        w(estado(conv, lead))
    return ruta


if __name__ == "__main__":
    for n in sys.argv[1:] or list(ESCENARIOS):
        print(f"\n>>> {correr(n)}\n")
