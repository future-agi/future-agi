"""Feedback reruns use the evaluator's real signature and stay tenant-scoped."""

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import pytest
from django.utils import timezone

from accounts.models.user import User
from accounts.models.workspace import Workspace
from conftest import WorkspaceAwareAPIClient
from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate, Feedback
from tfc.utils.tests.two_tenants import make_tenant
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.utils import eval as eval_module
from tracer.views import observation_span as span_view

URL = "/tracer/observation-span/submit_feedback_action_type/"
SUBMIT_URL = "/tracer/observation-span/submit_feedback/"
# Where the span's project lives. A project with no workspace was made before
# workspaces existed and is shown in the organization's default workspace.
PLACEMENTS = ["no-workspace", "default-workspace", "non-default-workspace"]
pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]


@pytest.fixture
def tenants():
    owner = make_tenant("rerun-owner")
    other = make_tenant("rerun-other")
    yield owner, other
    owner.client.stop_workspace_injection()
    other.client.stop_workspace_injection()


@pytest.fixture
def evaluators(monkeypatch):
    # Autospec the underlying functions, not the decorator's (*args, **kwargs)
    # placeholder: a permissive mock would hide the four-vs-three argument bug.
    experiment = create_autospec(
        eval_module.evaluate_observation_span._original_func, return_value=True
    )
    observe = create_autospec(
        eval_module.evaluate_observation_span_observe._original_func,
        return_value=True,
    )
    monkeypatch.setattr(span_view, "evaluate_observation_span", experiment)
    monkeypatch.setattr(span_view, "evaluate_observation_span_observe", observe)
    monkeypatch.setattr(span_view, "get_mixpanel_properties", Mock(return_value={}))
    monkeypatch.setattr(span_view, "track_mixpanel_event", Mock())
    return experiment, observe


def _records(tenant, versioned, workspace=None):
    workspace = workspace or tenant.workspace
    # Unique per call: project names are unique per org/workspace/trace type,
    # and some tests build a second record set in the same workspace.
    project = Project.no_workspace_objects.create(
        name=f"Feedback rerun {uuid.uuid4().hex[:8]}",
        organization=tenant.organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="experiment" if versioned else "observe",
    )
    version = (
        ProjectVersion.objects.create(project=project, name="Run", version="v1")
        if versioned
        else None
    )
    trace = Trace.objects.create(project=project, project_version=version)
    span = ObservationSpan.objects.create(
        id=f"rerun-{uuid.uuid4().hex[:16]}",
        project=project,
        project_version=version,
        trace=trace,
        name="Feedback span",
        observation_type="llm",
        start_time=timezone.now(),
    )
    template = EvalTemplate.no_workspace_objects.create(
        name=f"feedback-{uuid.uuid4().hex[:8]}",
        organization=tenant.organization,
        workspace=workspace,
        config={"type": "pass_fail", "criteria": "Is the output correct?"},
    )
    config = CustomEvalConfig.objects.create(
        name="Feedback eval", project=project, eval_template=template, config={}
    )
    feedback = Feedback.objects.create(
        source="experiment" if versioned else "observe",
        source_id=span.id,
        value="true",
        user=tenant.user,
        eval_template=template,
        custom_eval_config_id=config.id,
        organization=tenant.organization,
        workspace=workspace,
    )
    log = EvalLogger.objects.create(
        observation_span=span,
        trace=trace,
        custom_eval_config=config,
        eval_task_id=str(uuid.uuid4()),
        output_bool=False,
    )
    return SimpleNamespace(span=span, config=config, feedback=feedback, log=log)


def _body(records):
    return {
        "observation_span_id": records.span.id,
        "custom_eval_config_id": str(records.config.id),
        "feedback_id": str(records.feedback.id),
        "action_type": "recalculate",
    }


@pytest.mark.parametrize("versioned", [True, False], ids=["experiment", "observe"])
def test_feedback_rerun_passes_evaluator_parameters(tenants, evaluators, versioned):
    owner, _ = tenants
    records = _records(owner, versioned)
    response = owner.client.post(URL, _body(records), format="json")

    assert response.status_code == 200, response.content
    assert response.json()["result"] == {
        "message": "Action type submitted successfully"
    }
    called, unused = evaluators if versioned else evaluators[::-1]
    called.assert_called_once()
    unused.assert_not_called()
    function = (
        eval_module.evaluate_observation_span
        if versioned
        else eval_module.evaluate_observation_span_observe
    )._original_func
    arguments = (
        inspect.signature(function)
        .bind(*called.call_args.args, **called.call_args.kwargs)
        .arguments
    )
    expected = {
        "observation_span_id": records.span.id,
        "custom_eval_config_id": str(records.config.id),
        "feedback_id": records.feedback.id,
    }
    if not versioned:
        expected["eval_task_id"] = records.log.eval_task_id
    assert arguments == expected
    records.feedback.refresh_from_db()
    assert records.feedback.action_type == "recalculate"
    records.log.refresh_from_db()
    assert records.log.deleted is True
    assert records.log.deleted_at is not None


@pytest.mark.parametrize("versioned", [True, False], ids=["experiment", "observe"])
def test_feedback_rerun_rejects_another_users_feedback(tenants, evaluators, versioned):
    owner, other = tenants
    records = _records(owner, versioned)
    body = {**_body(records), "value": "failed", "explanation": "Other user edit"}
    response = other.client.post(URL, body, format="json")

    assert response.status_code == 400, response.content
    for evaluator in evaluators:
        evaluator.assert_not_called()
    records.feedback.refresh_from_db()
    assert records.feedback.action_type is None
    assert records.feedback.value == "true"
    assert records.feedback.explanation is None
    records.log.refresh_from_db()
    assert records.log.deleted is False
    assert records.log.deleted_at is None


@pytest.mark.parametrize("versioned", [True, False], ids=["experiment", "observe"])
@pytest.mark.parametrize("boundary", ["organization", "workspace"])
@pytest.mark.parametrize("reference", ["span", "config"])
def test_feedback_rerun_rejects_out_of_scope_references(
    tenants, evaluators, versioned, boundary, reference
):
    owner, other = tenants
    local = _records(owner, versioned)
    if boundary == "workspace":
        workspace = Workspace.no_workspace_objects.create(
            name="Other workspace",
            organization=owner.organization,
            is_default=False,
            created_by=owner.user,
        )
        foreign = _records(owner, versioned, workspace)
    else:
        foreign = _records(other, versioned)
    body = {**_body(local), "value": "failed", "explanation": "Out of scope edit"}
    if reference == "span":
        # Keep the feedback owned by the caller and matched to the requested
        # span, so the request reaches the span's scope check, not the user check.
        local.feedback.source_id = foreign.span.id
        local.feedback.save(update_fields=["source_id"])
        body["observation_span_id"] = foreign.span.id
        body["custom_eval_config_id"] = str(foreign.config.id)
    else:
        body["custom_eval_config_id"] = str(foreign.config.id)
        # An existing result for this combination ensures a missing scope
        # predicate cannot pass the test merely because no eval result exists.
        EvalLogger.objects.create(
            observation_span=local.span,
            trace=local.span.trace,
            custom_eval_config=foreign.config,
            output_bool=False,
        )
    before = list(EvalLogger.objects.values_list("id", "deleted", "deleted_at"))

    response = owner.client.post(URL, body, format="json")

    assert response.status_code == 400, response.content
    for evaluator in evaluators:
        evaluator.assert_not_called()
    assert list(EvalLogger.objects.values_list("id", "deleted", "deleted_at")) == before
    local.feedback.refresh_from_db()
    assert local.feedback.value == "true"
    assert local.feedback.explanation is None
    assert local.feedback.action_type is None


@pytest.mark.parametrize("action_type", ["retune", "recalculate"])
@pytest.mark.parametrize(
    ("value", "stored_value"),
    [
        ("failed", "failed"),
        (0, "0"),
        ('["relevant","complete"]', '["relevant","complete"]'),
    ],
    ids=["verdict", "zero-score", "multiple-choices"],
)
def test_observe_feedback_edit_persists_before_action(
    tenants, evaluators, action_type, value, stored_value
):
    owner, _ = tenants
    records = _records(owner, versioned=False)
    body = {
        **_body(records),
        "action_type": action_type,
        "value": value,
        "explanation": "Corrected explanation",
    }

    def assert_saved_before_rerun(**_kwargs):
        records.feedback.refresh_from_db()
        assert records.feedback.value == stored_value
        assert records.feedback.explanation == "Corrected explanation"
        assert records.feedback.action_type == action_type
        return True

    evaluators[1].side_effect = assert_saved_before_rerun
    response = owner.client.post(URL, body, format="json")

    assert response.status_code == 200, response.content
    records.feedback.refresh_from_db()
    assert records.feedback.value == stored_value
    assert records.feedback.explanation == "Corrected explanation"
    assert records.feedback.action_type == action_type
    evaluators[0].assert_not_called()
    if action_type == "recalculate":
        evaluators[1].assert_called_once()
    else:
        evaluators[1].assert_not_called()


@pytest.mark.parametrize("action_type", ["retune", "recalculate"])
def test_observe_feedback_action_only_preserves_existing_content(
    tenants, evaluators, action_type
):
    owner, _ = tenants
    records = _records(owner, versioned=False)
    records.feedback.explanation = "Original explanation"
    records.feedback.save(update_fields=["explanation"])

    response = owner.client.post(
        URL, {**_body(records), "action_type": action_type}, format="json"
    )

    assert response.status_code == 200, response.content
    records.feedback.refresh_from_db()
    assert records.feedback.value == "true"
    assert records.feedback.explanation == "Original explanation"
    assert records.feedback.action_type == action_type


@pytest.mark.parametrize(
    "correction",
    [{"value": []}, {"explanation": "x" * 5001}],
    ids=["invalid-value", "overlong-explanation"],
)
def test_observe_feedback_edit_rejects_invalid_content(tenants, evaluators, correction):
    owner, _ = tenants
    records = _records(owner, versioned=False)

    response = owner.client.post(URL, {**_body(records), **correction}, format="json")

    assert response.status_code == 400, response.content
    records.feedback.refresh_from_db()
    assert records.feedback.value == "true"
    assert records.feedback.explanation is None
    assert records.feedback.action_type is None
    for evaluator in evaluators:
        evaluator.assert_not_called()
    records.log.refresh_from_db()
    assert records.log.deleted is False


@pytest.mark.parametrize("mismatch", ["config", "organization", "workspace"])
def test_observe_feedback_edit_rejects_mismatched_feedback(
    tenants, evaluators, mismatch
):
    owner, other = tenants
    records = _records(owner, versioned=False)
    body = {
        **_body(records),
        "action_type": "retune",
        "value": "failed",
        "explanation": "Mismatched feedback edit",
    }
    if mismatch == "config":
        body["custom_eval_config_id"] = str(_records(owner, versioned=False).config.id)
    else:
        setattr(records.feedback, mismatch, getattr(other, mismatch))
        records.feedback.save(update_fields=[mismatch])

    response = owner.client.post(URL, body, format="json")

    assert response.status_code == 400, response.content
    records.feedback.refresh_from_db()
    assert records.feedback.value == "true"
    assert records.feedback.explanation is None
    assert records.feedback.action_type is None
    for evaluator in evaluators:
        evaluator.assert_not_called()


@pytest.fixture
def embeddings(monkeypatch):
    # Saving feedback also sends the trace to the embedding store; keep that
    # call inside the test process.
    manager = Mock()
    monkeypatch.setattr(span_view, "EmbeddingManager", Mock(return_value=manager))
    return manager


def _non_default_workspace(tenant):
    return Workspace.no_workspace_objects.create(
        name=f"Team workspace {uuid.uuid4().hex[:8]}",
        organization=tenant.organization,
        is_default=False,
        created_by=tenant.user,
    )


@pytest.fixture
def saved_feedback(tenants, embeddings):
    """Builds eval feedback saved through submit_feedback, as the edit drawer
    saves it, for a project placed as one of PLACEMENTS.

    Requests run in the workspace that shows the project. The builder returns
    the records and the client bound to that workspace.
    """
    owner, _ = tenants
    extra_clients = []

    def build(placement):
        client, workspace = owner.client, owner.workspace
        if placement == "non-default-workspace":
            workspace = _non_default_workspace(owner)
            client = WorkspaceAwareAPIClient()
            client.force_authenticate(user=owner.user)
            client.set_workspace(workspace)
            extra_clients.append(client)
        records = _records(owner, versioned=False, workspace=workspace)
        # Only the feedback saved through the endpoint below is edited.
        Feedback.no_workspace_objects.filter(id=records.feedback.id).delete()
        if placement == "no-workspace":
            Project.no_workspace_objects.filter(id=records.span.project_id).update(
                workspace=None
            )

        response = client.post(
            SUBMIT_URL,
            {
                "observation_span_id": records.span.id,
                "custom_eval_config_id": str(records.config.id),
                "feedback_value": "true",
            },
            format="json",
        )

        assert response.status_code == 200, response.content
        records.feedback = Feedback.no_workspace_objects.get(
            id=response.json()["result"]["feedback_id"]
        )
        # Saved in the workspace the request ran in: for a project with no
        # workspace, the organization's default workspace.
        assert records.feedback.workspace_id == workspace.id
        return records, client

    yield build
    for client in extra_clients:
        client.stop_workspace_injection()


def _assert_feedback_unchanged(records):
    records.feedback.refresh_from_db()
    assert records.feedback.value == "true"
    assert records.feedback.explanation is None
    assert records.feedback.action_type is None


@pytest.mark.parametrize("placement", PLACEMENTS)
def test_editing_saved_feedback_saves_the_new_value(
    saved_feedback, evaluators, placement
):
    records, client = saved_feedback(placement)
    body = {**_body(records), "action_type": "retune", "value": "new"}

    response = client.post(URL, body, format="json")

    assert response.status_code == 200, response.content
    records.feedback.refresh_from_db()
    assert records.feedback.value == "new"
    assert records.feedback.action_type == "retune"
    assert records.feedback.explanation is None
    for evaluator in evaluators:
        evaluator.assert_not_called()


@pytest.mark.parametrize("placement", PLACEMENTS)
def test_editing_another_members_feedback_changes_nothing(
    tenants, saved_feedback, evaluators, placement
):
    owner, _ = tenants
    records, client = saved_feedback(placement)
    teammate = User.objects.create_user(
        email=f"teammate-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Teammate",
        organization=owner.organization,
        organization_role="Member",
    )
    Feedback.no_workspace_objects.filter(id=records.feedback.id).update(user=teammate)
    body = {**_body(records), "action_type": "retune", "value": "new"}

    response = client.post(URL, body, format="json")

    assert response.status_code == 400, response.content
    _assert_feedback_unchanged(records)
    assert records.feedback.user_id == teammate.id
    for evaluator in evaluators:
        evaluator.assert_not_called()


@pytest.mark.parametrize("placement", PLACEMENTS)
def test_feedback_in_another_non_default_workspace_is_not_found(
    tenants, saved_feedback, evaluators, placement
):
    owner, _ = tenants
    records, client = saved_feedback(placement)
    elsewhere = _non_default_workspace(owner)
    Feedback.no_workspace_objects.filter(id=records.feedback.id).update(
        workspace=elsewhere
    )
    body = {**_body(records), "action_type": "retune", "value": "new"}

    response = client.post(URL, body, format="json")

    assert response.status_code == 400, response.content
    _assert_feedback_unchanged(records)
    assert records.feedback.workspace_id == elsewhere.id
    for evaluator in evaluators:
        evaluator.assert_not_called()


@pytest.mark.parametrize("organization", ["same-organization", "other-organization"])
@pytest.mark.parametrize("placement", PLACEMENTS)
def test_feedback_with_no_workspace_is_edited_only_from_the_default_workspace(
    tenants, saved_feedback, evaluators, placement, organization
):
    owner, other = tenants
    records, client = saved_feedback(placement)
    # Feedback saved before workspaces existed has no workspace of its own.
    owning = other if organization == "other-organization" else owner
    Feedback.no_workspace_objects.filter(id=records.feedback.id).update(
        workspace=None, organization=owning.organization
    )
    body = {**_body(records), "action_type": "retune", "value": "new"}

    response = client.post(URL, body, format="json")

    if placement != "non-default-workspace" and owning is owner:
        assert response.status_code == 200, response.content
        records.feedback.refresh_from_db()
        assert records.feedback.value == "new"
        assert records.feedback.action_type == "retune"
        assert records.feedback.explanation is None
    else:
        assert response.status_code == 400, response.content
        _assert_feedback_unchanged(records)
    # An edit changes only the feedback fields, never where the row belongs.
    assert records.feedback.workspace_id is None
    assert records.feedback.organization_id == owning.organization.id
    for evaluator in evaluators:
        evaluator.assert_not_called()
