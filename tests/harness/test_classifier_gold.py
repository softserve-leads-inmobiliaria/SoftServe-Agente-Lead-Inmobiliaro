"""Tests del gold set del clasificador (#247), sin Bedrock: que las etiquetas sean válidas y cómo se cuentan.

La corrida contra el clasificador real (`harness/scripts/classifier_gold.py`) no corre en CI: necesita Bedrock.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
sys.path.insert(0, str(ROOT / "services" / "core"))

import classifier_gold as gold  # noqa: E402

from app.bedrock_llm import CLASSIFIER_FLAGS  # noqa: E402
from app.fakes.fake_llm import INTENTS  # noqa: E402
from app.interest import UNCLEAR  # noqa: E402
from app.visits import visit_when  # noqa: E402

ITEMS = gold.load()
KEYS = {"id", "text", "last", "shown", "intent", "flags", "unsure", "refers_to", "visit_when"}


def test_the_gold_set_has_enough_messages_with_unique_ids():
    assert len(ITEMS) >= 60
    assert len({item["id"] for item in ITEMS}) == len(ITEMS)


def test_every_label_is_one_the_classifier_can_answer():
    catalog = (ROOT / "services" / "catalog" / "app" / "data.py").read_text(encoding="utf-8")
    for item in ITEMS:
        assert set(item) <= KEYS and item["text"].strip(), item["id"]
        assert item.get("intent", "otro") in INTENTS, item["id"]
        assert set(item.get("flags", [])) | set(item.get("unsure", [])) <= set(CLASSIFIER_FLAGS), item["id"]
        assert all(f'"id": "{prop}"' in catalog for prop in item.get("shown", [])), item["id"]
        # Como el clasificador: solo una de las mostradas, "unclear" o nada.
        assert item.get("refers_to") in {None, UNCLEAR, *item.get("shown", [])}, item["id"]


def test_every_field_is_measured_somewhere():
    measured = {field for item in ITEMS for field in gold.expected(item)}
    assert measured == {"intent", "refers_to", "visit_when", *CLASSIFIER_FLAGS}
    for flag in CLASSIFIER_FLAGS:  # cada bandera, con al menos un caso en true
        assert any(flag in item.get("flags", []) for item in ITEMS), flag


def test_the_flags_and_days_are_the_ones_of_the_core():
    assert gold.FLAGS == CLASSIFIER_FLAGS
    label = {"days": ["sat", "sun"], "part_of_day": "morning"}
    assert gold.when(label) == visit_when(label)
    assert gold.when({"other_day": True}) == visit_when({"other_day": True})
    assert gold.when(None) is None and visit_when(None) is None


def test_flags_not_listed_are_expected_false_unless_unsure():
    item = {"id": "x", "text": "This is useless", "flags": ["frustrated"], "unsure": ["asks_for_person"]}
    assert gold.expected(item) == {"wants_alert": False, "wants_visit": False, "frustrated": True,
                                   "asks_unlisted": False}


def _answer(intent="otro", refers_to=None, when=None, **flags):
    return gold.observed(SimpleNamespace(intent=intent, flags=flags, refers_to=refers_to, when=when))


def test_each_field_is_scored_on_its_own_and_a_missing_flag_is_a_miss():
    items = [{"id": "a", "text": "Can I see it on Monday?", "shown": ["prop_00233"], "intent": "agendar_visita",
              "flags": ["wants_visit"], "refers_to": "prop_00233", "visit_when": {"days": ["mon"]}}]
    right = _answer("agendar_visita", "prop_00233", {"days": [0], "part": None, "other_day": False},
                    wants_visit=True, asks_for_person=False, wants_alert=False, frustrated=False, asks_unlisted=False)
    assert gold.score(items, {"a": right})["misses"] == []
    wrong = _answer("agendar_visita", "prop_00233", None, wants_visit=True)
    result = gold.score(items, {"a": wrong})
    assert sorted(m["field"] for m in result["misses"]) == [
        "asks_for_person", "asks_unlisted", "frustrated", "visit_when", "wants_alert"]
    assert result["accuracy"]["intent"] == 1 and result["accuracy"]["visit_when"] == 0


def test_a_message_without_an_answer_misses_every_field():
    items = [{"id": "a", "text": "Hi", "intent": "saludo", "flags": []}]
    result = gold.score(items, {"a": None})
    assert result["accuracy"]["intent"] == 0 and {m["got"] for m in result["misses"]} == {"sin respuesta"}


def test_only_a_field_below_the_baseline_is_a_regression():
    result = {"accuracy": {"intent": 0.9, "wants_visit": 1.0}}
    assert gold.regressions(result, {"intent": 0.9, "wants_visit": 0.95}) == []
    assert gold.regressions(result, {"intent": 0.95}) == ["intent: 90% (línea base 95%)"]
