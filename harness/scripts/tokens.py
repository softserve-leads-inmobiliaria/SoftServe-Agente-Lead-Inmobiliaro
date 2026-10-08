"""Tokens y llamadas al LLM de una evaluación en vivo, leídos de Phoenix (#52).

`evaluate.py` guarda lo que pasó en cada turno, pero los tokens solo quedan en las trazas de Phoenix, que
viven en su volumen: `scripts/demo/down.sh` las borra. Este script las cruza con las conversaciones de un
informe y deja el resultado junto a él, en el host.

Uso (con Phoenix arriba, antes de apagar el stack):
    python harness/scripts/tokens.py tmp/eval/2026-10-08_213000_evaluacion.json

Deja <informe>_tokens.json y <informe>_tokens.md. Solo cuenta: no lee ni guarda el contenido de los
mensajes (con `TRACE_CONTENT` vacío, Phoenix tampoco lo tiene).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

PHOENIX = "http://localhost:6006"
PROJECT = "lead-agent"
LLM_SPAN = "bedrock.converse"
TOKENS = ("llm.token_count.prompt", "llm.token_count.completion", "llm.token_count.total")


def spans(phoenix: str, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
    """Todos los spans del proyecto entre `start` y `end`, página por página."""
    cursor = None
    while True:
        query = {"limit": 1000, "start_time": start.isoformat(), "end_time": end.isoformat()}
        if cursor:
            query["cursor"] = cursor
        url = f"{phoenix}/v1/projects/{PROJECT}/spans?{urllib.parse.urlencode(query)}"
        with urllib.request.urlopen(url, timeout=60) as response:
            page = json.loads(response.read())
        yield from page.get("data", [])
        cursor = page.get("next_cursor")
        if not cursor:
            return


def count(report: dict[str, Any], all_spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Llamadas y tokens por conversación del informe, por modelo, y por turno con texto."""
    wanted = {run["conversation_id"]: run for run in report["runs"]}
    conversation_of_trace = {}
    for span in all_spans:
        conversation = span.get("attributes", {}).get("conversation.id")
        if span["name"] == "turn" and conversation in wanted:
            conversation_of_trace[span["context"]["trace_id"]] = conversation

    per_conversation: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, **{k: 0 for k in TOKENS}})
    per_model: dict[str, dict[str, Any]] = defaultdict(lambda: {"calls": 0, **{k: 0 for k in TOKENS}})
    for span in all_spans:
        conversation = conversation_of_trace.get(span["context"]["trace_id"])
        if span["name"] != LLM_SPAN or conversation is None:
            continue
        attributes = span.get("attributes", {})
        model = str(attributes.get("llm.model_name", "?"))
        for bucket in (per_conversation[conversation], per_model[model]):
            bucket["calls"] += 1
            for key in TOKENS:
                bucket[key] += int(attributes.get(key) or 0)

    rows = []
    for conversation, run in wanted.items():
        turns = sum(1 for turn in run["turns"] if turn["text"])
        found = per_conversation.get(conversation, {"calls": 0, **{k: 0 for k in TOKENS}})
        rows.append({"scenario": run["scenario"], "rep": run["rep"], "conversation_id": conversation,
                     "turns": turns, **found})
    with_calls = [row for row in rows if row["calls"]]
    per_turn_calls = [row["calls"] / row["turns"] for row in with_calls if row["turns"]]
    per_turn_tokens = [row["llm.token_count.total"] / row["turns"] for row in with_calls if row["turns"]]
    return {
        "report_started": report["started"],
        "conversations": len(rows),
        "conversations_with_traces": len(with_calls),
        "turns": sum(row["turns"] for row in rows),
        "calls": sum(row["calls"] for row in rows),
        "tokens": {key: sum(row[key] for row in rows) for key in TOKENS},
        "per_turn": {
            "calls_mean": round(statistics.mean(per_turn_calls), 2) if per_turn_calls else None,
            "tokens_mean": round(statistics.mean(per_turn_tokens)) if per_turn_tokens else None,
        },
        "per_model": dict(per_model),
        "per_conversation": rows,
    }


def render(summary: dict[str, Any]) -> str:
    tokens = summary["tokens"]
    lines = [
        f"# Tokens de la evaluación del {summary['report_started'][:16].replace('T', ' ')}",
        "",
        f"- Conversaciones con trazas: {summary['conversations_with_traces']} de {summary['conversations']}",
        f"- Turnos con texto: {summary['turns']} · llamadas al LLM: {summary['calls']}"
        f" ({summary['per_turn']['calls_mean']} por turno)",
        f"- Tokens: {tokens['llm.token_count.total']:,} ({tokens['llm.token_count.prompt']:,} de entrada,"
        f" {tokens['llm.token_count.completion']:,} de salida) · {summary['per_turn']['tokens_mean']} por turno",
        "",
        "| Modelo | Llamadas | Tokens de entrada | Tokens de salida |",
        "| :--- | ---: | ---: | ---: |",
    ]
    for model, bucket in sorted(summary["per_model"].items()):
        lines.append(f"| `{model}` | {bucket['calls']:,} | {bucket['llm.token_count.prompt']:,} |"
                     f" {bucket['llm.token_count.completion']:,} |")
    if summary["conversations_with_traces"] < summary["conversations"]:
        lines += ["", "Faltan trazas de algunas conversaciones: ¿se reinició Phoenix o se borró su volumen?"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("informe", type=Path, help="el .json que dejó evaluate.py")
    parser.add_argument("--phoenix", default=PHOENIX)
    args = parser.parse_args(argv)

    report = json.loads(args.informe.read_text(encoding="utf-8"))
    # `started` es hora local sin zona; la ventana se abre un poco para no perder el primer y el último turno.
    start = datetime.fromisoformat(report["started"]).astimezone(timezone.utc) - timedelta(minutes=5)
    end = datetime.now(timezone.utc) + timedelta(minutes=5)
    try:
        all_spans = list(spans(args.phoenix.rstrip("/"), start, end))
    except OSError as exc:
        print(f"Phoenix no responde en {args.phoenix} ({exc}): levanta el stack antes de contar")
        return 2
    summary = count(report, all_spans)
    base = args.informe.with_name(args.informe.stem + "_tokens")
    base.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    base.with_suffix(".md").write_text(render(summary), encoding="utf-8")
    print(render(summary))
    print(f"Guardado en {base.with_suffix('.md')}")
    return 0 if summary["conversations_with_traces"] == summary["conversations"] else 1


if __name__ == "__main__":
    sys.exit(main())
