"""Métricas de la evaluación en vivo (#216): qué se observó en cada turno, si cumple lo esperado y los totales.

Funciones puras, sin red ni base: `evaluate.py` junta las observaciones contra el stack real y las pasa
por acá, y `tests/harness/test_evaluation.py` las prueba sin Bedrock.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from scenarios import ALWAYS_FORBIDDEN, Scenario, Turn

# Textos fijos de `services/core/app/agent.py` (un test comprueba que sigan iguales allá).
LLM_FALLBACK_REPLY = "Sorry, I had a problem processing your message. Could you send it again?"
FACTUALITY_DEGRADE_PREFIX = "That's a detail I'd rather have one of our agents confirm, so you get it right."
REGENERATED = "output.factuality_regenerated"
DEGRADED = "output.factuality_degraded"

# Umbrales sobre los totales de la corrida: (dirección, valor). "max": la tasa no puede pasarlo; "min": no
# puede quedar por debajo. Una regeneración no llega al lead, pero cuesta una llamada y es la antesala de
# una respuesta degradada; por eso su umbral es más holgado.
THRESHOLDS: dict[str, tuple[str, float]] = {
    "degraded_rate": ("max", 0.05),
    "fallback_rate": ("max", 0.02),
    "regenerated_rate": ("max", 0.30),
    "tool_hit_rate": ("min", 0.90),
    "turn_pass_rate": ("min", 0.90),
    "final_pass_rate": ("min", 0.80),
}


@dataclass
class TurnObservation:
    text: str
    reply: str
    tools: list[str] = field(default_factory=list)  # tools que llamó el modelo (`messages.tool_calls`)
    audits: list[str] = field(default_factory=list)  # acciones nuevas en `audit_log` durante el turno
    latency_s: float = 0.0
    error: str | None = None  # el turno no llegó a responder (HTTP, timeout)
    # Por qué contestó así (`turn.decision`): la ruta, la intención y lo que leyó el clasificador.
    decision: dict[str, Any] | None = None


@dataclass
class ScenarioRun:
    scenario: str
    rep: int
    turns: list[TurnObservation] = field(default_factory=list)
    final_state: str | None = None
    band: str | None = None
    conversation_id: str = ""


def is_fallback(obs: TurnObservation) -> bool:
    return obs.error is not None or obs.reply.strip().startswith(LLM_FALLBACK_REPLY)


def is_degraded(obs: TurnObservation) -> bool:
    return DEGRADED in obs.audits or obs.reply.strip().startswith(FACTUALITY_DEGRADE_PREFIX)


def is_regenerated(obs: TurnObservation) -> bool:
    return REGENERATED in obs.audits


def tool_hit(turn: Turn, obs: TurnObservation) -> bool | None:
    """Si llamó alguna de las tools esperadas; `None` si el turno no espera ninguna, o si contestó sin tools un
    dato que ya tenía de un turno anterior (`remembered_ok`, #217)."""
    if not turn.tools or (turn.remembered_ok and not obs.tools):
        return None
    return any(tool in obs.tools for tool in turn.tools)


def turn_failures(turn: Turn, obs: TurnObservation) -> list[str]:
    """Lo que el turno no cumplió. Vacío si está bien."""
    if obs.error is not None:
        return [f"sin respuesta: {obs.error}"]
    reply = obs.reply.lower()
    failures = []
    if is_fallback(obs):
        failures.append("respuesta de respaldo del LLM")
    if is_degraded(obs):
        failures.append("respuesta degradada por la factualidad")
    if tool_hit(turn, obs) is False:
        failures.append(f"no llamó {' ni '.join(turn.tools)} (llamó {', '.join(obs.tools) or 'ninguna'})")
    if turn.expect_any and not any(text.lower() in reply for text in turn.expect_any):
        failures.append(f"no dice ninguno de {list(turn.expect_any)}")
    said = [text for text in (*turn.forbid, *ALWAYS_FORBIDDEN) if text.lower() in reply]
    if said:
        failures.append(f"dice {said}")
    missing = [action for action in turn.audits if action not in obs.audits]
    if missing:
        failures.append(f"sin la auditoría {missing}")
    return failures + decision_failures(turn, obs.decision)


def route_matches(route: str, expected: tuple[str, ...]) -> bool:
    """La ruta `agenda` vale para cualquier paso de la agenda (`agenda:offer`); `agenda:booked`, solo para ese."""
    return any(route == want or route.startswith(want + ":") for want in expected)


def decision_failures(turn: Turn, decision: dict[str, Any] | None) -> list[str]:
    """Lo que el turno no cumplió de quién contestó (`route`) y de qué propiedad entendió que habla (`about`)."""
    if not (turn.route or turn.about):
        return []
    if decision is None:
        return ["sin turn.decision"]
    failures = []
    route = decision.get("route") or "?"
    if turn.route and not route_matches(route, turn.route):
        failures.append(f"ruta {route}, se esperaba {' o '.join(turn.route)}")
    if turn.about and decision.get("refers_to") != turn.about:
        failures.append(f"habla de {decision.get('refers_to')}, se esperaba {turn.about}")
    return failures


def final_failures(scenario: Scenario, run: ScenarioRun) -> list[str]:
    failures = []
    if scenario.final_states and run.final_state not in scenario.final_states:
        failures.append(f"estado final {run.final_state!r}, se esperaba {sorted(scenario.final_states)}")
    if scenario.final_bands and run.band not in scenario.final_bands:
        failures.append(f"banda {run.band!r}, se esperaba {sorted(scenario.final_bands)}")
    return failures


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _percentile(values: list[float], pct: float) -> float | None:
    """Percentil por rango más cercano."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def summarize(runs: list[ScenarioRun], scenarios: dict[str, Scenario]) -> dict[str, Any]:
    """Conteos y tasas de un grupo de corridas. Los turnos sin texto (el deep-link) no cuentan: no llaman al LLM."""
    turns = degraded = regenerated = fallback = expected_tools = hits = passed = 0
    finals = finals_ok = 0
    latencies: list[float] = []
    for run in runs:
        scenario = scenarios[run.scenario]
        for turn, obs in zip(scenario.turns, run.turns, strict=True):
            if not turn.text:
                continue
            turns += 1
            degraded += is_degraded(obs)
            regenerated += is_regenerated(obs)
            fallback += is_fallback(obs)
            hit = tool_hit(turn, obs)
            if hit is not None:
                expected_tools += 1
                hits += hit
            passed += not turn_failures(turn, obs)
            if obs.error is None:
                latencies.append(obs.latency_s)
        if scenario.final_states or scenario.final_bands:
            finals += 1
            finals_ok += not final_failures(scenario, run)
    return {
        "runs": len(runs), "turns": turns, "degraded": degraded, "regenerated": regenerated, "fallback": fallback,
        "tool_expected": expected_tools, "tool_hits": hits, "turns_passed": passed,
        "finals": finals, "finals_passed": finals_ok,
        "degraded_rate": _rate(degraded, turns), "regenerated_rate": _rate(regenerated, turns),
        "fallback_rate": _rate(fallback, turns), "tool_hit_rate": _rate(hits, expected_tools),
        "turn_pass_rate": _rate(passed, turns), "final_pass_rate": _rate(finals_ok, finals),
        "latency_p50_s": _percentile(latencies, 50), "latency_p95_s": _percentile(latencies, 95),
    }


def breaches(summary: dict[str, Any], thresholds: dict[str, tuple[str, float]] = THRESHOLDS) -> list[str]:
    """Las métricas que pasan su umbral. Una tasa sin datos (`None`) no se juzga."""
    out = []
    for metric, (direction, limit) in thresholds.items():
        value = summary.get(metric)
        if value is None:
            continue
        if (direction == "max" and value > limit) or (direction == "min" and value < limit):
            sign = "≤" if direction == "max" else "≥"
            out.append(f"{metric} = {value:.1%} (umbral {sign} {limit:.0%})")
    return out


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}s"


def _row(name: str, s: dict[str, Any]) -> str:
    return (f"| {name} | {s['runs']} | {s['turns']} | {_pct(s['turn_pass_rate'])} | {_pct(s['tool_hit_rate'])} | "
            f"{_pct(s['regenerated_rate'])} | {_pct(s['degraded_rate'])} | {_pct(s['fallback_rate'])} | "
            f"{_pct(s['final_pass_rate'])} | {_seconds(s['latency_p50_s'])} / {_seconds(s['latency_p95_s'])} |")


def render_report(runs: list[ScenarioRun], scenarios: dict[str, Scenario], header: list[str]) -> str:
    """Informe Markdown: totales, por escenario, los umbrales y cada turno que falló, con la respuesta."""
    total = summarize(runs, scenarios)
    lines = [*header, ""]
    problems = breaches(total)
    lines.append("**Resultado: " + ("dentro de los umbrales**" if not problems else "FUERA de los umbrales**"))
    lines += [f"- {problem}" for problem in problems]
    lines += ["", "| Escenario | Corridas | Turnos | Turnos OK | Tool esperada | Regeneradas | Degradadas | Respaldo "
              "| Estado final | Latencia p50 / p95 |", "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name in scenarios:
        group = [run for run in runs if run.scenario == name]
        if group:
            lines.append(_row(f"`{name}`", summarize(group, scenarios)))
    lines.append(_row("**Total**", total))
    lines += ["", "## Turnos que fallaron", ""]
    failed = False
    for run in runs:
        scenario = scenarios[run.scenario]
        for i, (turn, obs) in enumerate(zip(scenario.turns, run.turns, strict=True), 1):
            failures = turn_failures(turn, obs) if turn.text else []
            if failures:
                failed = True
                reply = " ".join(obs.reply.split())
                lines += [f"- `{run.scenario}` #{run.rep} turno {i} — {'; '.join(failures)}",
                          f"  - lead: {turn.text}", f"  - agente: {reply[:300]}{'…' if len(reply) > 300 else ''}"]
        for failure in final_failures(scenario, run):
            failed = True
            lines.append(f"- `{run.scenario}` #{run.rep} — {failure} (conversación `{run.conversation_id}`)")
    if not failed:
        lines.append("Ninguno.")
    return "\n".join(lines) + "\n"


def as_json(runs: list[ScenarioRun], scenarios: dict[str, Scenario]) -> dict[str, Any]:
    return {"summary": summarize(runs, scenarios), "breaches": breaches(summarize(runs, scenarios)),
            "runs": [asdict(run) for run in runs]}
