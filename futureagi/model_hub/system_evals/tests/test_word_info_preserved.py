"""Golden tests for word_info_preserved; expected values are from jiwer.wip."""

import builtins
from pathlib import Path

import yaml

FUNCTION_DIR = Path(__file__).resolve().parent.parent / "function"
WIP = "word_info_preserved"


def _score(name, hyp, ref):
    path = FUNCTION_DIR / f"{name}.yaml"
    code, ns = yaml.safe_load(path.read_text())["config"]["code"], {}
    vars(builtins)["exec"](compile(code, str(path), "exec"), ns)
    return ns["evaluate"](None, hyp, ref, None)["score"]


def test_inserted_words_do_not_erase_hits():
    assert _score(WIP, "the cat sat on the mat", "the cat sat") == 0.5


def test_insertions_and_deletions_together():
    assert _score(WIP, "the big cat sat", "the cat sat down") == 0.5625


def test_cases_without_insertions_unchanged():
    assert abs(_score(WIP, "a b", "a b c") - 2 / 3) < 1e-9


def test_agrees_with_word_info_lost():
    hyp, ref = "one two three four", "one three four five"
    assert abs(_score(WIP, hyp, ref) - _score("word_info_lost", hyp, ref)) < 1e-9
