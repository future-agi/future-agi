"""Existing catalogs must receive the corrected persisted similarity code."""

import builtins
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from django.core.cache import cache

from model_hub.management.commands import seed_system_evals as seeder
from model_hub.models.evals_metric import EvalTemplate

# Exact public-dev f609792baa269eb1da22cd13dd95f5c01ec3dc5e catalog bodies.
PREVIOUS_VERSION = 31
FIXTURES = Path(__file__).parent / "fixtures" / "similarity_version_31"
NAMES = ("answer_similarity", "jaro_winkler_similarity")


def _score(template, output, expected):
    namespace: dict = {}
    builtins.exec(compile(template.config["code"], template.name, "exec"), namespace)
    evaluate = namespace["evaluate"]
    if template.name == "answer_similarity":
        return evaluate(
            None,
            None,
            None,
            None,
            response=output,
            expected_response=expected,
            comparator="JaroWincklerSimilarity",
        )["score"]
    return evaluate(None, output, expected, None, case_insensitive=False)["score"]


def _templates():
    return {
        template.name: template
        for template in EvalTemplate.no_workspace_objects.filter(
            name__in=NAMES, owner="system", deleted=False
        )
    }


def test_catalog_version_advances_past_previous_install():
    assert seeder.SYSTEM_EVALS_VERSION > PREVIOUS_VERSION


@pytest.mark.django_db
@pytest.mark.parametrize("name", NAMES)
def test_existing_templates_refresh_scores_and_repeat_seed_is_idempotent(
    name, monkeypatch
):
    previous_cache = cache.get("system_evals_version")
    candidate_definitions = seeder.load_yaml_evals()
    old_definitions = deepcopy(candidate_definitions)
    for index, definition in enumerate(old_definitions):
        if definition["name"] in NAMES:
            old_definitions[index] = {
                **yaml.safe_load((FIXTURES / f"{definition['name']}.yaml").read_text()),
                "_track": definition["_track"],
                "_source_file": definition["_source_file"],
            }
    controls = [
        ("MARTHA", "MARHTA", 0.9611111111111111),
        ("DIXON", "DICKSONX", 0.8133333333333332),
        ("hello", "hello", 1.0),
        ("", "hello", 0.0),
    ]
    try:
        # Exercise the actual seeder and PostgreSQL with the already-shipped
        # definitions/version, not a fabricated placeholder in a model mock.
        with monkeypatch.context() as old_install:
            old_install.setattr(seeder, "SYSTEM_EVALS_VERSION", PREVIOUS_VERSION)
            old_install.setattr(seeder, "load_yaml_evals", lambda: old_definitions)
            seeder.seed_evals(force=True)
        assert cache.get("system_evals_version") == PREVIOUS_VERSION
        before = _templates()
        assert set(before) == set(NAMES)
        old_score = _score(before[name], "dadc", "acdcabbc")
        assert old_score == pytest.approx(0.5416666666666666)
        for output, expected, score in controls:
            assert _score(before[name], output, expected) == pytest.approx(score)

        # No --force: this is the startup path that previously skipped both rows.
        assert seeder.seed_evals() == (0, 2, len(candidate_definitions) - 2)
        after = _templates()
        for eval_name in NAMES:
            assert after[eval_name].pk == before[eval_name].pk
            expected_definition = next(
                d for d in candidate_definitions if d["name"] == eval_name
            )
            assert after[eval_name].config == expected_definition["config"]
        assert cache.get("system_evals_version") == seeder.SYSTEM_EVALS_VERSION
        new_score = _score(after[name], "dadc", "acdcabbc")
        assert new_score == pytest.approx(0.5972222222222222)
        for output, expected, score in controls:
            assert _score(after[name], output, expected) == pytest.approx(score)
        print(f"{name}: persisted odd-transposition score {old_score} -> {new_score}")

        stored = list(EvalTemplate.no_workspace_objects.filter(name__in=NAMES).values())
        assert seeder.seed_evals() == (0, 0, 0)
        assert (
            list(EvalTemplate.no_workspace_objects.filter(name__in=NAMES).values())
            == stored
        )
        # Also prove an explicit repeat refresh finds no changed definitions.
        assert seeder.seed_evals(force=True) == (0, 0, len(candidate_definitions))
    finally:
        if previous_cache is None:
            cache.delete("system_evals_version")
        else:
            cache.set("system_evals_version", previous_cache, timeout=None)
