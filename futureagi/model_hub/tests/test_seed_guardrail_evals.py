"""The guardrail eval fixes (blank keywords, unsupported PII types) live in
YAML code bodies. The seeder only rewrites stored templates when
SYSTEM_EVALS_VERSION moves past the version a database was last seeded at,
so a database already seeded at 28 must pick the new bodies up."""

from pathlib import Path

import pytest
import yaml
from django.core.cache import cache

from model_hub.management.commands.seed_system_evals import (
    SYSTEM_EVALS_VERSION,
    seed_evals,
)
from model_hub.models.evals_metric import EvalTemplate

YAML_DIR = Path(__file__).resolve().parent.parent / "system_evals" / "function"
GUARDRAIL_EVALS = ("contains_any", "contains_none", "regex_pii_detection")


def _yaml_code(name):
    return yaml.safe_load((YAML_DIR / f"{name}.yaml").read_text())["config"]["code"]


def _template(name):
    return EvalTemplate.no_workspace_objects.get(name=name, owner="system")


@pytest.mark.django_db
def test_database_seeded_at_28_receives_the_guardrail_eval_bodies():
    previous = cache.get("system_evals_version")
    try:
        seed_evals(force=True)
        for name in GUARDRAIL_EVALS:
            template = _template(name)
            template.config = {**template.config, "code": "stale body"}
            template.save(update_fields=["config"])
        cache.set("system_evals_version", 28, timeout=None)

        seed_evals()

        for name in GUARDRAIL_EVALS:
            assert _template(name).config["code"] == _yaml_code(name), name
        assert cache.get("system_evals_version") == SYSTEM_EVALS_VERSION
    finally:
        if previous is None:
            cache.delete("system_evals_version")
        else:
            cache.set("system_evals_version", previous, timeout=None)
