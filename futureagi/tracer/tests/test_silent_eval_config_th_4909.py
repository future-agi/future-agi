"""TH-4909 regression: the CEC save helper must merge template fields into
runtime config when missing.

Background: a bulk-attach-template flow persisted CustomEvalConfig rows with
`config={}` for AgentEvaluator templates. Those rows never produced
eval_logger rows. Root cause: `normalize_eval_runtime_config` only normalized
the `params` block — it never merged the rest of the template's config into
the runtime config.

These tests lock in two contracts:
  1. Agent template with output/rule_prompt + empty runtime config
     → after save, runtime config has output/rule_prompt filled from the
       template (auto-populate).
  2. Client-provided fields are not overwritten by the merge.
"""
from __future__ import annotations

import pytest

from model_hub.models.evals_metric import EvalTemplate
from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer


@pytest.fixture
def agent_template_working(db, organization, workspace):
    """An Agent template whose config has output + rule_prompt — the common case."""
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


class TestAutoPopulateFromTemplate:
    """The fix: normalize_eval_runtime_config merges template fields into
    runtime config when the runtime config is missing them."""

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
        assert merged["required_keys"] == ["transcript"]

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


def test_normalize_contains_passthrough_keys():
    """Source-level guard: catches a partial revert of the merge logic."""
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


def test_backfill_migration_present():
    """Source-level guard: catches removal of the data-heal migration."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "0076_backfill_silent_eval_configs.py"
    )
    assert migration.exists(), "TH-4909 backfill migration missing"
    text = migration.read_text()
    assert "RunPython" in text and "eval_template" in text
