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

import json
import os
from copy import deepcopy
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
            previous_install.setattr(seeder, "SYSTEM_EVALS_VERSION", 33)
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


@pytest.fixture
def citation_template(db, eval_def, monkeypatch):
    # The general Django test settings enable EE. Exercise the OSS seed path.
    monkeypatch.setattr("tfc.ee_gating.is_oss", lambda: True)
    fields = seeder._yaml_to_template_fields(deepcopy(eval_def))
    template, _ = EvalTemplate.no_workspace_objects.update_or_create(
        eval_id=fields.pop("eval_id"), defaults=fields
    )
    return template


@pytest.fixture
def citation_judge(citation_template, monkeypatch):
    from evaluations.engine import instance
    from evaluations.engine.registry import get_eval_class

    monkeypatch.setattr(instance, "_get_api_key", lambda *args: "local-test-only")
    monkeypatch.setattr(
        "model_hub.utils.llm_providers.get_provider_for_model", lambda *args: "openai"
    )
    model = os.environ.get("CITATION_JUDGE_MODEL", "local-judge")
    judge_class = get_eval_class(citation_template.config["eval_type_id"])
    judge, _ = instance.create_eval_instance(
        judge_class, citation_template, model=f"openai/{model}"
    )
    judge.temperature = 0
    return judge


@pytest.mark.parametrize(
    "score, status", [(0.0, "failed"), (5 / 11, "failed"), (0.5, "completed")]
)
def test_standalone_saves_citation_score_status(
    citation_template, citation_judge, user, monkeypatch, score, status
):
    from sdk.utils.evaluations import _create_evaluation

    monkeypatch.setattr(
        citation_judge,
        "call_llm",
        lambda **kw: json.dumps({"result": score, "explanation": "Fixture score"}),
    )
    inputs = {
        "context": "[1] The lens was replaced May 7.",
        "input": "When? Cite the log.",
        "output": "May 7.",
    }
    result = citation_judge._evaluate(**inputs, required_keys=list(inputs))
    saved = _create_evaluation(
        citation_template, inputs, user, {}, result, False, False, None, None
    )
    saved.refresh_from_db()
    assert saved.status == status
    assert saved.data["result"] == score


def test_fractional_judge_prompt_allows_non_tenths(citation_judge, monkeypatch):
    prompts = []

    def capture(prompt, **kwargs):
        prompts.append(prompt)
        return json.dumps({"result": 5 / 11, "explanation": "Five of eleven"})

    monkeypatch.setattr(citation_judge, "call_llm", capture)
    # Reusing the judge must not put the default ten-step hint on the next user turn.
    for _ in range(2):
        citation_judge._evaluate(
            context="source",
            input="question",
            output="answer",
            required_keys=["context", "input", "output"],
        )
    for system, user in prompts:
        assert "increments of 0.1" not in system["content"]
        assert "0.0 0.1 0.2" not in user["content"]


@pytest.mark.parametrize(
    "eval_def",
    [yaml.safe_load(YAML_PATH.with_name("context_adherence.yaml").read_text())],
    ids=["context_adherence"],
)
def test_existing_numeric_judge_control(citation_judge, monkeypatch):
    """Non-regression control: a seeded numeric judge keeps its original contract."""
    prompts = []

    def capture(prompt, **kwargs):
        prompts.append(prompt)
        return json.dumps({"result": 0.3, "explanation": "Fixture score"})

    monkeypatch.setattr(citation_judge, "call_llm", capture)
    for _ in range(2):
        result = citation_judge._evaluate(
            context="source", output="answer", required_keys=["context", "output"]
        )
        assert result["failure"] is False
    assert len(prompts) == 2
    assert all("increments of 0.1" in system["content"] for system, _ in prompts)
    assert "0.0 0.1 0.2" in prompts[1][1]["content"]


@pytest.mark.live_llm
@pytest.mark.parametrize(
    "question, answer, expected",
    [
        ("When was the Alder lens replaced? Cite the log.", "May 7.", 0.0),
        ("Say hello.", "Hello!", 1.0),
        ("When was the Alder lens replaced?", "May 7 [1].", 1.0),
        ("When was the Alder lens replaced?", "May 7 [2].", 0.0),
        (
            "When were the three lenses replaced? Cite each log.",
            "Alder: May 7 [1]. Larch: July 9 [3]. Birch: June 3 [2].",
            1 / 3,
        ),
    ],
    ids=["missing", "not-needed", "matching", "swapped", "mixed"],
)
def test_citation_scores_with_local_judge(
    citation_judge, monkeypatch, question, answer, expected
):
    """Optional model-dependent check, not a CI gate or a guarantee for other judges."""
    if not os.environ.get("CITATION_JUDGE_MODEL"):
        pytest.skip("Set CITATION_JUDGE_MODEL to a locally served judge")
    port = int(os.environ.get("CITATION_JUDGE_PORT", "8080"))
    base = f"http://127.0.0.1:{port}/v1"
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    monkeypatch.setenv("OPENAI_API_BASE", base)
    monkeypatch.setenv("OPENAI_BASE_URL", base)
    result = citation_judge._evaluate(
        context=(
            "[1] Alder Observatory maintenance log: the lens was replaced on May 7.\n"
            "[2] Larch Observatory maintenance log: the lens was replaced on July 9.\n"
            "[3] Birch Observatory maintenance log: the lens was replaced on June 3."
        ),
        input=question,
        output=answer,
        required_keys=["context", "input", "output"],
    )
    assert result["data"]["result"] == pytest.approx(expected, abs=0.005)
    assert result["failure"] is (expected < 0.5)
