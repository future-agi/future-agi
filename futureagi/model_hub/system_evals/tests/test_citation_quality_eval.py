"""
Tests for the citation_quality agent evaluator YAML.

Verifies:
- YAML schema (required fields, types)
- Template variables match required_keys
- eval_id uniqueness against known range
- PDF modality support (context can be a document)
- Tags, output type, and visibility
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from django.core.cache import cache

from model_hub.management.commands import seed_system_evals as seeder
from model_hub.models.evals_metric import EvalTemplate

YAML_PATH = Path(__file__).resolve().parent.parent / "agent" / "citation_quality.yaml"


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
    assert eval_def["name"] == "citation_quality"


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
    assert "context" in required_keys


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
# PDF modality — context accepts document sources
# ---------------------------------------------------------------------------


def test_context_supports_pdf_modality(eval_def):
    """Citation quality requires reading source docs — context must accept PDF."""
    context_modalities = eval_def["config"]["param_modalities"]["context"]
    assert "PDF" in context_modalities, "context must support PDF modality"


def test_context_supports_knowledge_base_modality(eval_def):
    context_modalities = eval_def["config"]["param_modalities"]["context"]
    assert "KNOWLEDGE_BASE" in context_modalities


# ---------------------------------------------------------------------------
# eval_id uniqueness guard
# ---------------------------------------------------------------------------


def test_allocated_eval_id(eval_def):
    assert eval_def["eval_id"] == 226


def test_no_other_eval_uses_same_eval_id(eval_def):
    catalog_dir = YAML_PATH.parent.parent
    our_id = eval_def["eval_id"]
    for path in catalog_dir.glob("*/*.yaml"):
        if path == YAML_PATH:
            continue
        other = yaml.safe_load(path.read_text())
        assert other.get("eval_id") != our_id, (
            f"eval_id {our_id} collision with {path.name}"
        )


@pytest.mark.django_db
def test_existing_catalog_adds_citation_judge_without_replacing_advice(monkeypatch):
    templates = EvalTemplate.no_workspace_objects
    version_key = "system_evals_version"
    previous_cache = cache.get(version_key)
    definitions = seeder.load_yaml_evals()
    previous_catalog = [row for row in definitions if row["name"] != "citation_quality"]
    try:
        # Seed the pre-citation catalog and its version just as on an installed DB.
        with monkeypatch.context() as previous_install:
            previous_install.setattr(
                seeder, "load_yaml_evals", lambda: previous_catalog
            )
            previous_install.setattr(seeder, "SYSTEM_EVALS_VERSION", 32)
            seeder.seed_evals(force=True)
        advice_before = templates.values().get(eval_id=203)
        assert advice_before["name"] == "advice_authority_boundary"
        assert not templates.filter(name="citation_quality").exists()

        assert seeder.seed_evals() == (1, 0, len(previous_catalog))
        assert templates.values().get(eval_id=203) == advice_before
        judge = templates.get(name="citation_quality", owner="system", visible_ui=True)
        assert not judge.allow_edit
        assert judge.config["rule_prompt"] == judge.criteria
        assert seeder.seed_evals() == (0, 0, 0)
    finally:
        if previous_cache is None:
            cache.delete(version_key)
        else:
            cache.set(version_key, previous_cache, timeout=None)


# ---------------------------------------------------------------------------
# Tags and visibility
# ---------------------------------------------------------------------------


def test_has_rag_tag(eval_def):
    assert "RAG" in eval_def.get("eval_tags", [])


def test_has_retrieval_systems_tag(eval_def):
    assert "Retrieval Systems" in eval_def.get("eval_tags", [])


def test_visible_in_ui(eval_def):
    assert eval_def.get("visible_ui") is True


def test_allow_copy_is_true(eval_def):
    assert eval_def["permissions"]["allow_copy"] is True


# ---------------------------------------------------------------------------
# Criteria sanity checks
# ---------------------------------------------------------------------------


def test_criteria_covers_citation_accuracy(eval_def):
    criteria = eval_def["criteria"].lower()
    assert "accuracy" in criteria or "correct" in criteria


def test_criteria_covers_fabricated_citations(eval_def):
    criteria = eval_def["criteria"].lower()
    assert "fabricat" in criteria or "non-existent" in criteria or "exist" in criteria


def test_criteria_covers_completeness(eval_def):
    criteria = eval_def["criteria"].lower()
    assert "complet" in criteria
