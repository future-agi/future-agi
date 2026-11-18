"""A built-in eval's YAML is the only source of its tags and of its offer to environments."""

from pathlib import Path

import pytest
import yaml
from django.core.cache import cache

from model_hub.management.commands.seed_system_evals import (
    SYSTEM_EVALS_DIR,
    SYSTEM_EVALS_VERSION,
    _eval_accepts_pdf,
    load_yaml_evals,
    seed_evals,
)
from model_hub.models.evals_metric import EvalTemplate

SIX = {
    "advice_authority_boundary": {"Insurance"},
    "claim_intake_accuracy": {"Insurance"},
    "lead_qualification_completeness": {"Insurance"},
    "no_misselling": {"Insurance"},
    "intake_field_accuracy": {"Accuracy"},
    "customer_agent_task_completion": set(),
}
BASE = {"Agents", "Conversation", "Voice", "Chatbot behaviors"}
CONVERSATION_AUDIT_EVALS = {
    "customer_agent_task_completion",
    "intake_field_accuracy",
    "customer_agent_query_handling",
    "conversation_hallucination",
}


def _one_system_template(name):
    rows = list(
        EvalTemplate.no_workspace_objects.filter(
            name=name, owner="system", deleted=False
        )
    )
    assert len(rows) == 1, f"{name}: expected one system template, found {len(rows)}"
    return rows[0]


def _yaml(name, subdir="agent"):
    return yaml.safe_load(
        (Path(SYSTEM_EVALS_DIR) / subdir / f"{name}.yaml").read_text()
    )


def _expected_tags(eval_def, template):
    expected = list(eval_def.get("eval_tags") or [])
    if _eval_accepts_pdf(template.config or {}) and "PDF" not in expected:
        expected.append("PDF")
    return expected


def test_the_six_have_their_tags():
    for name, extra in SIX.items():
        data = _yaml(name)
        assert set(data["eval_tags"]) == BASE | extra, name
        assert data["config"]["required_keys"] == ["agent_prompt", "conversation"], name
        assert data["offered_in_environments"] is True, name


def test_conversation_prompts_are_one_prompt():
    for name in set(SIX) | CONVERSATION_AUDIT_EVALS:
        data = _yaml(name)
        assert data["config"]["rule_prompt"] == data["criteria"], name


def test_every_offered_eval_declares_required_keys_and_tags():
    offered = [e for e in load_yaml_evals() if e.get("offered_in_environments")]
    assert offered
    bad = sorted(
        e["name"]
        for e in offered
        if not (e.get("config") or {}).get("required_keys") or not e.get("eval_tags")
    )
    assert bad == [], bad


@pytest.mark.django_db
def test_every_template_is_stored_with_its_yaml_tags():
    seed_evals(force=True)
    for eval_def in load_yaml_evals():
        template = _one_system_template(eval_def["name"])
        assert template.eval_tags == _expected_tags(eval_def, template), eval_def[
            "name"
        ]


@pytest.mark.django_db
def test_the_six_are_stored_with_their_tags():
    seed_evals(force=True)
    for name, extra in SIX.items():
        template = _one_system_template(name)
        assert set(template.eval_tags) == BASE | extra, name


@pytest.mark.django_db
def test_the_version_gate_reopens_for_this_change():
    # The cache is process-wide, not rolled back with the test transaction:
    # restore whatever was there so no later test sees this test's value.
    previous = cache.get("system_evals_version")
    assert SYSTEM_EVALS_VERSION > 26
    cache.set("system_evals_version", 26)
    try:
        seed_evals()
        for name, extra in SIX.items():
            template = _one_system_template(name)
            assert set(template.eval_tags) == BASE | extra, name
        for name in CONVERSATION_AUDIT_EVALS:
            template = _one_system_template(name)
            assert (
                template.config["rule_prompt"] == _yaml(name)["config"]["rule_prompt"]
            ), name
        assert cache.get("system_evals_version") == SYSTEM_EVALS_VERSION
    finally:
        if previous is None:
            cache.delete("system_evals_version")
        else:
            cache.set("system_evals_version", previous)


@pytest.mark.django_db
def test_an_eval_not_offered_keeps_its_own_tags():
    seed_evals(force=True)
    for name, subdir in (
        ("dead_air_detection", "function"),
        ("voicemail_handling", "agent"),
    ):
        data = _yaml(name, subdir)
        assert not data.get("offered_in_environments"), name
        template = _one_system_template(name)
        assert template.eval_tags == _expected_tags(data, template), name
