"""Tests de la evaluación en vivo (#216): escenarios, expectativas por turno, métricas y umbrales, sin Bedrock.

La corrida contra el stack (`harness/scripts/evaluate.py`) no corre en CI: necesita el LLM real. Lo que se
prueba acá es lo que decide si un turno está bien y cómo se cuentan las tasas, y que los escenarios no se
desalineen del catálogo, de las tools ni de los textos fijos del core.
"""
import re
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))

import evaluation  # noqa: E402
from evaluation import (  # noqa: E402
    ScenarioRun,
    TurnObservation,
    breaches,
    render_report,
    summarize,
    turn_failures,
)
from jury_scenarios import JURY  # noqa: E402
from scenarios import INVENTED_ZONES, INVENTORY_ZONES, SCENARIOS, Scenario, Turn  # noqa: E402

ALL = {**SCENARIOS, **JURY}

CORE_APP = ROOT / "services" / "core" / "app"


def _obs(text="hi", reply="Sure, here it is.", tools=(), audits=(), latency=1.0, error=None, decision=None):
    return TurnObservation(text, reply, list(tools), list(audits), latency, error, decision)


# --- Escenarios alineados con el resto del repo --------------------------------------------------------

def test_every_expected_tool_has_a_contract():
    tools = {tool for s in ALL.values() for t in s.turns for tool in t.tools}
    contracts = {p.name.removesuffix(".schema.json") for p in (ROOT / "contracts" / "mcp").glob("*.schema.json")}
    assert tools and tools <= contracts


def test_every_expected_audit_is_one_the_core_writes():
    actions = {action for s in ALL.values() for t in s.turns for action in t.audits}
    source = "".join(p.read_text(encoding="utf-8") for p in CORE_APP.glob("*.py"))
    assert actions and all(f'"{action}"' in source for action in actions)


def test_fixed_replies_match_the_core():
    agent = (CORE_APP / "agent.py").read_text(encoding="utf-8")
    assert f'"{evaluation.LLM_FALLBACK_REPLY}"' in agent
    assert f'"{evaluation.FACTUALITY_DEGRADE_PREFIX}"' in agent
    assert all(f'"{action}"' in agent for action in (evaluation.REGENERATED, evaluation.DEGRADED))


def test_inventory_zones_are_the_catalog_zones():
    data = (ROOT / "services" / "catalog" / "app" / "data.py").read_text(encoding="utf-8")
    zones = set(re.findall(r'"zone": "([^"]+)"', data))
    assert zones == set(INVENTORY_ZONES)
    assert not zones & set(INVENTED_ZONES)


def test_conversar_uses_the_same_scenarios():
    demo = runpy.run_path(str(ROOT / "scripts" / "demo" / "conversar.py"), run_name="conversar")
    assert list(demo["ESCENARIOS"]) == list(SCENARIOS)
    for name, scenario in SCENARIOS.items():
        assert demo["ESCENARIOS"][name]["turnos"] == [t.text for t in scenario.turns]


def test_every_scenario_expects_something():
    for name, scenario in ALL.items():
        assert any(t.tools or t.expect_any or t.audits or t.forbid or t.route for t in scenario.turns), name


def test_every_expected_route_is_one_the_core_writes():
    source = "".join(p.read_text(encoding="utf-8") for p in CORE_APP.glob("*.py"))
    for scenario in ALL.values():
        for route in (route for turn in scenario.turns for route in turn.route):
            name, _, kind = route.partition(":")
            assert f'"{name}"' in source, route  # `route="handoff"`, `route: str = "agent"`, `"frustration" if`
            assert not kind or f'kind="{kind}"' in source, route


def test_every_property_a_turn_is_about_is_in_the_catalog():
    data = (ROOT / "services" / "catalog" / "app" / "data.py").read_text(encoding="utf-8")
    abouts = {turn.about for s in ALL.values() for turn in s.turns if turn.about}
    assert abouts and all(f'"id": "{about}"' in data for about in abouts)


def test_the_jury_runs_the_same_conversations_it_measures():
    import jury

    assert jury.CONVERSATIONS == {name: (s.property_id, [t.text for t in s.turns]) for name, s in JURY.items()}
    assert not set(JURY) & set(SCENARIOS)


# --- Un turno -------------------------------------------------------------------------------------------

def test_turn_without_expectations_passes():
    assert turn_failures(Turn("hi"), _obs()) == []


@pytest.mark.parametrize("tools, ok", [(["get_property"], True), (["get_availability", "x"], True),
                                       ([], False), (["search_properties"], False)])
def test_any_of_the_expected_tools_is_enough(tools, ok):
    turn = Turn("is it available?", tools=("get_availability", "get_property"))
    assert (turn_failures(turn, _obs(tools=tools)) == []) is ok


def test_a_fact_answered_from_memory_passes_without_counting_as_a_tool_hit():
    """#217: los requisitos que ya trajo la ficha se pueden contestar sin volver a llamarla (#233)."""
    turn = Turn("What are the requirements?", tools=("get_property", "check_requirements"), remembered_ok=True)
    assert turn_failures(turn, _obs()) == [] and evaluation.tool_hit(turn, _obs()) is None
    # Si llama una tool, tiene que ser una de las esperadas.
    assert turn_failures(turn, _obs(tools=["search_properties"])) != []


def test_expect_any_and_forbid_ignore_case():
    turn = Turn("areas?", expect_any=("El Poblado", "Laureles"), forbid=("Chicó",))
    assert turn_failures(turn, _obs(reply="We have LAURELES.")) == []
    assert turn_failures(turn, _obs(reply="We have Laureles and chicó."))[0].startswith("dice")
    assert turn_failures(turn, _obs(reply="Several areas."))[0].startswith("no dice ninguno")


def test_always_forbidden_applies_to_every_turn():
    failures = turn_failures(Turn("price?"), _obs(reply="Let me check and get back to you."))
    assert failures == ["dice ['get back to you']"]


def test_missing_audit_fails():
    turn = Turn("my phone is 300", audits=("input.pii",))
    assert turn_failures(turn, _obs(audits=["input.pii"])) == []
    assert turn_failures(turn, _obs()) == ["sin la auditoría ['input.pii']"]


def test_fallback_degraded_and_errors_fail():
    assert "respuesta de respaldo del LLM" in turn_failures(Turn("x"), _obs(reply=evaluation.LLM_FALLBACK_REPLY))
    degraded = _obs(reply=evaluation.FACTUALITY_DEGRADE_PREFIX + " Want an agent?",
                    audits=[evaluation.REGENERATED, evaluation.DEGRADED])
    assert turn_failures(Turn("x"), degraded) == ["respuesta degradada por la factualidad"]
    assert turn_failures(Turn("x"), _obs(error="TimeoutError: timed out")) == ["sin respuesta: TimeoutError: timed out"]


DECIDED = {"route": "agenda:offer", "agent_route": "agenda", "refers_to": "prop_00233"}


@pytest.mark.parametrize("route, ok", [(("agenda",), True), (("agenda:offer",), True), (("agenda:booked",), False),
                                       (("agent", "handoff"), False)])
def test_the_route_is_read_from_the_turn_decision(route, ok):
    turn = Turn("Can I see it on Monday?", route=route)
    assert (turn_failures(turn, _obs(decision=DECIDED)) == []) is ok


def test_the_property_the_message_is_about_is_read_from_the_turn_decision():
    assert turn_failures(Turn("the cheaper one", about="prop_00233"), _obs(decision=DECIDED)) == []
    assert turn_failures(Turn("the cheaper one", about="prop_00234"), _obs(decision=DECIDED)) == [
        "habla de prop_00233, se esperaba prop_00234"]


def test_without_a_turn_decision_the_route_cannot_pass_but_other_turns_do_not_need_it():
    assert turn_failures(Turn("x", route=("agent",)), _obs()) == ["sin turn.decision"]
    assert turn_failures(Turn("x"), _obs()) == []


def test_a_regeneration_alone_is_not_a_failure():
    assert turn_failures(Turn("x"), _obs(audits=[evaluation.REGENERATED])) == []


# --- Métricas y umbrales -----------------------------------------------------------------------------------

DEMO = {
    "s": Scenario("demo", "prop_1", (Turn(""), Turn("available?", tools=("get_property",)), Turn("price?")),
                  final_states=frozenset({"RECOMMEND"})),
}


def _run(turns, final_state="RECOMMEND", rep=1):
    return ScenarioRun("s", rep, [_obs(text="", reply="Hi!", latency=0.1), *turns], final_state)


def test_summarize_skips_the_deep_link_turn_and_counts_rates():
    runs = [
        _run([_obs(tools=["get_property"], latency=2), _obs(audits=[evaluation.REGENERATED], latency=4)]),
        _run([_obs(tools=[], latency=6), _obs(reply=evaluation.FACTUALITY_DEGRADE_PREFIX, latency=8,
                                               audits=[evaluation.REGENERATED, evaluation.DEGRADED])],
             final_state="QUALIFY", rep=2),
    ]
    s = summarize(runs, DEMO)
    assert (s["runs"], s["turns"]) == (2, 4)
    assert (s["regenerated"], s["degraded"], s["fallback"]) == (2, 1, 0)
    assert (s["tool_expected"], s["tool_hits"], s["turns_passed"]) == (2, 1, 2)
    assert (s["finals"], s["finals_passed"]) == (2, 1)
    assert s["regenerated_rate"] == 0.5 and s["tool_hit_rate"] == 0.5 and s["final_pass_rate"] == 0.5
    assert (s["latency_p50_s"], s["latency_p95_s"]) == (4, 8)


def test_failed_turns_do_not_count_toward_latency():
    s = summarize([_run([_obs(tools=["get_property"], latency=3), _obs(error="boom", latency=120)])], DEMO)
    assert s["fallback"] == 1 and s["latency_p95_s"] == 3


def test_breaches_check_both_directions_and_skip_missing_data():
    limits = {"degraded_rate": ("max", 0.05), "tool_hit_rate": ("min", 0.9), "final_pass_rate": ("min", 0.8)}
    summary = {"degraded_rate": 0.10, "tool_hit_rate": 0.95, "final_pass_rate": None}
    assert breaches(summary, limits) == ["degraded_rate = 10.0% (umbral ≤ 5%)"]
    assert breaches({"degraded_rate": 0.0, "tool_hit_rate": 0.5}, limits) == ["tool_hit_rate = 50.0% (umbral ≥ 90%)"]


def test_report_lists_each_failure_with_the_reply():
    runs = [_run([_obs(tools=[]), _obs(reply="It is $1,150.")], final_state="QUALIFY")]
    report = render_report(runs, DEMO, ["# Evaluación"])
    assert "FUERA de los umbrales" in report
    assert "`s` #1 turno 2 — no llamó get_property (llamó ninguna)" in report
    assert "estado final 'QUALIFY'" in report
    assert "| **Total** | 1 | 2 |" in report


def test_report_with_everything_in_order():
    report = render_report([_run([_obs(tools=["get_property"]), _obs()])], DEMO, ["# Evaluación"])
    assert "dentro de los umbrales" in report and "Ninguno." in report
