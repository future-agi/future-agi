import pytest
from rest_framework import status

from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.ai_model import AIModel
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project


@pytest.fixture
def rag_function_template(user, workspace):
    return EvalTemplate.objects.create(
        name="recall_at_k_test_template",
        description="Recall@K test template",
        owner=OwnerChoices.SYSTEM.value,
        eval_tags=["FUNCTION", "RAG"],
        config={
            "required_keys": ["hypothesis", "reference"],
            "output": "score",
            "eval_type_id": "RecallAtK",
            "function_eval": True,
            "function_params_schema": {
                "k": {
                    "type": "integer",
                    "default": None,
                    "nullable": True,
                    "minimum": 1,
                }
            },
            "config_params_desc": {
                "hypothesis": "Retrieved chunks",
                "reference": "Ground-truth chunks",
                "k": "Top K",
            },
        },
    )


@pytest.fixture
def dataset_for_eval(user, workspace):
    dataset = Dataset.objects.create(
        name="Function Param Dataset",
        organization=user.organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
    )

    hypothesis_col = Column.objects.create(
        dataset=dataset,
        name="hypothesis_col",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    reference_col = Column.objects.create(
        dataset=dataset,
        name="reference_col",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(hypothesis_col.id), str(reference_col.id)]
    dataset.save(update_fields=["column_order"])

    return dataset, hypothesis_col, reference_col


@pytest.fixture
def prompt_template(user, workspace):
    return PromptTemplate.objects.create(
        name="Prompt Template Params",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )


@pytest.fixture
def simulate_run_test(user, workspace):
    return RunTest.objects.create(
        name="Simulate Params",
        organization=user.organization,
        workspace=workspace,
    )


@pytest.fixture
def observe_project_for_eval(user, workspace):
    return Project.objects.create(
        name="Eval Task Params",
        organization=user.organization,
        workspace=workspace,
        user=user,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM.value,
        trace_type="observe",
    )


@pytest.fixture
def experiment_for_eval(dataset_for_eval):
    dataset, hypothesis_col, _reference_col = dataset_for_eval
    return ExperimentsTable.objects.create(
        name="Experiment Params",
        dataset=dataset,
        column=hypothesis_col,
        status=StatusType.COMPLETED.value,
    )


@pytest.mark.django_db
def test_prompt_eval_config_update_and_get_uses_config_params(
    auth_client, prompt_template, rag_function_template
):
    payload = {
        "id": str(rag_function_template.id),
        "name": "prompt_recall_k",
        "mapping": {
            "hypothesis": "retrieved_contexts",
            "reference": "ground_truth_contexts",
        },
        "config": {"params": {"k": 7}},
    }

    response = auth_client.post(
        f"/model-hub/prompt-templates/{prompt_template.id}/update-evaluation-configs/",
        payload,
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK

    stored = PromptEvalConfig.objects.get(
        prompt_template=prompt_template, name="prompt_recall_k", deleted=False
    )
    assert stored.config.get("params", {}).get("k") == 7
    assert stored.mapping == {
        "hypothesis": "retrieved_contexts",
        "reference": "ground_truth_contexts",
    }

    get_response = auth_client.get(
        f"/model-hub/prompt-templates/{prompt_template.id}/evaluation-configs/"
    )
    assert get_response.status_code == status.HTTP_200_OK
    result = get_response.json().get("result", {})
    evals = result.get("evaluation_configs", [])
    target = next(item for item in evals if item.get("name") == "prompt_recall_k")
    assert target.get("params", {}).get("k") == 7
    assert "k" in (target.get("function_params_schema") or {})


@pytest.mark.django_db
def test_apply_eval_group_dataset_propagates_shared_params(
    auth_client, user, workspace, rag_function_template, dataset_for_eval
):
    dataset, hypothesis_col, reference_col = dataset_for_eval
    eval_group = EvalGroup.objects.create(
        name="dataset_param_group",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    eval_group.eval_templates.add(rag_function_template)

    payload = {
        "eval_group_id": str(eval_group.id),
        "page_id": "DATASET",
        "filters": {"dataset_id": str(dataset.id), "model": "turing_small"},
        "mapping": {
            "hypothesis": str(hypothesis_col.id),
            "reference": str(reference_col.id),
        },
        "params": {"k": 3},
    }

    response = auth_client.post(
        "/model-hub/eval-groups/apply-eval-group/", payload, format="json"
    )
    assert response.status_code == status.HTTP_200_OK

    metric = UserEvalMetric.objects.get(
        eval_group=eval_group,
        dataset=dataset,
        template=rag_function_template,
        deleted=False,
    )
    assert metric.config.get("params", {}).get("k") == 3


@pytest.mark.django_db
def test_apply_eval_group_prompt_propagates_shared_params(
    auth_client, user, workspace, rag_function_template, prompt_template
):
    eval_group = EvalGroup.objects.create(
        name="prompt_param_group",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    eval_group.eval_templates.add(rag_function_template)

    payload = {
        "eval_group_id": str(eval_group.id),
        "page_id": "PROMPT",
        "filters": {"prompt_template_id": str(prompt_template.id)},
        "mapping": {
            "hypothesis": "retrieved_contexts",
            "reference": "ground_truth_contexts",
        },
        "params": {"k": 11},
    }

    response = auth_client.post(
        "/model-hub/eval-groups/apply-eval-group/", payload, format="json"
    )
    assert response.status_code == status.HTTP_200_OK

    prompt_eval = PromptEvalConfig.objects.get(
        eval_group=eval_group,
        prompt_template=prompt_template,
        eval_template=rag_function_template,
        deleted=False,
    )
    assert prompt_eval.config.get("params", {}).get("k") == 11


@pytest.mark.django_db
def test_apply_eval_group_simulate_propagates_shared_params(
    auth_client, user, workspace, rag_function_template, simulate_run_test
):
    eval_group = EvalGroup.objects.create(
        name="simulate_param_group",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    eval_group.eval_templates.add(rag_function_template)

    payload = {
        "eval_group_id": str(eval_group.id),
        "page_id": "SIMULATE",
        "filters": {
            "simulate_id": str(simulate_run_test.id),
            "model": "turing_small",
            "error_localizer": False,
        },
        "mapping": {
            "hypothesis": "retrieved_contexts",
            "reference": "ground_truth_contexts",
        },
        "params": {"k": 13},
    }

    response = auth_client.post(
        "/model-hub/eval-groups/apply-eval-group/", payload, format="json"
    )
    assert response.status_code == status.HTTP_200_OK

    simulate_eval = SimulateEvalConfig.objects.get(
        eval_group=eval_group,
        run_test=simulate_run_test,
        eval_template=rag_function_template,
        deleted=False,
    )
    assert simulate_eval.config.get("params", {}).get("k") == 13
    assert simulate_eval.mapping == {
        "hypothesis": "retrieved_contexts",
        "reference": "ground_truth_contexts",
    }
    assert simulate_eval.model == "turing_small"


@pytest.mark.django_db
def test_apply_eval_group_eval_task_propagates_shared_params(
    auth_client, user, workspace, rag_function_template, observe_project_for_eval
):
    eval_group = EvalGroup.objects.create(
        name="eval_task_param_group",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    eval_group.eval_templates.add(rag_function_template)

    payload = {
        "eval_group_id": str(eval_group.id),
        "page_id": "EVAL_TASK",
        "filters": {
            "project_id": str(observe_project_for_eval.id),
            "model": "turing_small",
            "error_localizer": False,
        },
        "mapping": {
            "hypothesis": "retrieved_contexts",
            "reference": "ground_truth_contexts",
        },
        "params": {"k": 23},
    }

    response = auth_client.post(
        "/model-hub/eval-groups/apply-eval-group/", payload, format="json"
    )
    assert response.status_code == status.HTTP_200_OK

    custom_eval = CustomEvalConfig.objects.get(
        eval_group=eval_group,
        project=observe_project_for_eval,
        eval_template=rag_function_template,
        deleted=False,
    )
    assert custom_eval.config.get("params", {}).get("k") == 23
    assert custom_eval.mapping == {
        "hypothesis": "retrieved_contexts",
        "reference": "ground_truth_contexts",
    }
    assert custom_eval.model == "turing_small"
    assert custom_eval.error_localizer is False


@pytest.mark.django_db
def test_apply_eval_group_eval_task_rejects_other_org_project(
    auth_client, user, workspace, rag_function_template
):
    other_org = Organization.objects.create(name="Other Eval Task Org")
    other_user = User.objects.create_user(
        email="other-eval-task-org@example.com",
        password="testpassword123",
        name="Other Eval Task User",
        organization=other_org,
    )
    other_workspace = Workspace.objects.create(
        name="Other Eval Task Workspace",
        organization=other_org,
        is_default=True,
        created_by=other_user,
    )
    other_project = Project.objects.create(
        name="Other Eval Task Project",
        organization=other_org,
        workspace=other_workspace,
        user=other_user,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM.value,
        trace_type="observe",
    )
    eval_group = EvalGroup.objects.create(
        name="eval_task_cross_org_group",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    eval_group.eval_templates.add(rag_function_template)

    response = auth_client.post(
        "/model-hub/eval-groups/apply-eval-group/",
        {
            "eval_group_id": str(eval_group.id),
            "page_id": "EVAL_TASK",
            "filters": {"project_id": str(other_project.id)},
            "mapping": {
                "hypothesis": "retrieved_contexts",
                "reference": "ground_truth_contexts",
            },
            "params": {"k": 29},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not CustomEvalConfig.no_workspace_objects.filter(
        eval_group=eval_group,
        project=other_project,
    ).exists()


@pytest.mark.django_db
def test_apply_eval_group_experiment_propagates_shared_params(
    auth_client,
    user,
    workspace,
    rag_function_template,
    dataset_for_eval,
    experiment_for_eval,
):
    dataset, hypothesis_col, reference_col = dataset_for_eval
    eval_group = EvalGroup.objects.create(
        name="experiment_param_group",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    eval_group.eval_templates.add(rag_function_template)

    payload = {
        "eval_group_id": str(eval_group.id),
        "page_id": "EXPERIMENT",
        "filters": {
            "experiment_id": str(experiment_for_eval.id),
            "model": "turing_small",
            "error_localizer": False,
        },
        "mapping": {
            "hypothesis": str(hypothesis_col.id),
            "reference": str(reference_col.id),
        },
        "params": {"k": 17},
    }

    response = auth_client.post(
        "/model-hub/eval-groups/apply-eval-group/", payload, format="json"
    )
    assert response.status_code == status.HTTP_200_OK

    experiment_eval = UserEvalMetric.objects.get(
        eval_group=eval_group,
        dataset=dataset,
        template=rag_function_template,
        source_id=str(experiment_for_eval.id),
        deleted=False,
    )
    assert experiment_eval.config.get("params", {}).get("k") == 17
    assert experiment_eval.status == StatusType.EXPERIMENT_EVALUATION.value
    assert experiment_eval.config.get("mapping") == {
        "hypothesis": str(hypothesis_col.id),
        "reference": str(reference_col.id),
    }
    assert experiment_for_eval.user_eval_template_ids.filter(
        id=experiment_eval.id
    ).exists()


@pytest.mark.django_db
def test_apply_eval_group_experiment_rejects_other_org_experiment(
    auth_client, user, workspace, rag_function_template
):
    other_org = Organization.objects.create(name="Other Eval Group Org")
    other_user = User.objects.create_user(
        email="other-eval-group-org@example.com",
        password="testpassword123",
        name="Other Eval Group User",
        organization=other_org,
    )
    other_workspace = Workspace.objects.create(
        name="Other Eval Group Workspace",
        organization=other_org,
        is_default=True,
        created_by=other_user,
    )
    other_dataset = Dataset.objects.create(
        name="Other Eval Group Dataset",
        organization=other_org,
        workspace=other_workspace,
        user=other_user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
    )
    other_column = Column.objects.create(
        dataset=other_dataset,
        name="other_output",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    other_experiment = ExperimentsTable.objects.create(
        name="Other Eval Group Experiment",
        dataset=other_dataset,
        column=other_column,
        status=StatusType.COMPLETED.value,
    )
    eval_group = EvalGroup.objects.create(
        name="experiment_cross_org_group",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    eval_group.eval_templates.add(rag_function_template)

    response = auth_client.post(
        "/model-hub/eval-groups/apply-eval-group/",
        {
            "eval_group_id": str(eval_group.id),
            "page_id": "EXPERIMENT",
            "filters": {"experiment_id": str(other_experiment.id)},
            "mapping": {"hypothesis": "other_output", "reference": "other_output"},
            "params": {"k": 19},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not UserEvalMetric.no_workspace_objects.filter(
        eval_group=eval_group,
        dataset=other_dataset,
        source_id=str(other_experiment.id),
    ).exists()


@pytest.mark.django_db
def test_fetch_eval_group_details_contains_function_param_requirements(
    auth_client, user, workspace, rag_function_template
):
    eval_group = EvalGroup.objects.create(
        name="group_requirements_check",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    eval_group.eval_templates.add(rag_function_template)

    response = auth_client.get(f"/model-hub/eval-groups/{eval_group.id}/")
    assert response.status_code == status.HTTP_200_OK

    result = response.json().get("result", {})
    requirements = result.get("function_params_requirements", {})
    assert "k" in requirements
    assert requirements["k"].get("schema", {}).get("type") == "integer"
    supported_by = requirements["k"].get("supported_by", [])
    assert any(
        item.get("eval_template_id") == str(rag_function_template.id)
        for item in supported_by
    )
