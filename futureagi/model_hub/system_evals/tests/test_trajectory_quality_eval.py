"""
Tests for the trajectory_quality agent evaluator YAML.

This eval fills the gap between deterministic trajectory evals
(trajectory_match, tool_call_accuracy, step_count) and LLM-based
quality judgment. Unlike the deterministic evals, this does NOT require
a reference/expected trajectory — it judges quality without ground truth.

Verifies:
- YAML schema (required fields, types)
- Template variables match required_keys
- eval_id uniqueness (no collision with function evals)
- Criteria covers the three key dimensions
- Context accepts JSON/LIST for trajectory input
- eval_id is distinct from all existing function and agent evals
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

YAML_PATH = (
    Path(__file__).resolve().parent.parent / "agent" / "trajectory_quality.yaml"
)
FUNCTION_DIR = Path(__file__).resolve().parent.parent / "function"
AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"


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
    assert eval_def["name"] == "trajectory_quality"


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


def test_required_keys_present(eval_def):
    required_keys = eval_def["config"]["required_keys"]
    assert "input" in required_keys
    assert "context" in required_keys
    assert "output" in required_keys


def test_rule_prompt_contains_all_template_variables(eval_def):
    rule_prompt = eval_def["config"]["rule_prompt"]
    for key in eval_def["config"]["required_keys"]:
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
# Trajectory input — context must accept JSON and LIST
# ---------------------------------------------------------------------------


def test_context_accepts_json_modality(eval_def):
    """Trajectory is a JSON array — context must accept JSON."""
    context_modalities = eval_def["config"]["param_modalities"]["context"]
    assert "JSON" in context_modalities, "context must support JSON modality for trajectory arrays"


def test_context_accepts_list_modality(eval_def):
    context_modalities = eval_def["config"]["param_modalities"]["context"]
    assert "LIST" in context_modalities


def test_context_accepts_text_modality(eval_def):
    """Trajectory can also be passed as a formatted text string."""
    context_modalities = eval_def["config"]["param_modalities"]["context"]
    assert "TEXT" in context_modalities


# ---------------------------------------------------------------------------
# eval_id uniqueness — must not collide with function OR agent evals
# ---------------------------------------------------------------------------


def test_eval_id_value(eval_def):
    """eval_id 204 follows citation_quality (203) and answer_relevance (202)."""
    assert eval_def["eval_id"] == 204


def test_no_collision_with_agent_evals(eval_def):
    our_id = eval_def["eval_id"]
    for path in AGENT_DIR.glob("*.yaml"):
        if path.name == "trajectory_quality.yaml":
            continue
        other = yaml.safe_load(path.read_text())
        assert other.get("eval_id") != our_id, (
            f"eval_id {our_id} collision with agent eval {path.name}"
        )


def test_no_collision_with_function_evals(eval_def):
    our_id = eval_def["eval_id"]
    for path in FUNCTION_DIR.glob("*.yaml"):
        other = yaml.safe_load(path.read_text())
        assert other.get("eval_id") != our_id, (
            f"eval_id {our_id} collision with function eval {path.name}"
        )


# ---------------------------------------------------------------------------
# Differentiation from deterministic trajectory evals
# ---------------------------------------------------------------------------


def test_is_agent_evaluator_not_custom_code(eval_def):
    """Key differentiator: this is LLM-as-judge, not deterministic code."""
    assert eval_def["config"]["eval_type_id"] == "AgentEvaluator"
    # Deterministic evals use CustomCodeEval
    assert eval_def["config"]["eval_type_id"] != "CustomCodeEval"


def test_does_not_require_expected_key(eval_def):
    """Unlike trajectory_match and tool_call_accuracy, this eval works without
    a reference/expected trajectory — it judges quality without ground truth."""
    required_keys = eval_def["config"]["required_keys"]
    assert "expected" not in required_keys, (
        "trajectory_quality must NOT require 'expected' — it judges without ground truth"
    )


# ---------------------------------------------------------------------------
# Criteria content sanity checks
# ---------------------------------------------------------------------------


def test_criteria_covers_tool_selection(eval_def):
    criteria = eval_def["criteria"].lower()
    assert "tool" in criteria and ("select" in criteria or "appropriate" in criteria)


def test_criteria_covers_step_efficiency(eval_def):
    criteria = eval_def["criteria"].lower()
    assert "efficien" in criteria or "redundan" in criteria or "unnecessary" in criteria


def test_criteria_covers_data_flow(eval_def):
    criteria = eval_def["criteria"].lower()
    assert "data flow" in criteria or "inter-step" in criteria or "builds" in criteria


def test_criteria_has_anti_bias_rules(eval_def):
    criteria = eval_def["criteria"].lower()
    assert "anti-bias" in criteria or "multiple valid" in criteria


# ---------------------------------------------------------------------------
# Tags and visibility
# ---------------------------------------------------------------------------


def test_has_agents_tag(eval_def):
    assert "Agents" in eval_def.get("eval_tags", [])


def test_has_trajectory_tag(eval_def):
    assert "Trajectory" in eval_def.get("eval_tags", [])


def test_visible_in_ui(eval_def):
    assert eval_def.get("visible_ui") is True


def test_allow_copy_is_true(eval_def):
    assert eval_def["permissions"]["allow_copy"] is True
