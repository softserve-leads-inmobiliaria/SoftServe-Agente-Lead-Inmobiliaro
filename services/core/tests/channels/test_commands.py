"""Revisión del PR #75: comandos de Telegram como texto del lead y un solo criterio de baja."""
from __future__ import annotations

import pytest

from app.channels.canonical import split_command
from app.channels.consent import OPT_OUT_CONFIRMATION, is_opt_out
from app.input_guard import is_opt_out as core_is_opt_out


@pytest.mark.parametrize("raw,expected", [
    ("/start prop_00231", ("", "prop_00231")),
    ("/start camp_verano", ("", "camp_verano")),
    ("/start", ("", None)),
    ("  /start  ", ("", None)),
    ("/start@mi_bot prop_1", ("", "prop_1")),
    ("/stop", ("/stop", None)),
    ("/stop@mi_bot", ("/stop", None)),
    ("hola", ("hola", None)),
    ("/ayuda quiero info", ("/ayuda quiero info", None)),
    ("", ("", None)),
    ("  hola  ", ("hola", None)),
])
def test_split_command(raw, expected):
    assert split_command(raw) == expected


def test_a_start_with_two_words_is_not_a_deep_link():
    assert split_command("/start uno dos") == ("/start uno dos", None)


@pytest.mark.parametrize("phrase", [
    "baja", "eliminen mis datos", "borren mis datos",  # las del canal
    "dejen de escribirme", "cancelar", "unsubscribe", "quiero la baja", "/stop", "STOP",  # las del core
    "delete my data", "please erase my data",  # en inglés, las del canal (#202)
    "stop messaging me", "opt out", "don't text me anymore",  # en inglés, las del core
])
def test_the_channel_recognizes_every_way_the_core_or_the_channel_understands_an_opt_out(phrase):
    assert is_opt_out(phrase)


@pytest.mark.parametrize("phrase", ["dejen de escribirme", "cancelar", "unsubscribe", "quiero la baja"])
def test_it_is_a_superset_of_the_core_criterion(phrase):
    assert core_is_opt_out(phrase) and is_opt_out(phrase)


@pytest.mark.parametrize("phrase", ["hola", "¿Baja el precio?", "busco departamento", "no dejen de mandarme opciones", ""])
def test_ordinary_messages_are_still_not_an_opt_out(phrase):
    assert not is_opt_out(phrase)


def test_the_confirmation_does_not_promise_to_delete_the_data():
    assert "unsubscribed" in OPT_OUT_CONFIRMATION and "delete" not in OPT_OUT_CONFIRMATION
