"""Seeder freshness is scoped to the deployment mode that picked ``eval_type``.

Keyed on the catalogue version alone, a stack first seeded as OSS kept the
LLM-as-a-Judge catalogue after its licence landed: the seeder short circuited
on the version before it ever re-read the mode.
"""

import pytest
from django.core.cache import cache

from model_hub.management.commands.seed_system_evals import (
    SYSTEM_EVALS_VERSION,
    seed_evals,
)
from model_hub.models.evals_metric import EvalTemplate

AGENT_TRACK_EVAL = "advice_authority_boundary"
# What a deployment on the previous scheme left behind: a bare catalogue
# version under this key, with nothing in it about the mode.
LEGACY_CACHE_KEY = "system_evals_version"


@pytest.fixture(autouse=True)
def clear_seed_version_cache():
    cache.clear()
    yield
    cache.clear()


def _agent_template():
    return EvalTemplate.no_workspace_objects.get(name=AGENT_TRACK_EVAL)


def _run_as_oss(monkeypatch, oss):
    monkeypatch.setattr("tfc.ee_gating.is_oss", lambda: oss)


def test_seeding_after_the_licence_lands_swaps_the_agent_evaluator_in(db, monkeypatch):
    _run_as_oss(monkeypatch, True)
    seed_evals()

    template = _agent_template()
    assert template.eval_type == "llm"
    assert template.config["eval_type_id"] == "CustomPromptEvaluator"

    _run_as_oss(monkeypatch, False)
    seed_evals()

    template = _agent_template()
    assert template.eval_type == "agent"
    assert template.config["eval_type_id"] == "AgentEvaluator"


def test_a_second_seed_in_the_same_mode_does_no_work(db, monkeypatch):
    _run_as_oss(monkeypatch, True)
    seed_evals()

    assert seed_evals() == (0, 0, 0)

    _run_as_oss(monkeypatch, False)
    assert seed_evals() != (0, 0, 0)


def test_flipping_back_re_seeds_rather_than_reading_as_fresh(db, monkeypatch):
    _run_as_oss(monkeypatch, False)
    seed_evals()

    _run_as_oss(monkeypatch, True)
    seed_evals()
    assert _agent_template().eval_type == "llm"

    _run_as_oss(monkeypatch, False)
    seed_evals()

    template = _agent_template()
    assert template.eval_type == "agent"
    assert template.config["eval_type_id"] == "AgentEvaluator"


def test_a_bare_version_left_by_the_previous_scheme_re_seeds_once(db, monkeypatch):
    _run_as_oss(monkeypatch, False)
    cache.set(LEGACY_CACHE_KEY, SYSTEM_EVALS_VERSION, timeout=None)

    assert seed_evals() != (0, 0, 0)
    assert seed_evals() == (0, 0, 0)
