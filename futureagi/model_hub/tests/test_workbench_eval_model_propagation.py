from types import SimpleNamespace

import pytest

from evaluations.engine.instance import resolve_binding_model
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate
from model_hub.utils.eval_list import _RUN_CONFIG_KEYS, build_run_config_view


@pytest.mark.parametrize(
    "eval_config, template_config, expected",
    [
        ({"model": "gpt-4.1"}, {"model": "turing_large"}, "gpt-4.1"),
        (
            {"run_config": {"model": "claude-3-5-sonnet-latest"}},
            {"model": "turing_large"},
            "claude-3-5-sonnet-latest",
        ),
        ({}, {"model": "turing_large"}, "turing_large"),
        (None, {"model": "turing_large"}, "turing_large"),
        (
            {"model": "gpt-4.1", "run_config": {"model": "claude-3-5"}},
            {"model": "turing_large"},
            "gpt-4.1",
        ),
        ({"model": ""}, {"model": "turing_large"}, "turing_large"),
        ({"run_config": None}, {"model": "turing_large"}, "turing_large"),
        ({}, {}, None),
    ],
    ids=[
        "top-level-model-wins",
        "run_config-model-fallback",
        "template-default-when-runtime-empty",
        "template-default-when-runtime-none",
        "top-level-wins-over-run-config-when-both-set",
        "empty-string-top-level-falls-through",
        "none-run-config-falls-through",
        "no-model-anywhere-returns-none",
    ],
)
def test_resolve_binding_model_precedence(eval_config, template_config, expected):
    template = SimpleNamespace(config=template_config)
    assert resolve_binding_model(eval_config, template) == expected


def test_resolve_binding_model_template_config_none():
    template = SimpleNamespace(config=None)
    assert resolve_binding_model({"model": "gpt-4.1"}, template) == "gpt-4.1"
    assert resolve_binding_model({}, template) is None


def test_build_run_config_view_shape():
    result = build_run_config_view(None)
    assert set(result.keys()) == set(_RUN_CONFIG_KEYS)
    assert result["agent_mode"] == "agent"
    assert result["check_internet"] is False
    assert result["summary"] == "concise"
    assert result["pass_threshold"] == 0.5
    assert result["error_localizer_enabled"] is False
    assert result["data_injection"] == {}
    assert result["knowledge_bases"] == []
    assert result["tools"] == {}


def test_build_run_config_view_error_localizer_from_column_not_json():
    binding = {"run_config": {"error_localizer_enabled": True}}
    assert build_run_config_view(binding, error_localizer_enabled=False)[
        "error_localizer_enabled"
    ] is False
    assert build_run_config_view(binding, error_localizer_enabled=True)[
        "error_localizer_enabled"
    ] is True


def test_build_run_config_view_summary_dict_normalized_to_type_string():
    binding = {"run_config": {"summary": {"type": "detailed", "extra": 1}}}
    assert build_run_config_view(binding)["summary"] == "detailed"


def test_build_run_config_view_summary_dict_without_type_falls_back():
    binding = {"run_config": {"summary": {"other": "value"}}}
    assert build_run_config_view(binding)["summary"] == "concise"


def test_build_run_config_view_reads_all_saved_keys():
    binding = {
        "run_config": {
            "agent_mode": "protect",
            "check_internet": True,
            "summary": "detailed",
            "pass_threshold": 0.75,
            "data_injection": {"full_row": True},
            "knowledge_bases": ["kb-1", "kb-2"],
            "tools": {"web": {"enabled": True}},
        }
    }
    result = build_run_config_view(binding, error_localizer_enabled=True)
    assert result == {
        "agent_mode": "protect",
        "check_internet": True,
        "summary": "detailed",
        "pass_threshold": 0.75,
        "error_localizer_enabled": True,
        "data_injection": {"full_row": True},
        "knowledge_bases": ["kb-1", "kb-2"],
        "tools": {"web": {"enabled": True}},
    }


def test_build_run_config_view_ignores_top_level_run_config_none():
    result = build_run_config_view({"run_config": None}, error_localizer_enabled=False)
    assert result["agent_mode"] == "agent"
    assert result["pass_threshold"] == 0.5


@pytest.mark.django_db
def test_evaluation_configs_endpoint_returns_template_id_and_eval_type(
    auth_client, user, workspace
):
    template = EvalTemplate.objects.create(
        name="workbench-fixture-llm",
        description="",
        owner=OwnerChoices.USER.value,
        organization=user.organization,
        workspace=workspace,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    PromptEvalConfig.objects.create(
        name="toxicity_binding",
        eval_template=template,
        prompt_template=prompt_template,
        mapping={"output": "model_output"},
        config={},
    )

    response = auth_client.get(
        f"/model-hub/prompt-templates/{prompt_template.id}/evaluation-configs/"
    )
    assert response.status_code == 200
    row = response.json()["result"]["evaluation_configs"][0]
    assert row["template_id"] == str(template.id)
    assert row["eval_type"] == "llm"


@pytest.mark.django_db
def test_evaluation_configs_endpoint_surfaces_run_config(
    auth_client, user, workspace
):
    template = EvalTemplate.objects.create(
        name="workbench-fixture-runtime",
        description="",
        owner=OwnerChoices.SYSTEM.value,
        organization=None,
        workspace=None,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    PromptEvalConfig.objects.create(
        name="toxicity_binding",
        eval_template=template,
        prompt_template=prompt_template,
        mapping={"output": "model_output"},
        config={
            "params": {},
            "run_config": {"model": "gpt-4.1", "agent_mode": "agent"},
        },
    )

    response = auth_client.get(
        f"/model-hub/prompt-templates/{prompt_template.id}/evaluation-configs/"
    )
    row = response.json()["result"]["evaluation_configs"][0]
    assert row["run_config"]["agent_mode"] == "agent"
    assert row["run_config"]["pass_threshold"] == 0.5
    assert set(row["run_config"].keys()) == set(_RUN_CONFIG_KEYS)


@pytest.mark.django_db
def test_evaluation_configs_endpoint_error_localizer_column_wins(
    auth_client, user, workspace
):
    template = EvalTemplate.objects.create(
        name="workbench-fixture-loc",
        description="",
        owner=OwnerChoices.SYSTEM.value,
        organization=None,
        workspace=None,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    PromptEvalConfig.objects.create(
        name="loc_binding",
        eval_template=template,
        prompt_template=prompt_template,
        mapping={"output": "model_output"},
        config={"run_config": {"error_localizer_enabled": False}},
        error_localizer=True,
    )

    response = auth_client.get(
        f"/model-hub/prompt-templates/{prompt_template.id}/evaluation-configs/"
    )
    row = response.json()["result"]["evaluation_configs"][0]
    assert row["run_config"]["error_localizer_enabled"] is True
