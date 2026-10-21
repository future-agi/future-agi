"""The payment-agent evals load through the seeder and are offered where they work.

payment_agent_policy (219) is a judge the environments harness can offer: the
policy is the agent prompt, which every run has. payment_policy_adherence (220)
reads structured tool calls and takes its policy as settings, so its YAML does
not offer it to environments: the harness would fill ``output`` with transcript
text, which holds no tool calls. These tests run without a database.
"""

import re
from pathlib import Path

import pytest

from model_hub.management.commands.seed_system_evals import (
    _yaml_to_template_fields,
    load_yaml_evals,
)
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.function_eval_params import normalize_function_params
from simulate.services import harness_evals

JUDGE = "payment_agent_policy"
CODE = "payment_policy_adherence"
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


def test_both_load_with_their_ids(evals_by_name):
    assert evals_by_name[JUDGE]["eval_id"] == 219
    assert evals_by_name[CODE]["eval_id"] == 220


def test_judge_prompt_matches_declared_inputs(evals_by_name):
    e = evals_by_name[JUDGE]
    cfg = e["config"]
    assert cfg["eval_type_id"] == "AgentEvaluator"
    assert e["criteria"] == cfg["rule_prompt"]
    required = set(cfg["required_keys"])
    assert set(MUSTACHE.findall(cfg["rule_prompt"])) == required
    assert set(cfg.get("optional_keys") or []) <= required
    assert required <= set(cfg["config_params_desc"])
    assert required <= set(cfg["param_modalities"])


def test_judge_needs_the_policy(evals_by_name):
    # The agent prompt is the policy; without it the judge has nothing to apply.
    assert "agent_prompt" not in evals_by_name[JUDGE]["config"]["optional_keys"]


@pytest.mark.parametrize("name", [JUDGE, CODE])
def test_pass_fail_output_contract(evals_by_name, name):
    e = evals_by_name[name]
    assert e["config"]["output"] == "Pass/Fail"
    assert _yaml_to_template_fields(e)["output_type_normalized"] == "pass_fail"
    if name == JUDGE:
        assert e["choices"] == ["Passed", "Failed"]


@pytest.mark.parametrize("name", [JUDGE, CODE])
def test_every_tag_has_a_filter_chip(evals_by_name, name):
    chip_labels = set(
        re.findall(r'label: "([^"]+)"', CONSTANT_JS.read_text(encoding="utf-8"))
    )
    missing = set(evals_by_name[name]["eval_tags"]) - chip_labels
    assert not missing, f"{name}: tags without a filter chip: {missing}"


# --- Environments --------------------------------------------------------------


def test_only_the_judge_is_offered_to_environments(evals_by_name):
    assert evals_by_name[JUDGE]["offered_in_environments"] is True
    # Offered, the code eval would be handed transcript text and never find a payment.
    assert evals_by_name[CODE].get("offered_in_environments") is not True
    assert CODE not in harness_evals.offerable_eval_names()


def test_judge_is_offered_to_voice_and_chat_agents(evals_by_name):
    e = evals_by_name[JUDGE]
    template = EvalTemplate(
        name=JUDGE, config=e["config"], eval_tags=list(e["eval_tags"]), owner="system"
    )
    assert JUDGE in harness_evals.offerable_eval_names()
    for modality in ("voice", "text"):
        assert harness_evals._has_relevant_tag(template, modality)
        mapping = harness_evals.resolve_eval_mapping(template, modality)
        assert mapping is not None, f"not offered on {modality}"
        assert mapping["agent_prompt"] == "agent_prompt"


# --- Settings ------------------------------------------------------------------


def test_policy_settings_are_not_mapped_inputs(evals_by_name):
    cfg = evals_by_name[CODE]["config"]
    assert cfg["required_keys"] == ["output"]
    assert set(cfg["function_params_schema"]).isdisjoint(cfg["required_keys"])
    assert set(cfg["function_params_schema"]) <= set(cfg["config_params_desc"])


def test_settings_validate_through_the_platform(evals_by_name):
    cfg = evals_by_name[CODE]["config"]
    params = normalize_function_params(
        cfg, {"max_amount": "1000", "approved_vendors": "Northwind, Acme"}
    )
    assert params["max_amount"] == 1000.0
    assert params["approved_vendors"] == "Northwind, Acme"
    assert params["max_total"] is None
    assert params["currency"] is None
    assert params["payment_tools"].startswith("send_payment,")


@pytest.mark.parametrize(
    "params,message",
    [
        ({"approved_vendors": "Acme"}, "max_amount is required"),
        ({"max_amount": 10}, "approved_vendors is required"),
        ({"max_amount": -1, "approved_vendors": "Acme"}, "max_amount must be >= 0"),
        (
            {"max_amount": 1, "approved_vendors": "Acme", "max_total": -1},
            "max_total must be >= 0",
        ),
        (
            {"max_amount": 1, "approved_vendors": "Acme", "cap": 5},
            "Unknown function params: cap",
        ),
    ],
)
def test_bad_settings_are_refused_when_the_eval_is_configured(
    evals_by_name, params, message
):
    with pytest.raises(ValueError, match=message):
        normalize_function_params(evals_by_name[CODE]["config"], params)
