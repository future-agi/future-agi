"""W11: immutable creation lineage is independent of mutable worker links."""

from unittest.mock import Mock

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from simulate.models import (
    AgentOptimiser,
    AgentOptimiserRun,
    AgentPromptOptimiserRun,
    RunTest,
    TestExecution,
)
from simulate.serializers.agent_prompt_optimiser import (
    AgentPromptOptimiserRunSerializer,
)
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


def make_run(org, ws, label):
    optimiser = AgentOptimiser.no_workspace_objects.create(name=label)
    analysis = AgentOptimiserRun.no_workspace_objects.create(agent_optimiser=optimiser)
    owner = RunTest.no_workspace_objects.create(
        name=label, organization=org, workspace=ws
    )
    execution = TestExecution.no_workspace_objects.create(
        run_test=owner, agent_optimiser=optimiser
    )
    return AgentPromptOptimiserRun.no_workspace_objects.create(
        name=label,
        test_execution=execution,
        agent_optimiser=optimiser,
        agent_optimiser_run=analysis,
        optimiser_type="random_search",
        model="gpt-4o-mini",
        configuration={"num_variations": 1},
    )


@pytest.fixture
def runs(organization, workspace, user):
    clear_workspace_context()
    foreign_org = Organization.objects.create(name="optimizer foreign org")
    foreign_ws = Workspace.no_workspace_objects.create(
        name="optimizer foreign workspace", organization=foreign_org, created_by=user
    )
    sibling_ws = Workspace.no_workspace_objects.create(
        name="optimizer sibling workspace", organization=organization, created_by=user
    )
    return {
        "own": make_run(organization, workspace, "own"),
        "foreign": make_run(foreign_org, foreign_ws, "foreign"),
        "sibling": make_run(organization, sibling_ws, "sibling"),
    }


@pytest.mark.parametrize("method", ["put", "patch"])
@pytest.mark.parametrize(
    "field", ["test_execution", "agent_optimiser_run", "agent_optimiser"]
)
@pytest.mark.parametrize("target", ["foreign", "sibling"])
def test_update_refuses_repointing(auth_client, runs, method, field, target):
    own = runs["own"]
    old_id = getattr(own, field + "_id")
    body = dict(AgentPromptOptimiserRunSerializer(own).data) if method == "put" else {}
    body[field] = str(getattr(runs[target], field + "_id"))
    response = getattr(auth_client, method)(
        f"/simulate/api/agent-prompt-optimiser/{own.pk}/", body, format="json"
    )
    assert response.status_code == 400, response.data
    own.refresh_from_db()
    assert getattr(own, field + "_id") == old_id


@pytest.mark.parametrize(
    "field", ["test_execution", "agent_optimiser_run", "agent_optimiser", "all"]
)
@pytest.mark.parametrize("target", ["foreign", "sibling"])
def test_worker_rejects_tampered_links_before_reader(monkeypatch, runs, field, target):
    from simulate.tasks import agent_optimiser_tasks as tasks

    own = runs["own"]
    fields = (
        ("test_execution", "agent_optimiser_run", "agent_optimiser")
        if field == "all"
        else (field,)
    )
    AgentPromptOptimiserRun.no_workspace_objects.filter(pk=own.pk).update(
        **{key + "_id": getattr(runs[target], key + "_id") for key in fields}
    )
    reader = Mock()
    monkeypatch.setattr(tasks, "run_agent_prompt_optimiser", reader)
    try:
        tasks.run_agent_prompt_optimiser_task._original_func(str(own.pk))
    except ValueError as exc:
        assert "scope" in str(exc).lower()
    assert reader.call_count == 0, (
        "worker followed attacker-selected execution/analysis"
    )


@pytest.mark.parametrize(
    "entry",
    ["setup_run_activity", "run_optimization_activity", "finalize_run_activity"],
)
def test_temporal_workers_check_scope_before_data_reads(monkeypatch, runs, entry):
    from asgiref.sync import async_to_sync, sync_to_async

    from tfc.temporal.agent_prompt_optimiser import activities

    class NoHeartbeat:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(activities, "Heartbeater", NoHeartbeat)
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    monkeypatch.setattr(
        activities,
        "sync_to_async",
        lambda fn, **kwargs: sync_to_async(fn, thread_sensitive=True),
    )
    data_reader = Mock(return_value=None)
    result_reader = Mock(return_value=(None, None, None))
    monkeypatch.setattr(activities, "get_full_test_execution_data", data_reader)
    monkeypatch.setattr(activities, "_calc_best_from_trials_by_id", result_reader)
    own = runs["own"]
    AgentPromptOptimiserRun.no_workspace_objects.filter(pk=own.pk).update(
        test_execution_id=runs["foreign"].test_execution_id
    )
    try:
        async_to_sync(getattr(activities, entry))({"run_id": str(own.pk)})
    except ValueError:
        pass
    assert not data_reader.called and not result_reader.called
    own.refresh_from_db()
    assert own.status != "completed"


def test_same_tenant_worker_still_dispatches(monkeypatch, runs):
    from simulate.tasks import agent_optimiser_tasks as tasks

    reader = Mock()
    monkeypatch.setattr(tasks, "run_agent_prompt_optimiser", reader)
    tasks.run_agent_prompt_optimiser_task._original_func(str(runs["own"].pk))
    reader.assert_called_once_with(str(runs["own"].pk))


def test_read_only_lineage_preserves_ordinary_updates(runs):
    own = runs["own"]
    serializer = AgentPromptOptimiserRunSerializer(
        own, data={"model": "gpt-4o"}, partial=True
    )
    assert serializer.is_valid(), serializer.errors
    serializer.save()
    own.refresh_from_db()
    assert own.model == "gpt-4o"
    for field in ("test_execution", "agent_optimiser", "agent_optimiser_run"):
        assert serializer.fields[field].read_only


def test_unanchored_historical_run_fails_closed(monkeypatch, runs):
    from simulate.tasks import agent_optimiser_tasks as tasks

    own = runs["own"]
    # On the old implementation every run lacks provenance. On the fixed model
    # explicitly recreate this historical state without trusting its current FKs.
    if any(field.name == "tenant_lineage" for field in own._meta.fields):
        AgentPromptOptimiserRun.no_workspace_objects.filter(pk=own.pk).update(
            tenant_lineage=None
        )
    reader = Mock()
    monkeypatch.setattr(tasks, "run_agent_prompt_optimiser", reader)
    try:
        tasks.run_agent_prompt_optimiser_task._original_func(str(own.pk))
    except ValueError as exc:
        assert "scope" in str(exc).lower()
    assert reader.call_count == 0


SCOPE_FAILURE_MESSAGE = "Prompt optimization refused because its tenant scope could not be verified. Create a new run from an authorized execution."


@pytest.mark.parametrize("initial_status", ["pending", "running"])
@pytest.mark.parametrize(
    "entry",
    [
        "legacy",
        "direct",
        "setup_run_activity",
        "run_optimization_activity",
        "finalize_run_activity",
    ],
)
def test_historical_scope_refusal_is_terminal_and_value_free(
    monkeypatch, runs, initial_status, entry
):
    from asgiref.sync import async_to_sync, sync_to_async

    from simulate.tasks import agent_optimiser_tasks as tasks
    from simulate.utils.agent_prompt_optimiser import run_agent_prompt_optimiser
    from simulate.utils.prompt_optimizer_scope import PromptOptimiserScopeError
    from tfc.temporal.agent_prompt_optimiser import activities

    class NoHeartbeat:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(activities, "Heartbeater", NoHeartbeat)
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    monkeypatch.setattr(
        activities,
        "sync_to_async",
        lambda fn, **kwargs: sync_to_async(fn, thread_sensitive=True),
    )
    data_reader = Mock(side_effect=AssertionError("must not read execution data"))
    result_reader = Mock(side_effect=AssertionError("must not read trial results"))
    monkeypatch.setattr(tasks, "run_agent_prompt_optimiser", data_reader)
    monkeypatch.setattr(activities, "get_full_test_execution_data", data_reader)
    monkeypatch.setattr(activities, "_calc_best_from_trials_by_id", result_reader)
    own = runs["own"]
    AgentPromptOptimiserRun.no_workspace_objects.filter(pk=own.pk).update(
        tenant_lineage=None,
        status=initial_status,
        test_execution_id=runs["foreign"].test_execution_id,
        result={"saved": "history"},
        error_message="old error",
    )
    before = AgentPromptOptimiserRun.no_workspace_objects.get(pk=own.pk)
    before_values = dict(
        AgentPromptOptimiserRun.no_workspace_objects.filter(pk=own.pk).values().get()
    )
    foreign_values = dict(
        AgentPromptOptimiserRun.no_workspace_objects.filter(pk=runs["foreign"].pk)
        .values()
        .get()
    )
    with pytest.raises(PromptOptimiserScopeError):
        if entry == "legacy":
            tasks.run_agent_prompt_optimiser_task._original_func(str(own.pk))
        elif entry == "direct":
            run_agent_prompt_optimiser(str(own.pk))
        else:
            async_to_sync(getattr(activities, entry))(
                {"run_id": str(own.pk), "status": "failed", "error": "untrusted error"}
            )
    before.refresh_from_db()
    assert before.status == "failed"
    assert before.error_message == SCOPE_FAILURE_MESSAGE
    after_values = dict(
        AgentPromptOptimiserRun.no_workspace_objects.filter(pk=own.pk).values().get()
    )
    assert after_values == {
        **before_values,
        "status": "failed",
        "error_message": SCOPE_FAILURE_MESSAGE,
    }
    assert (
        AgentPromptOptimiserRun.no_workspace_objects.filter(pk=runs["foreign"].pk)
        .values()
        .get()
        == foreign_values
    )
    assert not data_reader.called and not result_reader.called


def test_scope_refusal_uses_write_alias_not_replica_object(monkeypatch, runs):
    from django.db import router
    from django.db.models.query import QuerySet

    from simulate.utils.prompt_optimizer_scope import (
        PromptOptimiserScopeError,
        load_scoped_prompt_run,
    )

    own = runs["own"]
    AgentPromptOptimiserRun.no_workspace_objects.filter(pk=own.pk).update(
        tenant_lineage=None
    )
    # A caller may hold stale replica state. Only its scalar ID may be passed on.
    own._state.db = "replica"
    own.name = "replica-only state must never be persisted"
    monkeypatch.setattr(router, "db_for_read", lambda *args, **kwargs: "replica")
    monkeypatch.setattr(router, "db_for_write", lambda *args, **kwargs: "default")
    monkeypatch.setattr(
        AgentPromptOptimiserRun,
        "save",
        Mock(side_effect=AssertionError("must not save a loaded object")),
    )
    original_update = QuerySet.update
    aliases = []

    def checked_update(queryset, **kwargs):
        if queryset.model is AgentPromptOptimiserRun:
            aliases.append(queryset.db)
            assert queryset.db == "default"
            assert kwargs == {
                "status": "failed",
                "error_message": SCOPE_FAILURE_MESSAGE,
            }
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "update", checked_update)
    with pytest.raises(PromptOptimiserScopeError):
        load_scoped_prompt_run(str(own.pk))
    stored = AgentPromptOptimiserRun.no_workspace_objects.using("default").get(
        pk=own.pk
    )
    assert aliases == ["default"]
    assert stored.status == "failed" and stored.error_message == SCOPE_FAILURE_MESSAGE
    assert stored.name == "own" and stored.tenant_lineage is None
    assert own._state.db == "replica" and own.status == "pending"


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_historical_terminal_history_is_preserved(monkeypatch, runs, terminal):
    from simulate.utils.prompt_optimizer_scope import (
        PromptOptimiserScopeError,
        load_scoped_prompt_run,
    )

    own = runs["own"]
    AgentPromptOptimiserRun.no_workspace_objects.filter(pk=own.pk).update(
        tenant_lineage=None,
        status=terminal,
        result={"saved": "history"},
        error_message="original",
    )
    before = (
        AgentPromptOptimiserRun.no_workspace_objects.filter(pk=own.pk).values().get()
    )
    with pytest.raises(PromptOptimiserScopeError):
        load_scoped_prompt_run(str(own.pk))
    assert (
        AgentPromptOptimiserRun.no_workspace_objects.filter(pk=own.pk).values().get()
        == before
    )
