"""Cloud-only API -> worker setup -> RunPrompt provider-boundary regressions."""

import uuid
from unittest.mock import patch

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, OwnerChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.experiments import ExperimentPromptConfig
from model_hub.models.openai_tools import Tools
from model_hub.models.run_prompt import (
    PromptTemplate,
    PromptVersion,
    UserResponseSchema,
)
from model_hub.serializers.experiments import PromptModelParamsSerializer

pytestmark = pytest.mark.django_db(transaction=True)
CREATE_URL = "/model-hub/experiments/v2/"
SAVED_TOOL = {
    "name": "Look up weather",
    "description": "Weather for a city",
    "config": {
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        }
    },
}
TOOL_ENVELOPE = {
    "type": "function",
    "function": {
        "name": "Look_up_weather",
        "description": "Weather for a city",
        "parameters": SAVED_TOOL["config"]["parameters"],
    },
}


@pytest.fixture
def version_world(organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name=f"params-{uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
    )
    column = Column.no_workspace_objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=dataset, column=column, row=row, value="What is the weather?"
    )
    dataset.column_order = [str(column.id)]
    dataset.save(update_fields=["column_order"])
    template = EvalTemplate.no_workspace_objects.create(
        name=f"params-eval-{uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "required_keys": ["output"],
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
        },
        criteria="Is {{output}} correct?",
        model="turing_small",
        eval_type="code",
    )
    # Published evaluators have a version that native admission can pin.
    EvalTemplateVersion.objects.create_version(
        eval_template=template,
        config_snapshot=template.config,
        criteria=template.criteria,
        model=template.model,
        user=user,
        organization=organization,
        workspace=workspace,
    )
    prompt = PromptTemplate.no_workspace_objects.create(
        name=f"params-{uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    version = PromptVersion.no_workspace_objects.create(
        original_template=prompt,
        template_version="v1",
        is_default=True,
        is_draft=False,
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": "parameter regression"}],
            "configuration": {"temperature": 0, "max_tokens": 64},
        },
    )
    return dataset, prompt, version, template


def body(world, params, configuration=None):
    dataset, prompt, version, template = world
    return {
        "name": f"params-{uuid.uuid4().hex}",
        "dataset_id": str(dataset.id),
        "experiment_type": "llm",
        "user_eval_metrics": [
            {
                "template_id": str(template.id),
                "name": "scoped judge",
                "config": {"mapping": {"output": dataset.column_order[0]}},
                "model": "turing_small",
            }
        ],
        "prompt_config": [
            {
                "name": "saved version",
                "prompt_id": str(prompt.id),
                "prompt_version": str(version.id),
                "model": "gpt-4o-mini",
                "model_params": params,
                "configuration": configuration or {},
            }
        ],
    }


def create_run(auth_client, request_body):
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="params-test",
    ):
        response = auth_client.post(CREATE_URL, request_body, format="json")
    assert response.status_code == 200, response.content
    experiment_id = response.json()["result"].split("experiment_id=")[1].strip()
    record = ExperimentPromptConfig.objects.get(
        experiment_dataset__experiment_id=experiment_id
    )
    metric = record.experiment_dataset.experiment.user_eval_template_ids.get()
    assert (
        str(metric.template_id) == request_body["user_eval_metrics"][0]["template_id"]
    )
    assert (
        metric.organization_id
        == record.experiment_dataset.experiment.dataset.organization_id
    )
    return record


def setup_run(record):
    from tfc.temporal.experiments.activities import _setup_prompt_v2_sync

    with patch("tfc.temporal.experiments.activities.activity.logger"):
        setup = _setup_prompt_v2_sync(
            str(record.experiment_dataset.experiment_id),
            str(record.experiment_dataset.experiment.snapshot_dataset_id),
            str(record.id),
        )
    assert setup["row_ids"]
    record.refresh_from_db()
    return setup


def provider_payloads(record, setup):
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
    from model_hub.services.credit_ledger_port import PayerRoute
    from model_hub.views.experiment_runner import _process_row_impl

    received = []

    def provider_boundary(run, *args, **kwargs):
        # Real payload construction, no provider/key lookup, downloads or network.
        # Full HTTP STANDIN receipt assertions remain a separate E2E gate.
        received.append(run._create_payload("openai", "test-only-sentinel"))
        return "standin", {"data": {"response": "standin"}, "cost": 0}

    # Stub payer lookup, not policy/payload construction; forbid real dispatch.
    with (
        patch("model_hub.services.credit_ledger_port.resolve_payer_route",
              return_value=PayerRoute.byok("openai")),
        patch.object(RunPrompt, "litellm_response", provider_boundary),
        patch("litellm.completion", side_effect=AssertionError("Unexpected provider call")) as sync_call,
        patch("litellm.acompletion", side_effect=AssertionError("Unexpected provider call")) as async_call,
    ):
        _process_row_impl(
            row_id=setup["row_ids"][0],
            column_id=setup["column_id"],
            dataset_id=str(record.experiment_dataset.experiment.snapshot_dataset_id),
            experiment_id=str(record.experiment_dataset.experiment_id),
            messages=setup["messages"],
            model=setup["model_name"],
            model_config=setup["model_config"],
            output_format=setup["output_format"],
            run_prompt_config=setup["run_prompt_config"],
            skip_celery_evals=True,
        )
    sync_call.assert_not_called()
    async_call.assert_not_called()
    return received


@pytest.mark.parametrize(
    "params, expected",
    [
        ({}, {"temperature": 0, "max_tokens": 64}),
        (
            {"temperature": None, "maxTokens": None},
            {"temperature": 0, "max_tokens": 64},
        ),
        ({"maxTokens": 128}, {"temperature": 0, "max_tokens": 128}),
        (
            {"temperature": 0.4, "max_tokens": 32},
            {"temperature": 0.4, "max_tokens": 32},
        ),
    ],
)
def test_api_setup_provider_and_audit(auth_client, version_world, params, expected):
    record = create_run(auth_client, body(version_world, params))
    setup = setup_run(record)
    assert {key: record.model_params[key] for key in expected} == expected
    assert {key: record.configuration[key] for key in expected} == expected
    received = provider_payloads(record, setup)
    assert [{key: payload.get(key) for key in expected} for payload in received] == [
        expected
    ]


@pytest.mark.parametrize("saved_tool", [SAVED_TOOL, TOOL_ENVELOPE])
def test_saved_tools_reach_provider_and_survive_rerun(
    auth_client, version_world, saved_tool
):
    version = version_world[2]
    version.prompt_config_snapshot["configuration"].update(
        tools=[saved_tool], tool_choice="required"
    )
    version.save(update_fields=["prompt_config_snapshot"])
    record = create_run(
        auth_client, body(version_world, {}, {"tools": [], "tool_choice": "auto"})
    )
    for _ in range(2):
        setup = setup_run(record)
        received = provider_payloads(record, setup)
        assert len(received) == 1
        assert received[0]["tools"] == [TOOL_ENVELOPE]
        assert received[0]["tool_choice"] == "required"
        assert received[0]["temperature"] == 0
        assert received[0]["max_tokens"] == 64
        assert record.configuration["tools"] == [saved_tool]
        assert record.configuration["tool_choice"] == "required"


@pytest.mark.parametrize("scope", ["same", "organization", "workspace", "deleted"])
def test_request_tool_ids_are_scoped_and_override_saved_tools(
    auth_client, version_world, user, scope
):
    dataset, _prompt, version, _template = version_world
    version.prompt_config_snapshot["configuration"]["tools"] = [SAVED_TOOL]
    version.save(update_fields=["prompt_config_snapshot"])
    requested_tool = {**SAVED_TOOL, "name": "Explicit override"}
    tool = Tools.no_workspace_objects.create(
        **requested_tool, organization=dataset.organization, workspace=dataset.workspace
    )
    if scope == "organization":
        org = Organization.objects.create(name=f"foreign-{uuid.uuid4().hex}")
        Tools.no_workspace_objects.filter(id=tool.id).update(organization=org)
    elif scope == "workspace":
        other = Workspace.no_workspace_objects.create(
            name=f"foreign-{uuid.uuid4().hex}",
            organization=dataset.organization,
            created_by=user,
            is_default=False,
        )
        Tools.no_workspace_objects.filter(id=tool.id).update(workspace=other)
    elif scope == "deleted":
        Tools.no_workspace_objects.filter(id=tool.id).update(deleted=True)
    record = create_run(
        auth_client,
        body(version_world, {}, {"tools": [str(tool.id)], "tool_choice": "auto"}),
    )
    setup = setup_run(record)
    received = provider_payloads(record, setup)
    if scope == "same":
        assert len(received) == 1
        assert received[0]["tools"] == [
            {
                "type": "function",
                "function": {**TOOL_ENVELOPE["function"], "name": "Explicit_override"},
            }
        ]
        assert received[0]["tool_choice"] == "auto"
    else:
        assert received == []
        cell = Cell.objects.get(
            row_id=setup["row_ids"][0], column_id=setup["column_id"]
        )
        assert "Tool is not accessible" in cell.value


@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
def test_foreign_version_rejected_on_create_and_worker(
    auth_client, version_world, user, foreign_scope
):
    from tfc.temporal.experiments.activities import _setup_prompt_v2_sync

    dataset, prompt, _version, _template = version_world
    record = create_run(auth_client, body(version_world, {}))
    if foreign_scope == "organization":
        org = Organization.objects.create(name=f"foreign-{uuid.uuid4().hex}")
        PromptTemplate.no_workspace_objects.filter(id=prompt.id).update(
            organization=org
        )
    else:
        workspace = Workspace.no_workspace_objects.create(
            name=f"foreign-{uuid.uuid4().hex}",
            organization_id=dataset.organization_id,
            created_by=user,
            is_default=False,
        )
        PromptTemplate.no_workspace_objects.filter(id=prompt.id).update(
            workspace=workspace
        )
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start:
        response = auth_client.post(CREATE_URL, body(version_world, {}), format="json")
        assert response.status_code == 404, response.content
        assert response.json()["detail"] == "Prompt version not found"
        start.assert_not_called()
    with (
        patch("tfc.temporal.experiments.activities.activity.logger"),
        pytest.raises(ValueError, match="Prompt version is not accessible"),
    ):
        _setup_prompt_v2_sync(
            str(record.experiment_dataset.experiment_id),
            str(record.experiment_dataset.experiment.snapshot_dataset_id),
            str(record.id),
        )


def test_saved_sampling_reaches_provider(auth_client, version_world):
    """Sampling fallibility check runnable unchanged on the composed base."""
    record = create_run(auth_client, body(version_world, {}))
    received = provider_payloads(record, setup_run(record))
    assert len(received) == 1
    assert received[0].get("temperature") == 0
    assert received[0].get("max_tokens") == 64


@pytest.mark.parametrize("scope", ["same", "organization", "workspace", "deleted"])
def test_saved_response_schema_is_scoped(auth_client, version_world, user, scope):
    dataset, _prompt, version, _template = version_world
    schema = UserResponseSchema.no_workspace_objects.create(
        name=f"schema-{uuid.uuid4().hex}",
        organization=dataset.organization,
        workspace=dataset.workspace,
        schema={"type": "json_object"},
    )
    if scope == "organization":
        schema.organization = Organization.objects.create(
            name=f"foreign-{uuid.uuid4().hex}"
        )
    elif scope == "workspace":
        schema.workspace = Workspace.no_workspace_objects.create(
            name=f"foreign-{uuid.uuid4().hex}",
            organization=dataset.organization,
            created_by=user,
            is_default=False,
        )
    elif scope == "deleted":
        schema.deleted = True
    schema.save()
    version.prompt_config_snapshot["configuration"]["response_format"] = str(schema.id)
    version.save(update_fields=["prompt_config_snapshot"])
    record = create_run(auth_client, body(version_world, {}))
    setup = setup_run(record)
    received = provider_payloads(record, setup)
    if scope == "same":
        assert len(received) == 1
        assert received[0]["response_format"] == schema.schema
    else:
        assert received == []
        cell = Cell.objects.get(
            row_id=setup["row_ids"][0], column_id=setup["column_id"]
        )
        assert "Response schema is not accessible" in cell.value


def test_aliases_validate_before_use_and_nulls_are_unset():
    serializer = PromptModelParamsSerializer(
        data={"temperature": None, "maxTokens": 64, "topP": None}
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"max_tokens": 64}
    invalid = PromptModelParamsSerializer(data={"maxTokens": "not-an-integer"})
    assert not invalid.is_valid()
    assert "max_tokens" in invalid.errors
