"""Voice-agent system evals (eval_id 209-218) load through the seeder with a
well-formed judge contract, and tag additions survive the catalog override.

A voice agent's conversation can arrive as audio or transcript; these evals use
the existing Audio chip rather than a separate Voice tag.

The seeder replaces a YAML's ``eval_tags`` with the entry in
``evaluations/catalog/system_evals.yaml`` whenever the eval name is listed
there, so a tag added only to the YAML of a catalog-listed eval is silently
dropped at seed time. The override tests pin that the tags land in both.

The ten are also catalog entries, which is what makes the environments harness
offer them; the last tests check that without a database.
"""

import re
from pathlib import Path

import pytest
import yaml

from model_hub.management.commands.seed_system_evals import (
    CATALOG_YAML,
    SYSTEM_EVALS_DIR,
    _yaml_to_template_fields,
    load_yaml_evals,
)
from model_hub.models.evals_metric import EvalTemplate
from simulate.services import harness_evals

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
        ("evaluate_function_calling", "Tools"),
        ("customer_agent_objection_handling", "Sales"),
        ("tool_call_accuracy", "Tools"),
    ],
)
def test_tag_additions_survive_catalog_override(evals_by_name, name, tag):
    assert tag in evals_by_name[name]["eval_tags"]


def test_every_new_eval_tag_has_a_filter_chip(evals_by_name):
    chip_labels = set(re.findall(r'label: "([^"]+)"', CONSTANT_JS.read_text()))
    for name in NEW_EVALS.values():
        missing = set(evals_by_name[name]["eval_tags"]) - chip_labels
        assert not missing, f"{name}: tags without a filter chip: {missing}"


# --- Environments: the catalog lists them, the harness offers them -------------


@pytest.fixture(scope="module")
def catalog():
    return yaml.safe_load(Path(CATALOG_YAML).read_text()) or {}


@pytest.mark.parametrize("name", sorted(NEW_EVALS.values()))
def test_catalog_entry_mirrors_the_legacy_yaml(catalog, name):
    """The catalog is what the environments harness offers from and where the
    seeder takes tags from, so each entry must match its legacy YAML."""
    legacy = yaml.safe_load(
        (Path(SYSTEM_EVALS_DIR) / "agent" / f"{name}.yaml").read_text()
    )
    entry = catalog[name]
    assert entry["eval_type"] == "agent"
    assert entry["output"] == legacy["config"]["output"]
    assert entry["required_keys"] == legacy["config"]["required_keys"]
    assert entry["tags"] == legacy["eval_tags"]
    assert entry["description"] == legacy["description"]
    assert entry["rule_prompt"] == legacy["config"]["rule_prompt"]


@pytest.mark.parametrize("name", sorted(NEW_EVALS.values()))
def test_offered_to_the_right_agent_kinds(evals_by_name, name):
    """Built from the seeded fields (catalog tags merged), without a database."""
    e = evals_by_name[name]
    template = EvalTemplate(
        name=name, config=e["config"], eval_tags=list(e["eval_tags"]), owner="system"
    )
    assert name in harness_evals.offerable_eval_names()
    for modality in ("voice", "text"):
        offered = (
            harness_evals._has_relevant_tag(template, modality)
            and harness_evals.resolve_eval_mapping(template, modality) is not None
        )
        # Audio-only: a chat run has no recording, so it is never offered there.
        expected = not (name == "unclear_audio_handling" and modality == "text")
        assert offered is expected, f"{name} on {modality}"


def test_consent_gets_recording_and_transcript_on_voice(evals_by_name):
    template = EvalTemplate(
        name="action_confirmation_gating",
        config=evals_by_name["action_confirmation_gating"]["config"],
    )
    mapping = harness_evals.resolve_eval_mapping(template, "voice")
    assert mapping["conversation"] == "voice_recording"
    assert mapping["transcript"] == "transcript"
