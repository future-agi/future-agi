"""Upgrade an installed catalog without changing other definitions or IDs."""

import builtins
from pathlib import Path

import pytest
import yaml
from django.core.cache import cache

from model_hub.management.commands import seed_system_evals as seeder
from model_hub.models.evals_metric import EvalTemplate

# The exact numeric_similarity definition shipped with catalog 32.
PREVIOUS = Path(__file__).parent / "fixtures/numeric_version_32/numeric_similarity.yaml"
CACHE_KEY = "system_evals_version"


def _snapshot():
    return {
        row["eval_id"]: row
        for row in EvalTemplate.no_workspace_objects.filter(owner="system").values()
    }


def _score(code):
    namespace: dict = {}
    builtins.exec(compile(code, "numeric_similarity", "exec"), namespace)
    return namespace["evaluate"](None, "1,234", 1234, None)["score"]


@pytest.mark.django_db
def test_existing_catalog_refreshes_only_numeric_similarity(monkeypatch):
    definitions = seeder.load_yaml_evals()
    previous = yaml.safe_load(PREVIOUS.read_text())
    eval_id = previous["eval_id"]
    old_definitions = [
        {**definition, **previous} if definition["eval_id"] == eval_id else definition
        for definition in definitions
    ]
    previous_cache = cache.get(CACHE_KEY)
    try:
        with monkeypatch.context() as old:
            old.setattr(seeder, "SYSTEM_EVALS_VERSION", 32)
            old.setattr(seeder, "load_yaml_evals", lambda: old_definitions)
            seeder.seed_evals(force=True)
        before = _snapshot()
        assert set(before) >= {definition["eval_id"] for definition in definitions}
        assert cache.get(CACHE_KEY) == 32
        assert _score(before[eval_id]["config"]["code"]) == pytest.approx(1 / 1234)

        assert seeder.seed_evals() == (0, 1, len(definitions) - 1)
        assert cache.get(CACHE_KEY) == seeder.SYSTEM_EVALS_VERSION
        after = _snapshot()
        updated = after[eval_id]["config"]["code"]
        assert _score(updated) == 1.0
        before[eval_id]["config"]["code"] = updated
        assert after == before
        assert seeder.seed_evals() == (0, 0, 0)
        assert _snapshot() == after
    finally:
        if previous_cache is None:
            cache.delete(CACHE_KEY)
        else:
            cache.set(CACHE_KEY, previous_cache, timeout=None)
