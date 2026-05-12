"""TH-4909 regression: incomplete eval configs must not slip past save-time.

Background: a bulk-attach-template flow persisted CustomEvalConfig rows with
`config={}` for AgentEvaluator templates. Those rows never produced
eval_logger rows.

These tests lock in the four contracts:
  1. Agent template + missing output/rule_prompt + empty linked template
     → serializer rejects with a clear error.
  2. Agent template + missing output/rule_prompt + working linked template
     → serializer auto-populates from the template (no rejection).
  3. Non-agent template (llm/code) + empty config → still accepted.
  4. The runner refuses to dispatch a still-incomplete agent eval.
"""
from __future__ import annotations

import pytest

from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer


# ---------- fixtures specific to this regression ---------------------------


@pytest.fixture
def agent_template_working(db, organization, workspace):
    """An Agent template whose config has output + rule_prompt (the common case)."""
    return EvalTemplate.objects.create(
        name="agent_working",
        organization=organization,
        workspace=workspace,
        eval_type="agent",
        config={
            "output": "Pass/Fail",
            "rule_prompt": "Evaluate {{transcript}} for X.",
            "eval_type_id": "AgentEvaluator",
            "required_keys": ["transcript"],
        },
    )


@pytest.fixture
def agent_template_empty(db, organization, workspace):
    """An Agent template whose config is itself empty — mirrors the
    edge case where a template was created without instructions and so the
    migration has nothing to copy from."""
    return EvalTemplate.objects.create(
        name="agent_empty",
        organization=organization,
        workspace=workspace,
        eval_type="agent",
        config={},
    )


@pytest.fixture
def llm_template(db, organization, workspace):
    """A non-agent template — the rejection rule must NOT apply to these."""
    return EvalTemplate.objects.create(
        name="llm_template",
        organization=organization,
        workspace=workspace,
        eval_type="llm",
        config={},
    )


# ---------- serializer contracts -------------------------------------------


class TestRejectIncompleteAgentEvalConfig:
    """1+2: at save time the serializer either auto-populates or rejects."""

    def test_empty_config_with_working_agent_template_auto_populates(
        self, db, project, agent_template_working
    ):
        s = CustomEvalConfigSerializer(
            data={
                "project": str(project.id),
                "eval_template": str(agent_template_working.id),
                "name": "auto_populate_case",
                "config": {},
                "mapping": {"transcript": "transcript"},
            }
        )
        assert s.is_valid(), s.errors
        merged = s.validated_data["config"]
        assert merged["output"] == "Pass/Fail"
        assert merged["rule_prompt"].startswith("Evaluate")
        assert merged["eval_type_id"] == "AgentEvaluator"

    def test_empty_config_with_empty_agent_template_rejects(
        self, db, project, agent_template_empty
    ):
        s = CustomEvalConfigSerializer(
            data={
                "project": str(project.id),
                "eval_template": str(agent_template_empty.id),
                "name": "reject_case",
                "config": {},
                "mapping": {"audio": "conversation.recording"},
            }
        )
        assert not s.is_valid()
        # Error mentions both missing fields and is attached to `config`.
        err = str(s.errors.get("config", ""))
        assert "output" in err
        assert "rule_prompt" in err

    def test_empty_config_with_non_agent_template_still_accepted(
        self, db, project, llm_template
    ):
        s = CustomEvalConfigSerializer(
            data={
                "project": str(project.id),
                "eval_template": str(llm_template.id),
                "name": "llm_passthrough",
                "config": {},
                "mapping": {},
            }
        )
        assert s.is_valid(), s.errors

    def test_partial_config_with_working_agent_template_only_fills_blanks(
        self, db, project, agent_template_working
    ):
        # Client provides output explicitly — server fills rule_prompt from
        # the template but does NOT overwrite client-provided output.
        s = CustomEvalConfigSerializer(
            data={
                "project": str(project.id),
                "eval_template": str(agent_template_working.id),
                "name": "partial_case",
                "config": {"output": "score"},
                "mapping": {"transcript": "transcript"},
            }
        )
        assert s.is_valid(), s.errors
        merged = s.validated_data["config"]
        assert merged["output"] == "score"  # untouched
        assert merged["rule_prompt"].startswith("Evaluate")  # populated


# ---------- source-level guards --------------------------------------------
# These catch a partial revert: if someone deletes one of the four layers
# the tests fail without needing to import the (circular) runner module.


def test_serializer_contains_agent_rejection_block():
    """Layer 1: the serializer's agent-type rejection block."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "serializers" / "custom_eval_config.py"
    text = source.read_text()
    assert 'eval_type", None) == "agent"' in text or '"eval_type", None) == "agent"' in text, \
        "TH-4909 agent-eval rejection block missing from CustomEvalConfigSerializer.validate"
    assert "rule_prompt" in text and "output" in text


def test_normalize_contains_passthrough_keys():
    """Layer 2: the merge-from-template passthrough keys."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "model_hub"
        / "utils"
        / "function_eval_params.py"
    )
    text = source.read_text()
    assert "_TEMPLATE_PASSTHROUGH_KEYS" in text, \
        "TH-4909 template-passthrough merge removed from normalize_eval_runtime_config"
    for key in ("output", "rule_prompt", "eval_type_id", "required_keys"):
        assert f'"{key}"' in text, f"passthrough key '{key}' missing"


def test_runner_contains_defensive_check():
    """Layer 4: the dispatch-time agent-eval guard at top of _run_evaluation."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "utils" / "eval.py"
    text = source.read_text()
    assert "TH-4909" in text and "is missing required" in text, \
        "TH-4909 dispatch-time guard missing from _run_evaluation"


def test_backfill_migration_present():
    """Layer 3: the data migration that heals pre-existing silent rows."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "0076_backfill_silent_eval_configs.py"
    )
    assert migration.exists(), "TH-4909 backfill migration missing"
    text = migration.read_text()
    assert "RunPython" in text and "eval_template" in text
