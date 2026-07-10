"""Workbench eval respects FE inputs + surfaces template metadata (TH-6725)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.choices import OwnerChoices
from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate


class _CapturedInstance:
    def __init__(self, **kwargs):
        self.received = kwargs


def _resolve_model_via_view_slice(evaluation, eval_template):
    """Mirror of the model-resolution slice inside
    PromptTemplateView.run_evaluation. Kept as a pure helper so the
    precedence order can be pinned without spinning up a full HTTP
    request.
    """
    runtime_config = evaluation.config or {}
    return (
        runtime_config.get("model")
        or (runtime_config.get("run_config") or {}).get("model")
        or eval_template.config.get("model")
    )


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
    ],
    ids=[
        "top-level-model-wins",
        "run_config-model-fallback",
        "template-default-when-runtime-empty",
        "template-default-when-runtime-none",
    ],
)
def test_model_resolution_precedence(eval_config, template_config, expected):
    evaluation = SimpleNamespace(config=eval_config)
    template = SimpleNamespace(config=template_config)
    assert _resolve_model_via_view_slice(evaluation, template) == expected


@pytest.mark.django_db
def test_evaluation_configs_endpoint_returns_template_id_and_eval_type(
    auth_client, user, workspace
):
    """FE reads `template_id` for the edit-drawer and `eval_type` for the
    badge; the endpoint used to omit both, so edits silently failed and
    every eval rendered as the fallback badge type."""
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
