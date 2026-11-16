"""Upgrade the persisted MAP template without changing the rest of the catalog."""

import builtins
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from django.core.cache import cache

from agentic_eval.core_evals.fi_evals.function.functions import custom_code_eval
from agentic_eval.core_evals.fi_utils import sandbox
from model_hub.management.commands import seed_system_evals as seeder
from model_hub.models.evals_metric import EvalTemplate

# Exact public-dev 344845831f944b7f388b24ebb07924222bf13a21 template.
PREVIOUS_VERSION = 32
PREVIOUS_MAP = (
    Path(__file__).parent / "fixtures/map_version_32/mean_average_precision.yaml"
)


def _rows():
    return {
        row["eval_id"]: row
        for row in EvalTemplate.no_workspace_objects.filter(owner="system").values()
    }


def _raw_score(code, hypothesis, reference):
    namespace: dict = {}
    vars(builtins)["exec"](compile(code, "stored-map", "exec"), namespace)
    return namespace["evaluate"](None, hypothesis, reference, None)["score"]


def test_catalog_version_advances_past_previous_map_install():
    assert seeder.SYSTEM_EVALS_VERSION > PREVIOUS_VERSION


@pytest.mark.django_db
def test_map_upgrade_preserves_catalog_and_repeat_seed_is_idempotent(
    monkeypatch, settings
):
    previous_cache = cache.get("system_evals_version")
    definitions = seeder.load_yaml_evals()
    old_definitions = deepcopy(definitions)
    for index, definition in enumerate(old_definitions):
        if definition["eval_id"] == 187:
            old_definitions[index] = {
                **yaml.safe_load(PREVIOUS_MAP.read_text()),
                "_track": definition["_track"],
                "_source_file": definition["_source_file"],
            }
    # Use the actual local subprocess executor, not a mocked evaluator result.
    monkeypatch.setattr(sandbox, "CODE_EXECUTOR_URL", "")
    settings.CODE_EXECUTOR_LOCAL_FALLBACK = True
    settings.CLOUD_DEPLOYMENT = ""
    try:
        with monkeypatch.context() as old_install:
            old_install.setattr(seeder, "SYSTEM_EVALS_VERSION", PREVIOUS_VERSION)
            old_install.setattr(seeder, "load_yaml_evals", lambda: old_definitions)
            seeder.seed_evals(force=True)
        assert cache.get("system_evals_version") == PREVIOUS_VERSION
        before = _rows()
        old_code = before[187]["config"]["code"]
        assert _raw_score(old_code, ["a", "a"], ["a"]) == 2.0
        # Preserve the actual runtime failure separately from the raw score:
        # the old local helper name is rejected by the production compiler.
        with pytest.raises(ValueError, match='"_p" is an invalid variable name'):
            custom_code_eval(
                old_code, hypothesis=["a", "a", "b"], reference=["a", "b"]
            )

        assert seeder.seed_evals() == (0, 1, len(definitions) - 1)
        after = _rows()
        assert set(after) == set(before)
        assert {key: value for key, value in before.items() if key != 187} == {
            key: value for key, value in after.items() if key != 187
        }
        assert after[187]["id"] == before[187]["id"]
        assert after[187]["config"] == next(
            item["config"] for item in definitions if item["eval_id"] == 187
        )
        assert cache.get("system_evals_version") == seeder.SYSTEM_EVALS_VERSION
        new_code = after[187]["config"]["code"]
        assert _raw_score(new_code, ["a", "a"], ["a"]) == 1.0
        for hypothesis, reference, expected in [
            (["a", "a", "b"], ["a", "b"], 5 / 6),
            (["a", "b"], ["a", "b"], 1.0),
            (["a", "x", "b"], ["a", "b"], 5 / 6),
            (["x"], ["a"], 0.0),
            (["a"], [], 0.0),
        ]:
            assert custom_code_eval(
                new_code, hypothesis=hypothesis, reference=reference
            )["result"] == pytest.approx(expected)
        assert seeder.seed_evals() == (0, 0, 0)
        assert _rows() == after
        assert seeder.seed_evals(force=True) == (0, 0, len(definitions))
        assert _rows() == after
    finally:
        if previous_cache is None:
            cache.delete("system_evals_version")
        else:
            cache.set("system_evals_version", previous_cache, timeout=None)
