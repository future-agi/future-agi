"""The AI tool's Update links only eval configs of the task's own project.

Every read of an eval task's results finds its eval configs through the
config's project, so a task linked to another project's config spends
evaluations nobody can see. The tool resolves the task and checks the configs
through ``tracer.selectors.eval_tasks.scope``, the scope the eval-task
endpoints use, and a refusal writes nothing and starts no run.
"""

import pytest

from accounts.models import Organization
from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_eval_template, make_project
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.project import Project

CONFIG_NOT_ON_PROJECT = "CustomEvalConfig(s) not found on the task's project"


@pytest.fixture
def starts(monkeypatch):
    """Record workflow starts instead of reaching Temporal."""
    recorded = []
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        lambda task, **kwargs: recorded.append((str(task.id), kwargs)),
    )
    return recorded


@pytest.fixture
def soft_deletes(monkeypatch):
    """Record the fresh-run wipe of a task's live results."""
    recorded = []
    monkeypatch.setattr(
        "tracer.services.eval_tasks.entries.soft_delete_live",
        lambda task: recorded.append(str(task.id)),
    )
    return recorded


@pytest.fixture
def template(tool_context):
    return make_eval_template(tool_context)


@pytest.fixture
def update(django_capture_on_commit_callbacks):
    """Run the tool with its on-commit callbacks executed, as after a real
    commit, so a workflow start scheduled on any path reaches ``starts``."""

    def run(task, context, **params):
        with django_capture_on_commit_callbacks(execute=True):
            return run_tool(
                "update_eval_task",
                {"eval_task_id": str(task.id), "edit_type": "fresh_run", **params},
                context,
            )

    return run


def _config(project, template, name):
    return CustomEvalConfig.objects.create(
        name=name,
        project=project,
        eval_template=template,
        config={},
        mapping={},
        filters={},
    )


def _paused_task(project, config):
    task = EvalTask.objects.create(
        project=project,
        name="Paused Task",
        filters={},
        sampling_rate=1.0,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.PAUSED,
        spans_limit=100,
    )
    task.evals.add(config)
    return task


def _ids(configs):
    return [str(config.id) for config in configs]


def _linked_ids(task):
    return set(task.evals.values_list("id", flat=True))


def _task_not_found(task):
    return f"EvalTask with ID `{task.id}` was not found in this workspace"


def _assert_nothing_written(result, task, linked, starts, soft_deletes, refusal):
    assert result.is_error
    assert result.error_code == "NOT_FOUND"
    # Both guards answer NOT_FOUND; the text tells which one refused.
    assert refusal in result.content, result.content
    task.refresh_from_db()
    assert task.name == "Paused Task"
    assert task.status == EvalTaskStatus.PAUSED
    assert task.last_run is None
    assert _linked_ids(task) == linked
    assert starts == []
    assert soft_deletes == []


def _update_params(with_evals, configs):
    """A rename, with or without new eval configs. Without them the task
    lookup is the only guard against acting on another caller's task."""
    params = {"name": "Renamed Task"}
    if with_evals:
        params["evals"] = _ids(configs)
    return params


class TestUpdateEvalTaskScope:
    def test_a_config_of_the_tasks_own_project_is_linked(
        self, tool_context, template, starts, soft_deletes, update
    ):
        project = make_project(tool_context)
        task = _paused_task(project, _config(project, template, "Current"))
        replacement = _config(project, template, "Replacement")

        result = update(
            task, tool_context, edit_type="edit_rerun", evals=_ids([replacement])
        )

        assert not result.is_error, result.content
        task.refresh_from_db()
        assert task.status == EvalTaskStatus.PENDING
        assert _linked_ids(task) == {replacement.id}
        assert starts == [(str(task.id), {"replace_existing": True})]
        assert soft_deletes == []

    def test_a_config_of_another_project_in_the_workspace_is_refused(
        self, tool_context, template, starts, soft_deletes, update
    ):
        """Nothing is linked when any requested config is foreign, not even
        the requested configs that do belong to the task's project."""
        project = make_project(tool_context)
        current = _config(project, template, "Current")
        task = _paused_task(project, current)
        own = _config(project, template, "Own")
        sibling = make_project(tool_context, name="Sibling Project")
        foreign = _config(sibling, template, "Foreign")

        result = update(task, tool_context, evals=_ids([own, foreign]))

        _assert_nothing_written(
            result, task, {current.id}, starts, soft_deletes, CONFIG_NOT_ON_PROJECT
        )
        assert str(foreign.id) in result.content
        assert str(own.id) not in result.content

    def test_a_config_of_another_organization_is_refused(
        self, tool_context, template, starts, soft_deletes, update
    ):
        project = make_project(tool_context)
        current = _config(project, template, "Current")
        task = _paused_task(project, current)
        other_organization = Organization.objects.create(name="Other Organization")
        other_workspace = Workspace.objects.create(
            name="Other Organization Workspace",
            organization=other_organization,
            is_default=True,
            is_active=True,
            created_by=tool_context.user,
        )
        elsewhere = make_project(
            tool_context,
            name="Other Organization Project",
            organization=other_organization,
            workspace=other_workspace,
        )
        foreign = _config(elsewhere, template, "Foreign")

        result = update(task, tool_context, evals=_ids([foreign]))

        _assert_nothing_written(
            result, task, {current.id}, starts, soft_deletes, CONFIG_NOT_ON_PROJECT
        )

    @pytest.mark.parametrize(
        "with_evals", [False, True], ids=["rename_only", "with_evals"]
    )
    def test_a_task_in_another_workspace_is_not_found(
        self, tool_context, template, starts, soft_deletes, update, with_evals
    ):
        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=tool_context.organization,
            is_default=False,
            is_active=True,
            created_by=tool_context.user,
        )
        project = make_project(tool_context, workspace=other_workspace)
        current = _config(project, template, "Current")
        task = _paused_task(project, current)
        replacement = _config(project, template, "Replacement")

        result = update(task, tool_context, **_update_params(with_evals, [replacement]))

        _assert_nothing_written(
            result, task, {current.id}, starts, soft_deletes, _task_not_found(task)
        )

    @pytest.mark.parametrize(
        "with_evals", [False, True], ids=["rename_only", "with_evals"]
    )
    def test_a_task_whose_project_is_deleted_is_not_found(
        self, tool_context, template, starts, soft_deletes, update, with_evals
    ):
        project = make_project(tool_context)
        current = _config(project, template, "Current")
        task = _paused_task(project, current)
        replacement = _config(project, template, "Replacement")
        Project.all_objects.filter(id=project.id).update(deleted=True)

        result = update(task, tool_context, **_update_params(with_evals, [replacement]))

        _assert_nothing_written(
            result, task, {current.id}, starts, soft_deletes, _task_not_found(task)
        )

    def test_a_caller_without_a_workspace_is_scoped_by_organization(
        self, tool_context, template, starts, soft_deletes, update
    ):
        """A context without a workspace still refuses another project's
        config: the project rule does not depend on the workspace."""
        project = make_project(tool_context)
        current = _config(project, template, "Current")
        task = _paused_task(project, current)
        sibling = make_project(tool_context, name="Sibling Project")
        foreign = _config(sibling, template, "Foreign")
        unscoped = ToolContext(
            user=tool_context.user,
            organization=tool_context.organization,
            workspace=None,
        )

        result = update(task, unscoped, evals=_ids([foreign]))

        _assert_nothing_written(
            result, task, {current.id}, starts, soft_deletes, CONFIG_NOT_ON_PROJECT
        )
