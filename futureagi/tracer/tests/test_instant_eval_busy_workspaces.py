"""An organization-wide run cap must not disclose another workspace's runs."""

import json
from datetime import UTC, datetime

import pytest
from django.test import override_settings

from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.ai_model import AIModel
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.instant_eval import InstantEvalRun
from tracer.models.project import Project
from tracer.services.instant_eval_rows import RUN_MARKER
from tracer.tests.test_instant_eval_insights_roles import _signed_in
from tracer.tests.test_instant_eval_scale import (
    CONTROL,
    _question,
    _start,
)
from tracer.tests.test_instant_eval_scale import (
    fake_view as fake_view,
)
from tracer.tests.test_instant_eval_scale import (
    instant_eval_enabled as instant_eval_enabled,
)
from tracer.views import instant_eval_scale as views

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_managed_activation(monkeypatch):
    monkeypatch.setattr("ee.licensing.activation_client._activate", lambda: None)


@pytest.fixture
def caller(organization, workspace):
    return _signed_in(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER,
        org_level=Level.MEMBER,
        ws_role=OrganizationRoles.WORKSPACE_MEMBER,
        ws_level=Level.WORKSPACE_MEMBER,
    )


@pytest.fixture
def private_project(organization, user):
    workspace = Workspace.objects.create(
        name="Private workspace",
        organization=organization,
        created_by=user,
    )
    return Project.objects.create(
        name="Private project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _run(project, user, question, *, status=EvalTaskStatus.RUNNING):
    task = EvalTask.objects.create(
        project=project,
        name=f"Instant eval · {question}",
        filters={RUN_MARKER: True},
        row_type=RowType.SESSIONS,
        run_type=RunType.HISTORICAL,
        status=status,
    )
    return InstantEvalRun.objects.create(
        eval_task=task,
        project=project,
        organization=project.organization,
        created_by=user,
        unit="sessions",
        row_type=RowType.SESSIONS,
        population=20,
        rows_requested=5,
        sample_size=5,
        date_from=datetime(2026, 10, 1, tzinfo=UTC),
        date_to=datetime(2026, 10, 15, tzinfo=UTC),
    )


def _assert_private_run_hidden(response, private, visible=()):
    assert response.status_code == 429, response.data
    body = response.json()["result"]
    assert body["error_code"] == "INSTANT_EVAL_BUSY"
    assert "2 Instant eval runs" in body["message"]
    assert {run["task_id"] for run in body["runs"]} == {
        str(run.eval_task_id) for run in visible
    }
    serialized = json.dumps(response.json())
    for run in private:
        assert str(run.eval_task_id) not in serialized
        assert str(run.project_id) not in serialized
        assert run.eval_task.name.removeprefix("Instant eval · ") not in serialized
        assert run.created_by.name not in serialized


@override_settings(INSTANT_EVAL_MAX_ACTIVE_PER_ORG=2)
@pytest.mark.parametrize("visible_count", [0, 1])
def test_start_counts_private_runs_without_disclosing_them(
    caller,
    observe_project,
    private_project,
    eval_template,
    user,
    monkeypatch,
    visible_count,
):
    question = _question(observe_project, eval_template, "My question")
    user.name = "Private runner"
    user.save(update_fields=["name"])
    private = [
        _run(private_project, user, f"Confidential question {i}")
        for i in range(2 - visible_count)
    ]
    visible = []
    if visible_count:
        from accounts.models.user import User

        member = User.objects.get(email__startswith="role-")
        visible = [_run(observe_project, member, "Visible question")]
    assert not WorkspaceMembership.no_workspace_objects.filter(
        workspace=private_project.workspace, user__email__startswith="role-"
    ).exists()
    monkeypatch.setattr(views, "_jev_key_missing", lambda *args: None)
    response = _start(caller, observe_project, [question], sample_size=5)
    _assert_private_run_hidden(response, private, visible)
    if visible:
        assert "Visible question" in response.json()["result"]["message"]
    assert InstantEvalRun.objects.count() == 2


@override_settings(INSTANT_EVAL_MAX_ACTIVE_PER_ORG=2)
def test_start_recheck_under_lock_redacts_private_runs(
    caller,
    observe_project,
    private_project,
    eval_template,
    user,
    fake_view,
    monkeypatch,
):
    question = _question(observe_project, eval_template, "My question")
    user.name = "Private runner"
    user.save(update_fields=["name"])
    private = [
        _run(private_project, user, f"Confidential question {i}") for i in range(2)
    ]
    original = views._active_runs
    calls = []

    def active_runs(org):
        calls.append(org.id)
        return [] if len(calls) == 1 else original(org)

    monkeypatch.setattr(views, "_active_runs", active_runs)
    monkeypatch.setattr(views, "_jev_key_missing", lambda *args: None)
    monkeypatch.setattr(views, "credits_per_answer", lambda: 0.06)
    monkeypatch.setattr(views, "check_credits", lambda *args: None)
    response = _start(caller, observe_project, [question], sample_size=5)
    _assert_private_run_hidden(response, private)
    assert len(calls) == 2
    assert InstantEvalRun.objects.count() == 2


@override_settings(INSTANT_EVAL_MAX_ACTIVE_PER_ORG=2)
@pytest.mark.parametrize("locked_recheck", [False, True])
def test_resume_redacts_private_runs_at_both_count_checks(
    caller,
    observe_project,
    private_project,
    user,
    monkeypatch,
    locked_recheck,
):
    paused = _run(
        observe_project, user, "My paused question", status=EvalTaskStatus.PAUSED
    )
    user.name = "Private runner"
    user.save(update_fields=["name"])
    private = [
        _run(private_project, user, f"Confidential question {i}") for i in range(2)
    ]
    original = views._active_runs
    calls = []

    def active_runs(org):
        calls.append(org.id)
        return [] if locked_recheck and len(calls) == 1 else original(org)

    monkeypatch.setattr(views, "_active_runs", active_runs)
    monkeypatch.setattr(views, "_jev_key_missing", lambda *args: None)
    monkeypatch.setattr(views, "credits_per_answer", lambda: 0.06)
    monkeypatch.setattr(views, "check_credits", lambda *args: None)
    response = caller.post(
        CONTROL,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(paused.eval_task_id),
            "action": "resume",
        },
        format="json",
    )
    _assert_private_run_hidden(response, private)
    paused.eval_task.refresh_from_db()
    assert paused.eval_task.status == EvalTaskStatus.PAUSED
    assert len(calls) == (2 if locked_recheck else 1)
