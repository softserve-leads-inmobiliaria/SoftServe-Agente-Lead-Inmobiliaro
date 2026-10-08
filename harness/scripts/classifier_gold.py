"""Gold set del clasificador (#247, paso 2 del plan de confiabilidad): cada campo, medido contra mensajes etiquetados.

El clasificador decide lo que antes se adivinaba sobre el texto (DT-62): de qué propiedad habla el mensaje, si
quiere visitar y cuándo, si pide una persona, si está molesto, si pregunta algo que el catálogo no registra. Un
cambio a su prompt (`CLASSIFIER_SYSTEM`) se acepta solo si ningún campo baja de la línea base.

Cada línea de `harness/gold/classifier.jsonl` es un mensaje con su contexto:

- `text`; `last`: la última respuesta del asistente; `shown`: las propiedades ya mostradas (ids del catálogo);
- `intent`, `refers_to`, `visit_when`: se miden solo si están (`visit_when: null` es "no dijo cuándo");
- `flags`: las que tienen que venir en true; el resto de `CLASSIFIER_FLAGS` se espera en false, salvo las de
  `unsure`, que no se miden.

Uso (con las credenciales de Bedrock en `.env`, sin otra corrida en vivo al mismo tiempo: 10 RPM, DT-6):
    python harness/scripts/classifier_gold.py               # mide y compara con la línea base
    python harness/scripts/classifier_gold.py --guardar     # además, deja esta corrida como línea base
    python harness/scripts/classifier_gold.py O6-5 O9-3     # solo esos mensajes (no compara)

Deja tmp/gold/<fecha>_clasificador.md y .json. Sale con 1 si algún campo bajó, y con 2 si el LLM no contestó alguno.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "harness" / "gold" / "classifier.jsonl"
BASELINE = ROOT / "harness" / "gold" / "classifier_baseline.json"
REPORTS = ROOT / "tmp" / "gold"

# Los de `app.bedrock_llm.CLASSIFIER_FLAGS` (un test comprueba que sigan iguales).
FLAGS = ("asks_for_person", "wants_alert", "wants_visit", "frustrated", "asks_unlisted")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # `date.weekday()`, como `visits.visit_when`
# 10 llamadas por minuto (DT-6): una cada 6,5 s no hace esperar al limitador del cliente.
PAUSE_S = 6.5


def load(path: Path = GOLD) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def when(label: dict[str, Any] | None) -> dict[str, Any] | None:
    """`visit_when` como lo deja `visits.visit_when`: los días como números, sin claves vacías."""
    if not label:
        return None
    return {"days": sorted(DAYS.index(day) for day in label.get("days", [])), "part": label.get("part_of_day"),
            "other_day": label.get("other_day") is True}


def expected(item: dict[str, Any]) -> dict[str, Any]:
    """Campo -> valor esperado, solo los campos que el mensaje mide."""
    out: dict[str, Any] = {}
    if "intent" in item:
        out["intent"] = item["intent"]
    for flag in FLAGS:
        if flag not in item.get("unsure", []):
            out[flag] = flag in item.get("flags", [])
    if "refers_to" in item:
        out["refers_to"] = item["refers_to"]
    if "visit_when" in item:
        out["visit_when"] = when(item["visit_when"])
    return out


def observed(classification: Any) -> dict[str, Any]:
    """Lo que contestó el clasificador (`app.bedrock_llm.Classification`). Una bandera que no vino es None."""
    return {"intent": classification.intent, **{flag: classification.flags.get(flag) for flag in FLAGS},
            "refers_to": classification.refers_to, "visit_when": classification.when}


def score(items: list[dict[str, Any]], answers: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Aciertos por campo y cada error. Un mensaje sin respuesta (error del LLM) cuenta como error en todo."""
    fields: dict[str, dict[str, int]] = {}
    misses = []
    for item in items:
        got = answers.get(item["id"])
        for field, want in expected(item).items():
            counts = fields.setdefault(field, {"right": 0, "total": 0})
            counts["total"] += 1
            value = None if got is None else got.get(field)
            if got is not None and value == want:
                counts["right"] += 1
            else:
                misses.append({"id": item["id"], "text": item["text"], "field": field, "want": want,
                               "got": "sin respuesta" if got is None else value})
    accuracy = {field: c["right"] / c["total"] for field, c in sorted(fields.items())}
    return {"messages": len(items), "fields": dict(sorted(fields.items())), "accuracy": accuracy, "misses": misses}


def regressions(result: dict[str, Any], baseline: dict[str, float]) -> list[str]:
    """Los campos que bajaron respecto de la línea base."""
    return [f"{field}: {result['accuracy'].get(field, 0):.0%} (línea base {base:.0%})"
            for field, base in sorted(baseline.items()) if result["accuracy"].get(field, 0) < base]


def render(result: dict[str, Any], header: list[str], problems: list[str]) -> str:
    lines = [*header, "", "| Campo | Aciertos | Exactitud |", "| :--- | ---: | ---: |"]
    for field, counts in result["fields"].items():
        lines.append(f"| `{field}` | {counts['right']}/{counts['total']} | {result['accuracy'][field]:.0%} |")
    lines += ["", "**Contra la línea base: " + ("ningún campo bajó**" if not problems else "BAJÓ**")]
    lines += [f"- {problem}" for problem in problems]
    lines += ["", "## Errores", ""]
    lines += [f"- `{m['id']}` {m['field']}: esperaba `{m['want']}`, vino `{m['got']}` — {m['text']}"
              for m in result["misses"]] or ["Ninguno."]
    return "\n".join(lines) + "\n"


def _env() -> dict[str, str]:
    """El entorno con lo de `.env` debajo: una variable ya exportada gana."""
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                env[key.strip()] = value.strip().strip('"').strip("'")
    return {**env, **os.environ}


async def classify_all(items: list[dict[str, Any]], env: dict[str, str]) -> dict[str, dict[str, Any] | None]:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "services" / "core"))
    from app.bedrock_llm import BedrockLLM, LLMError
    from app.llm_config import _bedrock_client, _retry_policy
    from app.llm_retry import Retrier
    from app.rate_limit import RateLimits
    from services.catalog.app.data import PROPERTIES

    # boto3 lee las credenciales del entorno del proceso (o de ~/.aws), no de `env`: sin esto usaba otras.
    os.environ.update({key: value for key, value in env.items() if key.startswith("AWS_")})
    catalog = {prop["id"]: prop for prop in PROPERTIES}
    llm = BedrockLLM(_bedrock_client(env), env["BEDROCK_MODEL_ID_CHAT"], env["BEDROCK_MODEL_ID_CLASSIFIER"],
                     RateLimits(10, 60.0), max_wait=60.0, retrier=Retrier(_retry_policy(env)))
    answers: dict[str, dict[str, Any] | None] = {}
    for i, item in enumerate(items, start=1):
        started = time.monotonic()
        try:
            got = await llm.classify(item["text"], item.get("last"), [catalog[p] for p in item.get("shown", [])])
            answers[item["id"]] = observed(got)
        except LLMError as exc:
            answers[item["id"]] = None
            print(f"  {item['id']}: ERROR {exc}", flush=True)
        print(f"  {i}/{len(items)} {item['id']}", flush=True)
        await asyncio.sleep(max(0.0, PAUSE_S - (time.monotonic() - started)))
    return answers


def main(argv: list[str] | None = None) -> int:
    # La consola de Windows (cp1252) no imprime "≤" ni "Ó": sin esto, la corrida se caía al final.
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ids", nargs="*", help="solo estos mensajes; por defecto, todos")
    parser.add_argument("--guardar", action="store_true", help="dejar esta corrida como línea base")
    args = parser.parse_args(argv)
    items = load()
    if args.ids:
        unknown = set(args.ids) - {item["id"] for item in items}
        if unknown:
            parser.error(f"desconocidos: {sorted(unknown)}")
        items = [item for item in items if item["id"] in args.ids]
    env = _env()
    missing = [k for k in ("BEDROCK_MODEL_ID_CHAT", "BEDROCK_MODEL_ID_CLASSIFIER") if not env.get(k)]
    if missing:
        print(f"faltan {missing} (en .env o en el entorno)")
        return 2

    started = datetime.now()
    answers = asyncio.run(classify_all(items, env))
    failed = sum(answer is None for answer in answers.values())
    result = score(items, answers)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() and not args.ids else {}
    problems = regressions(result, baseline)
    header = [f"# Gold set del clasificador — {started:%Y-%m-%d %H:%M}", "",
              f"- Clasificador: `{env['BEDROCK_MODEL_ID_CLASSIFIER']}` · {result['messages']} mensajes",
              "- Línea base: " + ("ninguna" if not baseline else f"`{BASELINE.relative_to(ROOT).as_posix()}`")]
    REPORTS.mkdir(parents=True, exist_ok=True)
    base = REPORTS / f"{started:%Y-%m-%d_%H%M%S}_clasificador"
    base.with_suffix(".md").write_text(render(result, header, problems), encoding="utf-8")
    base.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                                         encoding="utf-8")
    if failed:  # un error del LLM no es un error del clasificador: la corrida no vale como medida
        print(f"{failed} mensaje(s) sin respuesta del LLM: la corrida no se compara ni se guarda")
        return 2
    if args.guardar and not args.ids:
        BASELINE.write_text(json.dumps(result["accuracy"], indent=2) + "\n", encoding="utf-8")
    for field, accuracy in result["accuracy"].items():
        print(f"{field:16} {accuracy:.0%}")
    for problem in problems:
        print(f"BAJÓ {problem}")
    print(f"Informe: {base.with_suffix('.md')}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
