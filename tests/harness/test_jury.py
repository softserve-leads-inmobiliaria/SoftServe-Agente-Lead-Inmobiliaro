"""Tests de la prueba de jurado (#247), sin Bedrock: qué conversaciones corre y que las notas apunten a un turno real.

La corrida contra el stack (`harness/scripts/jury.py`) no corre en CI: necesita el LLM real.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))

import jury  # noqa: E402


def test_a_letter_runs_its_whole_set_and_a_name_runs_one_conversation():
    assert jury._names(["M"]) == [name for name in jury.CONVERSATIONS if name.startswith("M")]
    assert len(jury._names(["m"])) == 6
    assert jury._names(["M4_preguntas_trampa"]) == ["M4_preguntas_trampa"]
    assert jury._names(["M", "M4_preguntas_trampa"]) == jury._names(["M"])  # sin repetir
    assert jury._names([]) == list(jury.CONVERSATIONS)


def test_an_unknown_set_or_name_stops_before_spending_quota():
    with pytest.raises(SystemExit):
        jury._names(["Z"])


def test_every_note_points_to_a_turn_that_exists():
    for (name, turn), note in jury.WATCH.items():
        assert name in jury.CONVERSATIONS and 1 <= turn <= len(jury.CONVERSATIONS[name][1]), (name, turn)
        assert note.strip()


def test_only_the_first_turn_can_be_the_empty_start_of_a_listing():
    for name, (listing, turns) in jury.CONVERSATIONS.items():
        assert all(turns[1:]), name
        assert (turns[0] == "") == (listing is not None), name
