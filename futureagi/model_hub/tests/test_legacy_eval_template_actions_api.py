import uuid

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import DatasetSourceChoices, OwnerChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evaluation import Evaluation
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tracer.models.custom_eval_config import CustomEvalConfig, InlineEval
from tracer.models.external_eval_config import (
    ExternalEvalConfig,
    PlatformChoices,
    StatusChoices,
)
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace

try:
    from ee.usage.models.usage import APICallLog, APICallType
except ImportError:  # pragma: no cover - EE app is optional in OSS-only installs.
    APICallLog = None
    APICallType = None


def _same_org_other_workspace(organization, user):
    return Workspace.no_workspace_objects.create(
        name=f"Other Eval Template Workspace {uuid.uuid4()}",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


def _create_eval_template(
    organization,
    workspace,
    *,
    name,
    config=None,
    eval_tags=None,
    multi_choice=False,
    choices=None,
):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        description=f"{name} description",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_tags=eval_tags if eval_tags is not None else ["api-test", "legacy-action"],
        config=config
        if config is not None
        else {
            "required_keys": ["response"],
            "eval_type_id": "CustomCodeEval",
            "output": "Pass/Fail",
            "code": "def evaluate(response=None):\n    return True",
        },
        criteria="Return pass for {{response}}.",
        choices=choices,
        multi_choice=multi_choice,
        eval_type="code",
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _create_related_rows(template, organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name=f"Eval action dataset {uuid.uuid4()}",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="Eval action metric",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=dataset,
        config={"mapping": {"response": "response"}},
        status=StatusType.COMPLETED.value,
    )

    prompt_template = PromptTemplate.no_workspace_objects.create(
        name=f"Eval action prompt {uuid.uuid4()}",
        description="Prompt used by legacy eval action tests.",
        organization=organization,
        workspace=workspace,
        created_by=user,
        variable_names=["response"],
    )
    prompt_config = PromptEvalConfig.no_workspace_objects.create(
        name="Eval action prompt config",
        eval_template=template,
        prompt_template=prompt_template,
        user=user,
        mapping={"response": "response"},
    )

    project = Project.no_workspace_objects.create(
        name=f"Eval action project {uuid.uuid4()}",
        organization=organization,
        workspace=workspace,
        user=user,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    custom_config = CustomEvalConfig.no_workspace_objects.create(
        name=f"Eval action custom config {uuid.uuid4()}",
        project=project,
        eval_template=template,
        config={"threshold": 0.5},
        mapping={"response": "response"},
        filters={},
    )

    external_config = ExternalEvalConfig.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        eval_template=template,
        name=f"Eval action external config {uuid.uuid4()}",
        mapping={"response": "response"},
        model="turing_large",
        platform=PlatformChoices.LANGFUSE.value,
        credentials={
            "langfuse_secret_key": "secret",
            "langfuse_public_key": "public",
            "langfuse_host": "https://example.test",
        },
        status=StatusChoices.PENDING.value,
    )

    evaluation = Evaluation.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        eval_template=template,
        input_data={"response": "ok"},
    )
    inline_eval = InlineEval.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        span_id=f"span-{uuid.uuid4()}",
        custom_eval_name=template.name,
        evaluation=evaluation,
    )

    trace = Trace.no_workspace_objects.create(
        project=project,
        name=f"Eval action trace {uuid.uuid4()}",
    )
    span = ObservationSpan.no_workspace_objects.create(
        id=f"span-{uuid.uuid4()}",
        project=project,
        trace=trace,
        name="Eval action span",
        observation_type="llm",
    )
    eval_logger = EvalLogger.no_workspace_objects.create(
        trace=trace,
        observation_span=span,
        custom_eval_config=custom_config,
        target_type="span",
        output_bool=True,
    )

    api_log = None
    if APICallLog is not None and APICallType is not None:
        api_call_type, _ = APICallType.no_workspace_objects.get_or_create(
            name=APICallTypeChoices.DATASET_EVALUATION.value,
            defaults={"description": "Dataset Evaluation"},
        )
        api_log = APICallLog.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            user=user,
            api_call_type=api_call_type,
            cost=0,
            deducted_cost=0,
            status=APICallStatusChoices.SUCCESS.value,
            source="eval_template",
            source_id=str(template.id),
        )

    return {
        "metric": metric,
        "prompt_config": prompt_config,
        "custom_config": custom_config,
        "external_config": external_config,
        "inline_eval": inline_eval,
        "eval_logger": eval_logger,
        "api_log": api_log,
    }


def _assert_soft_deleted(model, obj):
    refreshed = model.all_objects.get(id=obj.id)
    assert refreshed.deleted is True
    assert refreshed.deleted_at is not None


@pytest.mark.django_db
def test_legacy_duplicate_eval_template_scopes_name_to_active_workspace(
    auth_client,
    organization,
    workspace,
    user,
):
    source = _create_eval_template(
        organization, workspace, name="legacy_duplicate_source"
    )
    other_workspace = _same_org_other_workspace(organization, user)
    other_workspace_duplicate_name = _create_eval_template(
        organization,
        other_workspace,
        name="legacy_duplicate_target",
    )

    response = auth_client.post(
        "/model-hub/duplicate-eval-template/",
        {
            "eval_template_id": str(source.id),
            "name": "legacy_duplicate_target",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.json()["result"]
    duplicated = EvalTemplate.no_workspace_objects.get(id=result["eval_template_id"])
    assert duplicated.name == "legacy_duplicate_target"
    assert duplicated.organization == organization
    assert duplicated.workspace == workspace
    assert duplicated.owner == OwnerChoices.USER.value
    assert duplicated.config == source.config
    assert duplicated.eval_tags == source.eval_tags
    assert duplicated.eval_type == source.eval_type
    assert duplicated.output_type_normalized == source.output_type_normalized

    other_workspace_duplicate_name.refresh_from_db()
    assert other_workspace_duplicate_name.deleted is False


@pytest.mark.django_db
def test_legacy_delete_eval_template_soft_deletes_related_rows(
    auth_client,
    organization,
    workspace,
    user,
):
    template = _create_eval_template(
        organization, workspace, name="legacy_delete_source"
    )
    related = _create_related_rows(template, organization, workspace, user)

    response = auth_client.post(
        "/model-hub/delete-eval-template/",
        {"eval_template_id": str(template.id)},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"] == "Evaluation template Deleted successfully"
    _assert_soft_deleted(EvalTemplate, template)
    _assert_soft_deleted(UserEvalMetric, related["metric"])
    _assert_soft_deleted(PromptEvalConfig, related["prompt_config"])
    _assert_soft_deleted(CustomEvalConfig, related["custom_config"])
    _assert_soft_deleted(ExternalEvalConfig, related["external_config"])
    _assert_soft_deleted(InlineEval, related["inline_eval"])
    _assert_soft_deleted(EvalLogger, related["eval_logger"])
    if related["api_log"] is not None:
        _assert_soft_deleted(APICallLog, related["api_log"])


@pytest.mark.django_db
def test_legacy_duplicate_and_delete_hide_same_org_other_workspace_template(
    auth_client,
    organization,
    workspace,
    user,
):
    other_workspace = _same_org_other_workspace(organization, user)
    other_template = _create_eval_template(
        organization,
        other_workspace,
        name="other_workspace_legacy_action_template",
    )

    duplicate_response = auth_client.post(
        "/model-hub/duplicate-eval-template/",
        {
            "eval_template_id": str(other_template.id),
            "name": "should_not_be_created",
        },
        format="json",
    )
    assert duplicate_response.status_code == status.HTTP_400_BAD_REQUEST

    delete_response = auth_client.post(
        "/model-hub/delete-eval-template/",
        {"eval_template_id": str(other_template.id)},
        format="json",
    )
    assert delete_response.status_code == status.HTTP_400_BAD_REQUEST

    other_template.refresh_from_db()
    assert other_template.deleted is False
    assert not EvalTemplate.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        name="should_not_be_created",
    ).exists()


@pytest.mark.django_db
def test_legacy_update_eval_template_allows_self_name_and_preserves_omitted_options(
    auth_client,
    organization,
    workspace,
):
    template = _create_eval_template(
        organization,
        workspace,
        name="legacy_update_self_name",
        eval_tags=["legacy", "preserve"],
        multi_choice=True,
        choices=["yes", "no"],
        config={
            "required_keys": ["response"],
            "check_internet": True,
            "choices_map": {"yes": "pass", "no": "fail"},
            "eval_type_id": "CustomCodeEval",
            "output": "choices",
        },
    )

    response = auth_client.post(
        "/model-hub/update-eval-template/",
        {
            "eval_template_id": str(template.id),
            "name": template.name,
            "description": "Updated without touching omitted options",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["result"] == "Evaluation template updated successfully"
    template.refresh_from_db()
    assert template.name == "legacy_update_self_name"
    assert template.description == "Updated without touching omitted options"
    assert template.multi_choice is True
    assert template.eval_tags == ["legacy", "preserve"]
    assert template.choices == ["yes", "no"]
    assert template.config["check_internet"] is True
    assert template.config["required_keys"] == ["response"]
    assert template.config["choices_map"] == {"yes": "pass", "no": "fail"}


@pytest.mark.django_db
def test_legacy_update_eval_template_can_clear_explicit_options(
    auth_client,
    organization,
    workspace,
):
    template = _create_eval_template(
        organization,
        workspace,
        name="legacy_update_clear_options",
        eval_tags=["legacy", "clear"],
        multi_choice=True,
        choices=["yes", "no"],
        config={
            "required_keys": ["response"],
            "check_internet": True,
            "choices_map": {"yes": "pass", "no": "fail"},
            "eval_type_id": "CustomCodeEval",
            "output": "choices",
        },
    )

    response = auth_client.post(
        "/model-hub/update-eval-template/",
        {
            "eval_template_id": str(template.id),
            "multi_choice": False,
            "check_internet": False,
            "eval_tags": [],
            "choices_map": {},
            "required_keys": [],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    template.refresh_from_db()
    assert template.multi_choice is False
    assert template.eval_tags == []
    assert template.choices == []
    assert template.config["check_internet"] is False
    assert template.config["required_keys"] == []
    assert template.config["choices_map"] == {}


@pytest.mark.django_db
def test_legacy_update_eval_template_scopes_name_to_active_workspace(
    auth_client,
    organization,
    workspace,
    user,
):
    source = _create_eval_template(organization, workspace, name="legacy_update_source")
    active_name_collision = _create_eval_template(
        organization,
        workspace,
        name="legacy_update_active_collision",
    )
    other_workspace = _same_org_other_workspace(organization, user)
    other_workspace_duplicate_name = _create_eval_template(
        organization,
        other_workspace,
        name="legacy_update_target",
    )

    blocked_response = auth_client.post(
        "/model-hub/update-eval-template/",
        {
            "eval_template_id": str(source.id),
            "name": active_name_collision.name,
        },
        format="json",
    )
    assert blocked_response.status_code == status.HTTP_400_BAD_REQUEST

    response = auth_client.post(
        "/model-hub/update-eval-template/",
        {
            "eval_template_id": str(source.id),
            "name": "legacy_update_target",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    source.refresh_from_db()
    assert source.name == "legacy_update_target"
    other_workspace_duplicate_name.refresh_from_db()
    assert other_workspace_duplicate_name.deleted is False


@pytest.mark.django_db
def test_legacy_update_eval_template_hides_same_org_other_workspace_template(
    auth_client,
    organization,
    workspace,
    user,
):
    other_workspace = _same_org_other_workspace(organization, user)
    other_template = _create_eval_template(
        organization,
        other_workspace,
        name="other_workspace_legacy_update_template",
    )

    response = auth_client.post(
        "/model-hub/update-eval-template/",
        {
            "eval_template_id": str(other_template.id),
            "description": "should not be applied",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    other_template.refresh_from_db()
    assert other_template.description != "should not be applied"
