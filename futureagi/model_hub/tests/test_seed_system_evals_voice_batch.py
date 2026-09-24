"""Voice-agent system evals (eval_id 209-218) load through the seeder with a
well-formed judge contract, and tag additions survive the catalog override.

The seeder replaces a YAML's ``eval_tags`` with the entry in
``evaluations/catalog/system_evals.yaml`` whenever the eval name is listed
there, so a tag added only to the YAML of a catalog-listed eval is silently
dropped at seed time. The override tests pin that the tags land in both.
"""

import re
from pathlib import Path

import pytest

from model_hub.management.commands.seed_system_evals import (
    _yaml_to_template_fields,
    load_yaml_evals,
)

NEW_EVALS = {
    209: "identity_verification_compliance",
    210: "action_confirmation_gating",
    211: "ai_disclosure_compliance",
    212: "unclear_audio_handling",
    213: "persona_consistency",
    214: "system_prompt_leakage",
    215: "jailbreak_resistance",
    216: "call_opening_handling",
    217: "knowledge_gap_handling",
    218: "conversational_naturalness",
}
MUSTACHE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
CONSTANT_JS = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "sections"
    / "evals"
    / "constant.js"
)


@pytest.fixture(scope="module")
def evals_by_name():
    return {e["name"]: e for e in load_yaml_evals()}


def test_new_evals_load_with_their_ids(evals_by_name):
    for eval_id, name in NEW_EVALS.items():
        assert name in evals_by_name, f"{name} not loaded by the seeder"
        assert evals_by_name[name]["eval_id"] == eval_id


def test_eval_ids_and_names_stay_unique():
    evals = load_yaml_evals()
    ids = [e["eval_id"] for e in evals]
    names = [e["name"] for e in evals]
    assert len(ids) == len(set(ids)), "duplicate eval_id in system_evals"
    assert len(names) == len(set(names)), "duplicate name in system_evals"


@pytest.mark.parametrize("name", sorted(NEW_EVALS.values()))
def test_judge_prompt_matches_declared_inputs(evals_by_name, name):
    e = evals_by_name[name]
    cfg = e["config"]
    assert cfg["eval_type_id"] == "AgentEvaluator"
    assert e["criteria"] == cfg["rule_prompt"]
    required = set(cfg["required_keys"])
    assert set(MUSTACHE.findall(cfg["rule_prompt"])) == required
    assert set(cfg.get("optional_keys") or []) <= required
    assert required <= set(cfg["config_params_desc"])
    assert required <= set(cfg["param_modalities"])


@pytest.mark.parametrize("name", sorted(NEW_EVALS.values()))
def test_output_contract(evals_by_name, name):
    e = evals_by_name[name]
    fields = _yaml_to_template_fields(e)
    if e["config"]["output"] == "Pass/Fail":
        assert e["choices"] == ["Passed", "Failed"]
        assert fields["output_type_normalized"] == "pass_fail"
    else:
        assert e["config"]["output"] == "choices"
        assert set(e["choice_scores"]) == set(e["choices"])
        assert fields["output_type_normalized"] == "deterministic"


@pytest.mark.parametrize(
    "name,tag",
    [
        ("ASR/STT_accuracy", "Voice"),
        ("TTS_accuracy", "Voice"),
        ("evaluate_function_calling", "Tools"),
        ("customer_agent_objection_handling", "Sales"),
        ("word_error_rate", "Voice"),
        ("no_misselling", "Compliance"),
        ("lead_qualification_completeness", "Sales"),
    ],
)
def test_tag_additions_survive_catalog_override(evals_by_name, name, tag):
    assert tag in evals_by_name[name]["eval_tags"]


def test_every_new_eval_tag_has_a_filter_chip(evals_by_name):
    chip_labels = set(re.findall(r'label: "([^"]+)"', CONSTANT_JS.read_text()))
    for name in NEW_EVALS.values():
        missing = set(evals_by_name[name]["eval_tags"]) - chip_labels
        assert not missing, f"{name}: tags without a filter chip: {missing}"
