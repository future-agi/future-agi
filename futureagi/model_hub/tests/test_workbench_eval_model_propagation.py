from types import SimpleNamespace

import pytest

from evaluations.engine.instance import resolve_binding_model
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate
from model_hub.utils.eval_list import _RUN_CONFIG_DEFAULTS, build_run_config_view


@pytest.mark.parametrize(
    "eval_config, template_config, expected",
    [
        (
            {"run_config": {"model": "claude-3-5-sonnet-latest"}},
            {"model": "turing_large"},
            "claude-3-5-sonnet-latest",
        ),
        ({"model": "gpt-4.1"}, {"model": "turing_large"}, "gpt-4.1"),
        (
            {"model": "gpt-4.1", "run_config": {"model": "claude-3-5"}},
            {"model": "turing_large"},
            "claude-3-5",
        ),
        ({}, {"model": "turing_large"}, "turing_large"),
        (None, {"model": "turing_large"}, "turing_large"),
        (
            {"run_config": {"model": ""}, "model": "gpt-4.1"},
            {"model": "turing_large"},
            "gpt-4.1",
        ),
        ({"model": ""}, {"model": "turing_large"}, "turing_large"),
        ({"run_config": None}, {"model": "turing_large"}, "turing_large"),
        ({}, {}, None),
    ],
    ids=[
        "run_config-nested-wins-alone",
        "top-level-model-when-no-nested",
        "run_config-nested-wins-over-top-level",
        "template-default-when-runtime-empty",
        "template-default-when-runtime-none",
        "empty-nested-falls-through-to-top-level",
        "empty-string-top-level-falls-through",
        "none-run-config-falls-through-to-top-level-or-template",
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


def _fake_binding(config=None, error_localizer=False):
    return SimpleNamespace(config=config, error_localizer=error_localizer)


def test_build_run_config_view_shape_defaults():
    result = build_run_config_view(_fake_binding())
    assert set(result.keys()) == set(_RUN_CONFIG_DEFAULTS)
    assert result["agent_mode"] == "agent"
    assert result["check_internet"] is False
    assert result["summary"] == "concise"
    assert result["pass_threshold"] == 0.5
    assert result["error_localizer_enabled"] is False
    assert result["data_injection"] == {}
    assert result["knowledge_bases"] == []
    assert result["tools"] == {}


def test_build_run_config_view_error_localizer_column_wins_over_json():
    binding = _fake_binding(
        config={"run_config": {"error_localizer_enabled": False}}, error_localizer=True
    )
    assert build_run_config_view(binding)["error_localizer_enabled"] is True


def test_build_run_config_view_error_localizer_falls_back_to_json_flag():
    binding = _fake_binding(
        config={"error_localizer_enabled": True}, error_localizer=False
    )
    assert build_run_config_view(binding)["error_localizer_enabled"] is True


def test_build_run_config_view_summary_dict_normalized_to_type_string():
    binding = _fake_binding(
        config={"run_config": {"summary": {"type": "detailed", "extra": 1}}}
    )
    assert build_run_config_view(binding)["summary"] == "detailed"


def test_build_run_config_view_summary_dict_without_type_falls_back():
    binding = _fake_binding(config={"run_config": {"summary": {"other": "value"}}})
    assert build_run_config_view(binding)["summary"] == "concise"


def test_build_run_config_view_reads_all_saved_keys():
    binding = _fake_binding(
        config={
            "run_config": {
                "agent_mode": "protect",
                "check_internet": True,
                "summary": "detailed",
                "pass_threshold": 0.75,
                "data_injection": {"full_row": True},
                "knowledge_bases": ["kb-1", "kb-2"],
                "tools": {"web": {"enabled": True}},
            }
        },
        error_localizer=True,
    )
    assert build_run_config_view(binding) == {
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
    binding = _fake_binding(config={"run_config": None})
    result = build_run_config_view(binding)
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
    assert set(row["run_config"].keys()) == set(_RUN_CONFIG_DEFAULTS)


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
