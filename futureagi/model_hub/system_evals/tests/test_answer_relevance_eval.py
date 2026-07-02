"""
Tests for the answer_relevance agent evaluator YAML.

Verifies:
- YAML schema (required fields, types)
- Template variables match required_keys
- eval_id uniqueness against known range
- Tags, modalities, and output type are correct
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

YAML_PATH = Path(__file__).resolve().parent.parent / "agent" / "answer_relevance.yaml"


@pytest.fixture(scope="module")
def eval_def():
    return yaml.safe_load(YAML_PATH.read_text())


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_yaml_loads_without_error(eval_def):
    assert eval_def is not None


def test_required_top_level_fields(eval_def):
    for field in ("eval_id", "name", "description", "criteria", "config"):
        assert field in eval_def, f"Missing required field: {field}"


def test_name_matches_filename(eval_def):
    assert eval_def["name"] == "answer_relevance"


def test_output_type_is_percentage(eval_def):
    assert eval_def["output_type_normalized"] == "percentage"


def test_pass_threshold_in_range(eval_def):
    threshold = eval_def["pass_threshold"]
    assert 0.0 <= threshold <= 1.0


def test_eval_type_is_agent_evaluator(eval_def):
    assert eval_def["config"]["eval_type_id"] == "AgentEvaluator"


# ---------------------------------------------------------------------------
# required_keys vs. template variables
# ---------------------------------------------------------------------------


def test_required_keys_present_in_config(eval_def):
    required_keys = eval_def["config"]["required_keys"]
    assert "input" in required_keys
    assert "output" in required_keys


def test_rule_prompt_contains_template_variables(eval_def):
    rule_prompt = eval_def["config"]["rule_prompt"]
    required_keys = eval_def["config"]["required_keys"]
    for key in required_keys:
        assert f"{{{{{key}}}}}" in rule_prompt, (
            f"Template variable '{{{{{key}}}}}' missing from rule_prompt"
        )


def test_config_params_desc_covers_required_keys(eval_def):
    params_desc = eval_def["config"]["config_params_desc"]
    for key in eval_def["config"]["required_keys"]:
        assert key in params_desc, f"config_params_desc missing key: {key}"


def test_param_modalities_covers_required_keys(eval_def):
    modalities = eval_def["config"]["param_modalities"]
    for key in eval_def["config"]["required_keys"]:
        assert key in modalities, f"param_modalities missing key: {key}"


# ---------------------------------------------------------------------------
# eval_id uniqueness guard
# ---------------------------------------------------------------------------


def test_eval_id_is_within_expected_range(eval_def):
    """eval_id 202 should not collide with existing agent evals (max was 200)."""
    assert eval_def["eval_id"] == 202


def test_no_other_agent_eval_uses_same_eval_id(eval_def):
    agent_dir = YAML_PATH.parent
    our_id = eval_def["eval_id"]
    for path in agent_dir.glob("*.yaml"):
        if path.name == "answer_relevance.yaml":
            continue
        other = yaml.safe_load(path.read_text())
        assert other.get("eval_id") != our_id, (
            f"eval_id {our_id} collision with {path.name}"
        )


# ---------------------------------------------------------------------------
# Tags and visibility
# ---------------------------------------------------------------------------


def test_has_rag_tag(eval_def):
    assert "RAG" in eval_def.get("eval_tags", [])


def test_visible_in_ui(eval_def):
    assert eval_def.get("visible_ui") is True


def test_allow_copy_is_true(eval_def):
    assert eval_def["permissions"]["allow_copy"] is True
