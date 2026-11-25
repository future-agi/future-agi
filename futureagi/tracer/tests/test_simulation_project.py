"""The hidden simulation project of an agent definition."""

import inspect
import json
import threading
import uuid
from types import SimpleNamespace

import pytest
import structlog
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.db.migrations import AddField, AlterModelOptions
from django.db.migrations.loader import MigrationLoader
from django.db.models import CASCADE, OneToOneField
from django.test.utils import CaptureQueriesContext

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.management.commands import backfill_score_tracer_project
from model_hub.models.ai_model import AIModel
from simulate.models.agent_definition import AgentDefinition
from simulate.models.run_test import RunTest
from simulate.models.scenarios import Scenarios
from simulate.models.test_execution import CallExecution, TestExecution
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tracer.models.project import Project
from tracer.services import simulation_project
from tracer.services.simulation_project import (
    SIMULATION_PROJECT_PREFIX,
    SimulationProjectError,
    agent_id_in_simulation_project_name,
    agent_of_run,
    ensure_simulation_project,
    run_scope_project_ids,
    simulation_project_name,
    simulation_project_of,
    simulation_scope_for_run,
)
from tracer.utils.helper import get_default_project_version_config

pytestmark = pytest.mark.django_db

AGENT_ID = str(uuid.UUID("0c6f1a52-93b4-4c1e-8d27-5a3e9b7f4d10"))
RESERVED_NAME = SIMULATION_PROJECT_PREFIX + AGENT_ID
NOT_RESERVED_NAMES = [
    SIMULATION_PROJECT_PREFIX + AGENT_ID.upper(),
    SIMULATION_PROJECT_PREFIX + AGENT_ID.replace("-", ""),
    SIMULATION_PROJECT_PREFIX + "{" + AGENT_ID + "}",
    SIMULATION_PROJECT_PREFIX + "urn:uuid:" + AGENT_ID,
    SIMULATION_PROJECT_PREFIX + AGENT_ID[:-1],
    SIMULATION_PROJECT_PREFIX + AGENT_ID + "0",
    RESERVED_NAME + "\n",
    " " + RESERVED_NAME,
    RESERVED_NAME + " ",
    RESERVED_NAME + "-x",
    SIMULATION_PROJECT_PREFIX,
    "Fi-Simulation-" + AGENT_ID,
    "fi-simulation",
]

COLLECTORS_UPSERT = """
INSERT INTO tracer_project (id, name, organization_id, workspace_id, trace_type,
    model_type, source, tags, deleted, created_at, updated_at, simulation_agent_id)
SELECT %s, %s, a.organization_id, a.workspace_id, 'observe', 'GenerativeLLM',
    'simulator', '[]', false, now(), now(), a.id
FROM simulate_agent_definition a
WHERE a.id = %s AND a.deleted = false
ON CONFLICT (simulation_agent_id) DO UPDATE SET updated_at = EXCLUDED.updated_at
WHERE tracer_project.deleted = false
RETURNING id
"""


def _agent(organization, workspace, name="Simulated agent"):
    return AgentDefinition.objects.create(
        agent_name=name,
        description=f"{name} description",
        agent_type="text",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


def _agent_without_context(organization, workspace, name="Simulated agent"):
    clear_workspace_context()
    agent = _agent(organization, workspace, name)
    clear_workspace_context()
    return agent


def _collectors_upsert(agent):
    with connection.cursor() as cursor:
        cursor.execute(
            COLLECTORS_UPSERT,
            [uuid.uuid4(), simulation_project_name(agent.id), agent.id],
        )
        return cursor.fetchall()


def _row(project):
    return Project.with_simulation.filter(pk=project.pk).values().get()


def test_migration_adds_one_nullable_unique_column():
    field = Project._meta.get_field("simulation_agent")
    assert isinstance(field, OneToOneField)
    assert field.null is True
    assert field.unique is True
    assert field.remote_field.on_delete is CASCADE
    assert field.remote_field.related_name == "simulation_project"
    assert field.related_model is AgentDefinition

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'tracer_project' "
            "AND column_name = 'simulation_agent_id'"
        )
        assert cursor.fetchall() == [("uuid", "YES")]
        cursor.execute(
            "SELECT i.indpred IS NULL FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid "
            "AND a.attnum = ANY (i.indkey) "
            "WHERE i.indrelid = 'tracer_project'::regclass "
            "AND a.attname = 'simulation_agent_id' "
            "AND i.indisunique AND i.indnatts = 1"
        )
        assert cursor.fetchall() == [(True,)]

    graph = MigrationLoader(connection).graph
    migration = graph.nodes[("tracer", "0115_project_simulation_agent")]
    assert [type(operation) for operation in migration.operations] == [
        AlterModelOptions,
        AddField,
    ]
    assert ("simulate", "0097_merge_selected_runs_environment_v3") in (
        migration.dependencies
    )


def test_score_backfill_that_runs_before_0115_never_names_the_column(
    organization, workspace
):
    plan = MigrationLoader(connection).graph.forwards_plan(
        ("tracer", "0115_project_simulation_agent")
    )
    assert ("model_hub", "0120_backfill_score_tracer_project") in plan
    visible = Project.objects.create(
        name="A visible project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    # Any queryset with organization_id stands in for the pending scores.
    base = Project._base_manager.filter(pk=visible.pk)

    for only_project in (str(visible.id), None):
        with CaptureQueriesContext(connection) as queries:
            projects, _ = backfill_score_tracer_project._projects_for_pending_scores(
                base, only_project
            )
        assert str(visible.id) in {ref.project_id for ref in projects}
        assert queries.captured_queries
        for query in queries.captured_queries:
            assert "simulation_agent" not in query["sql"]


def test_hard_delete_of_the_agent_removes_the_project(organization, workspace):
    agent = _agent(organization, workspace)
    project = Project.with_simulation.create(
        name=simulation_project_name(agent.id),
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        simulation_agent=agent,
    )
    assert Project.with_simulation.filter(pk=project.pk).exists()

    AgentDefinition._base_manager.filter(pk=agent.pk).delete()

    assert not Project.with_simulation.filter(pk=project.pk).exists()


def test_collectors_upsert_statement_runs_against_the_column(organization, workspace):
    agent = _agent(organization, workspace)

    [(project_id,)] = _collectors_upsert(agent)
    assert Project.with_simulation.filter(simulation_agent_id=agent.id).count() == 1
    assert not Project.objects.filter(pk=project_id).exists()

    assert _collectors_upsert(agent) == [(project_id,)]
    assert Project.with_simulation.filter(simulation_agent_id=agent.id).count() == 1

    Project.with_simulation.filter(pk=project_id).update(deleted=True)
    assert _collectors_upsert(agent) == []

    other = _agent(organization, workspace, "Another agent")
    AgentDefinition._base_manager.filter(pk=other.pk).update(deleted=True)
    assert _collectors_upsert(other) == []
    assert not Project.with_simulation.filter(simulation_agent_id=other.id).exists()


@pytest.mark.django_db(transaction=True)
def test_ensure_is_idempotent_and_race_safe(organization, workspace, monkeypatch):
    agent = _agent_without_context(organization, workspace)

    with structlog.testing.capture_logs() as events:
        first = ensure_simulation_project(agent)
        second = ensure_simulation_project(agent)

    assert first.id == second.id
    assert Project.with_simulation.filter(simulation_agent_id=agent.id).count() == 1
    [event] = [e for e in events if e["event"] == "simulation_project_created"]
    assert set(event) == {
        "event",
        "log_level",
        "agent_definition_id",
        "project_id",
        "by",
    }
    assert event["agent_definition_id"] == str(agent.id)
    assert event["project_id"] == str(first.id)
    assert event["by"] == "platform"

    racing_agent = _agent_without_context(organization, workspace, "Racing agent")
    barrier = threading.Barrier(2, timeout=10)
    save = Project.save

    def save_when_both_missed(self, *args, **kwargs):
        if self.simulation_agent_id == racing_agent.id:
            barrier.wait()
        return save(self, *args, **kwargs)

    monkeypatch.setattr(Project, "save", save_when_both_missed)
    project_ids = []
    errors = []

    def ensure():
        try:
            project_ids.append(ensure_simulation_project(racing_agent).id)
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    with structlog.testing.capture_logs() as race_events:
        threads = [threading.Thread(target=ensure) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert errors == []
    assert len(project_ids) == 2
    assert project_ids[0] == project_ids[1]
    assert (
        Project.with_simulation.filter(simulation_agent_id=racing_agent.id).count() == 1
    )
    assert [e["event"] for e in race_events].count("simulation_project_created") == 1


def test_name_workspace_type_source_and_no_user(organization, workspace):
    agent = _agent_without_context(organization, workspace)

    project = ensure_simulation_project(agent)

    project = Project.with_simulation.get(pk=project.pk)
    expected_name = "fi-simulation-" + str(agent.id)
    assert project.name == expected_name
    assert len(project.name) == 50
    assert simulation_project_name(str(agent.id).upper()) == expected_name
    assert project.trace_type == "observe"
    assert project.source == "simulator"
    assert project.model_type == "GenerativeLLM"
    default_config = json.loads(json.dumps(get_default_project_version_config()))
    assert project.config == default_config
    assert project.user_id is None
    assert project.deleted is False
    assert project.simulation_agent_id == agent.id
    assert project.organization_id == organization.id
    assert project.workspace_id == workspace.id


def test_deleted_agent_has_no_project_and_raises(organization, workspace):
    with pytest.raises(SimulationProjectError):
        ensure_simulation_project(None)

    deleted_agent = _agent_without_context(organization, workspace, "Deleted agent")
    deleted_agent.delete()
    with pytest.raises(SimulationProjectError):
        ensure_simulation_project(deleted_agent)
    assert not Project.with_simulation.filter(
        simulation_agent_id=deleted_agent.id
    ).exists()

    stale_agent = _agent_without_context(organization, workspace, "Stale agent")
    AgentDefinition._base_manager.filter(pk=stale_agent.pk).update(deleted=True)
    assert stale_agent.deleted is False
    with pytest.raises(SimulationProjectError):
        ensure_simulation_project(stale_agent)
    assert not Project.with_simulation.filter(
        simulation_agent_id=stale_agent.id
    ).exists()

    agent = _agent_without_context(organization, workspace, "Agent with a project")
    project = ensure_simulation_project(agent)
    before = _row(project)
    AgentDefinition._base_manager.filter(pk=agent.pk).update(deleted=True)
    with pytest.raises(SimulationProjectError):
        ensure_simulation_project(agent)
    assert _row(project) == before


def test_agent_without_workspace_gets_the_default(organization, user, workspace):
    first = Workspace.objects.create(
        name="First", organization=organization, created_by=user
    )
    second = Workspace.objects.create(
        name="Second", organization=organization, created_by=user
    )

    # (a) no workspace: the organization's default
    agent = _agent_without_context(organization, None, "No workspace")
    agent.refresh_from_db()
    assert agent.workspace_id is None
    project = ensure_simulation_project(agent)
    assert project.workspace_id == workspace.id

    # (b) its own non-default workspace
    agent = _agent_without_context(organization, first, "Own workspace")
    project = ensure_simulation_project(agent)
    assert project.workspace_id == first.id

    # (c) the ambient workspace is neither of them
    agent = _agent_without_context(organization, None, "Ambient, no workspace")
    agent.refresh_from_db()
    assert agent.workspace_id is None
    set_workspace_context(workspace=second, organization=organization)
    project = ensure_simulation_project(agent)
    assert project.workspace_id == workspace.id
    clear_workspace_context()

    agent = _agent_without_context(organization, first, "Ambient, own workspace")
    set_workspace_context(workspace=second, organization=organization)
    project = ensure_simulation_project(agent)
    assert project.workspace_id == first.id
    clear_workspace_context()

    # (d) an inactive default workspace is not a fallback, but it is the agent's own
    Workspace.objects.filter(pk=workspace.pk).update(is_active=False)
    agent = _agent_without_context(organization, None, "Inactive default")
    agent.refresh_from_db()
    assert agent.workspace_id is None
    with pytest.raises(SimulationProjectError):
        ensure_simulation_project(agent)
    assert not Project.with_simulation.filter(simulation_agent_id=agent.id).exists()

    agent = _agent_without_context(organization, workspace, "In the inactive default")
    project = ensure_simulation_project(agent)
    assert project.workspace_id == workspace.id

    # a soft-deleted default workspace is not a fallback either
    Workspace.objects.filter(pk=workspace.pk).update(is_active=True, deleted=True)
    agent = _agent_without_context(organization, None, "Deleted default")
    agent.refresh_from_db()
    assert agent.workspace_id is None
    with pytest.raises(SimulationProjectError):
        ensure_simulation_project(agent)
    assert not Project.with_simulation.filter(simulation_agent_id=agent.id).exists()

    # (e) never without a workspace
    assert not Project.with_simulation.filter(
        organization=organization, workspace__isnull=True
    ).exists()


def test_agent_of_run_is_the_executions_agent_else_the_run_tests(
    organization, workspace
):
    clear_workspace_context()
    run_agent = _agent(organization, workspace, "Run agent")
    execution_agent = _agent(organization, workspace, "Execution agent")

    def execution(run_test_agent, agent):
        run = RunTest.objects.create(
            name="Run",
            organization=organization,
            workspace=workspace,
            agent_definition=run_test_agent,
        )
        created = TestExecution.objects.create(
            run_test=run,
            status=TestExecution.ExecutionStatus.COMPLETED,
            agent_definition=agent,
        )
        return TestExecution._base_manager.get(pk=created.pk)

    assert agent_of_run(execution(None, execution_agent)) == execution_agent
    assert agent_of_run(execution(run_agent, None)) == run_agent
    assert agent_of_run(execution(run_agent, execution_agent)) == execution_agent
    assert agent_of_run(execution(None, None)) is None

    execution_agent.delete()
    returned = agent_of_run(execution(run_agent, execution_agent))
    assert returned == execution_agent
    assert returned.deleted is True


def test_public_signatures_and_constants():
    assert list(inspect.signature(simulation_project_name).parameters) == [
        "agent_definition_id"
    ]
    assert list(inspect.signature(agent_id_in_simulation_project_name).parameters) == [
        "name"
    ]
    assert list(inspect.signature(ensure_simulation_project).parameters) == [
        "agent_definition"
    ]
    assert list(inspect.signature(simulation_project_of).parameters) == [
        "agent_definition_id"
    ]
    assert list(inspect.signature(agent_of_run).parameters) == ["test_execution"]
    assert simulation_project.SIMULATION_PROJECT_PREFIX == "fi-simulation-"
    assert (
        simulation_project.RESERVED_PROJECT_NAME_MESSAGE
        == "that project name is reserved"
    )
    assert issubclass(SimulationProjectError, Exception)


def test_agent_id_in_name_is_exact():
    assert agent_id_in_simulation_project_name(RESERVED_NAME) == AGENT_ID
    for name in NOT_RESERVED_NAMES + [None, 7]:
        assert agent_id_in_simulation_project_name(name) is None, repr(name)


def test_ensure_refuses_a_deleted_hidden_project(organization, workspace):
    agent = _agent_without_context(organization, workspace)
    project = ensure_simulation_project(agent)
    Project.objects.create(
        name="A visible project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    assert simulation_project_of(agent.id) == project
    assert simulation_project_of(uuid.uuid4()) is None
    assert simulation_project_of(None) is None

    Project.with_simulation.filter(pk=project.pk).update(deleted=True)
    assert simulation_project_of(agent.id) == project
    before = _row(project)
    with pytest.raises(SimulationProjectError):
        ensure_simulation_project(agent)
    assert _row(project) == before
    assert before["deleted"] is True


def test_name_conflict_with_an_older_visible_project_propagates(
    organization, user, workspace
):
    agent = _agent_without_context(organization, workspace)
    Project.no_workspace_objects.create(
        name=simulation_project_name(agent.id),
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )

    with pytest.raises((ValidationError, IntegrityError)):
        ensure_simulation_project(agent)
    assert not Project.with_simulation.filter(simulation_agent_id=agent.id).exists()

    other_workspace = Workspace.objects.create(
        name="Other", organization=organization, created_by=user
    )
    set_workspace_context(workspace=other_workspace, organization=organization)
    with pytest.raises((ValidationError, IntegrityError)):
        ensure_simulation_project(agent)
    clear_workspace_context()
    assert not Project.with_simulation.filter(simulation_agent_id=agent.id).exists()


def test_collectors_upsert_meets_the_platform_row(organization, workspace):
    agent = _agent_without_context(organization, workspace)
    project = ensure_simulation_project(agent)

    assert _collectors_upsert(agent) == [(project.id,)]
    assert Project.with_simulation.filter(simulation_agent_id=agent.id).count() == 1

    other = _agent_without_context(organization, workspace, "Collector first")
    [(project_id,)] = _collectors_upsert(other)
    with structlog.testing.capture_logs() as events:
        ensured = ensure_simulation_project(other)
    assert ensured.id == project_id
    assert Project.with_simulation.filter(simulation_agent_id=other.id).count() == 1
    assert not [e for e in events if e["event"] == "simulation_project_created"]


def test_deleting_an_agent_leaves_its_hidden_project_untouched_and_ensure_refuses(
    organization, workspace
):
    from simulate.views.agent_definition import (
        soft_delete_agent_definition_and_versions,
    )

    def delete_through_the_model(agent):
        agent.delete()

    def delete_through_the_view_helper(agent):
        soft_delete_agent_definition_and_versions(agent)

    def delete_through_a_queryset(agent):
        AgentDefinition._base_manager.filter(pk=agent.pk).update(deleted=True)

    for delete in (
        delete_through_the_model,
        delete_through_the_view_helper,
        delete_through_a_queryset,
    ):
        agent = _agent_without_context(organization, workspace, delete.__name__)
        project = ensure_simulation_project(agent)
        before = _row(project)

        delete(agent)

        assert _row(project) == before
        with pytest.raises(SimulationProjectError):
            ensure_simulation_project(agent)
        assert _row(project) == before


def _request(organization, workspace):
    return SimpleNamespace(
        user=SimpleNamespace(organization=organization),
        organization=organization,
        workspace=workspace,
    )


def _run(organization, workspace, run_test_agent=None, execution_agent=None):
    clear_workspace_context()
    run = RunTest.objects.create(
        name="Run",
        organization=organization,
        workspace=workspace,
        agent_definition=run_test_agent,
    )
    execution = TestExecution.objects.create(
        run_test=run,
        status=TestExecution.ExecutionStatus.COMPLETED,
        agent_definition=execution_agent,
    )
    # ``CallExecution.scenario`` is a required foreign key.
    scenario = Scenarios.objects.create(
        name="Scenario",
        source="synthetic",
        organization=organization,
        workspace=workspace,
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
    )
    return run, execution, call


def _other_organization(user):
    other = Organization.objects.create(name=f"Other {uuid.uuid4().hex[:8]}")
    other_workspace = Workspace.no_workspace_objects.create(
        name=f"Other workspace {uuid.uuid4().hex[:8]}",
        organization=other,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    return other, other_workspace


def test_scope_for_run_opens_the_project_of_the_runs_agent(organization, workspace):
    request = _request(organization, workspace)
    run_agent = _agent_without_context(organization, workspace, "Run agent")
    execution_agent = _agent_without_context(organization, workspace, "Exec agent")
    run_project = ensure_simulation_project(run_agent)
    execution_project = ensure_simulation_project(execution_agent)

    _, only_execution, only_execution_call = _run(
        organization, workspace, None, execution_agent
    )
    _, only_run_test, only_run_test_call = _run(organization, workspace, run_agent)
    _, both, both_call = _run(organization, workspace, run_agent, execution_agent)
    _, neither, neither_call = _run(organization, workspace)

    cases = [
        (only_execution, only_execution_call, execution_project),
        (only_run_test, only_run_test_call, run_project),
        (both, both_call, execution_project),
        (neither, neither_call, None),
    ]
    for execution, call, expected in cases:
        by_run = simulation_scope_for_run(request, test_execution_id=execution.id)
        by_call = simulation_scope_for_run(request, call_execution_id=call.id)
        assert by_run == expected
        assert by_call == expected


def _refusal_case(name, organization, workspace, user):
    """Return ``(request, kwargs)``: a run the caller must not be able to open."""
    agent = _agent_without_context(organization, workspace, name)
    project = ensure_simulation_project(agent)
    request = _request(organization, workspace)
    run, execution, call = _run(organization, workspace, agent)

    if name == "other organization":
        other, other_workspace = _other_organization(user)
        other_agent = _agent_without_context(other, other_workspace, name)
        ensure_simulation_project(other_agent)
        _, other_execution, _ = _run(other, other_workspace, other_agent)
        return request, {"test_execution_id": other_execution.id}
    if name == "call of another organization":
        other, other_workspace = _other_organization(user)
        other_agent = _agent_without_context(other, other_workspace, name)
        ensure_simulation_project(other_agent)
        _, _, other_call = _run(other, other_workspace, other_agent)
        return request, {"call_execution_id": other_call.id}
    if name in (
        "run of another organization naming this organization's agent",
        "call of another organization naming this organization's agent",
    ):
        # No workspace: the default-workspace rule (``workspace__isnull``)
        # matches it, so only the organization filter refuses it.
        other, _ = _other_organization(user)
        _, other_execution, other_call = _run(other, None, None, agent)
        if name.startswith("call"):
            return request, {"call_execution_id": other_call.id}
        return request, {"test_execution_id": other_execution.id}
    if name == "run naming another organization's agent whose project is here":
        # Inconsistent rows: the hidden project sits in this organization but
        # belongs to an agent of another one, so only the agent check refuses.
        other, other_workspace = _other_organization(user)
        other_agent = _agent_without_context(other, other_workspace, name)
        other_project = ensure_simulation_project(other_agent)
        Project.with_simulation.filter(pk=other_project.pk).update(
            organization=organization
        )
        _, foreign_execution, _ = _run(organization, workspace, other_agent)
        return request, {"test_execution_id": foreign_execution.id}
    if name == "other workspace":
        first = Workspace.objects.create(
            name="First", organization=organization, created_by=user
        )
        second = Workspace.objects.create(
            name="Second", organization=organization, created_by=user
        )
        first_agent = _agent_without_context(organization, first, name)
        ensure_simulation_project(first_agent)
        _, in_first, _ = _run(organization, first, first_agent)
        return _request(organization, second), {"test_execution_id": in_first.id}
    if name == "deleted run test":
        RunTest.objects.filter(pk=run.pk).update(deleted=True)
    elif name == "deleted test execution":
        TestExecution.objects.filter(pk=execution.pk).update(deleted=True)
    elif name == "call of a deleted test execution":
        TestExecution.objects.filter(pk=execution.pk).update(deleted=True)
        return request, {"call_execution_id": call.id}
    elif name == "unknown id":
        return request, {"test_execution_id": uuid.uuid4()}
    elif name == "deleted agent":
        AgentDefinition.objects.filter(pk=agent.pk).update(deleted=True)
    elif name == "agent with no hidden project":
        Project.with_simulation.filter(pk=project.pk).delete()
    elif name == "deleted hidden project":
        Project.with_simulation.filter(pk=project.pk).update(deleted=True)
    elif name == "hidden project of another organization":
        other, _ = _other_organization(user)
        Project.with_simulation.filter(pk=project.pk).update(organization=other)
    else:
        raise AssertionError(name)
    return request, {"test_execution_id": execution.id}


@pytest.mark.parametrize(
    "name",
    [
        "other organization",
        "call of another organization",
        "run of another organization naming this organization's agent",
        "call of another organization naming this organization's agent",
        "run naming another organization's agent whose project is here",
        "other workspace",
        "deleted run test",
        "deleted test execution",
        "call of a deleted test execution",
        "unknown id",
        "deleted agent",
        "agent with no hidden project",
        "deleted hidden project",
        "hidden project of another organization",
    ],
)
def test_scope_for_run_refuses_what_the_caller_cannot_read(
    name, organization, workspace, user
):
    request, kwargs = _refusal_case(name, organization, workspace, user)
    assert simulation_scope_for_run(request, **kwargs) is None


def test_scope_for_run_returns_only_that_agents_project(organization, workspace):
    request = _request(organization, workspace)
    agent_x = _agent_without_context(organization, workspace, "X")
    agent_y = _agent_without_context(organization, workspace, "Y")
    project_x = ensure_simulation_project(agent_x)
    project_y = ensure_simulation_project(agent_y)
    _, execution_x, _ = _run(organization, workspace, agent_x)
    _, execution_y, _ = _run(organization, workspace, agent_y)

    assert simulation_scope_for_run(request, test_execution_id=execution_x.id) == (
        project_x
    )
    assert simulation_scope_for_run(request, test_execution_id=execution_y.id) == (
        project_y
    )


def test_scope_for_run_with_no_parameter_or_both_runs_no_query(
    organization, workspace, django_assert_num_queries
):
    request = _request(organization, workspace)
    agent = _agent_without_context(organization, workspace, "Agent")
    ensure_simulation_project(agent)
    _, execution, call = _run(organization, workspace, agent)

    with django_assert_num_queries(0):
        assert simulation_scope_for_run(request) is None
        assert (
            simulation_scope_for_run(
                request, test_execution_id=execution.id, call_execution_id=call.id
            )
            is None
        )


def test_scope_for_run_with_an_agent_of_another_workspace(
    organization, workspace, user
):
    """A run is the authority: it opens the project of whichever agent it names.

    ``RunTest`` and ``TestExecution`` do not check that the agent is in the
    run's workspace, so a caller who can read the run reads the agent-wide
    hidden project of an agent in another workspace. This pins that behaviour;
    adding an agent-workspace check would be a product decision.
    """
    other_workspace = Workspace.objects.create(
        name="Other", organization=organization, created_by=user
    )
    agent = _agent_without_context(organization, other_workspace, "Elsewhere")
    project = ensure_simulation_project(agent)
    _, execution, call = _run(organization, workspace, agent)
    request = _request(organization, workspace)

    assert simulation_scope_for_run(request, test_execution_id=execution.id) == (
        project
    )
    assert simulation_scope_for_run(request, call_execution_id=call.id) == project


def test_run_scope_project_ids_pinned_must_equal(organization, workspace):
    agent = _agent_without_context(organization, workspace, "Agent")
    project = ensure_simulation_project(agent)
    only = [str(project.id)]

    assert run_scope_project_ids(project) == only
    assert run_scope_project_ids(project, None) == only
    assert run_scope_project_ids(project, "") == only
    assert run_scope_project_ids(project, str(project.id)) == only
    assert run_scope_project_ids(project, project.id) == only
    assert run_scope_project_ids(project, uuid.uuid4()) == []
    assert run_scope_project_ids(project, str(uuid.uuid4())) == []


def test_the_run_lookup_matches_the_v3_run_pages(
    organization, workspace, user, monkeypatch
):
    # Function-local: importing the views loads every simulate view.
    from django.http import Http404

    from simulate.views import run_results_v3

    # Only the payload builder is replaced; the lookup under test is the view's.
    monkeypatch.setattr(
        run_results_v3, "build_call_execution_detail", lambda call, request: {}
    )

    def v3_reads_execution(request, execution):
        try:
            found = run_results_v3._execution_for_request(request, execution.id)
        except Http404:
            return False
        return found.pk == execution.pk

    def v3_reads_call(request, call):
        try:
            run_results_v3.CallExecutionV3DetailView().get(request, call.id)
        except Http404:
            return False
        return True

    agent = _agent_without_context(organization, workspace, "Agent")
    ensure_simulation_project(agent)
    first = Workspace.objects.create(
        name="First", organization=organization, created_by=user
    )
    second = Workspace.objects.create(
        name="Second", organization=organization, created_by=user
    )
    other, other_workspace = _other_organization(user)

    readable = _run(organization, workspace, agent)
    in_first = _run(organization, first, agent)
    other_org = _run(other, other_workspace, agent)
    deleted_run_test = _run(organization, workspace, agent)
    RunTest.objects.filter(pk=deleted_run_test[0].pk).update(deleted=True)

    cases = [
        (_request(organization, workspace), readable, True),
        (_request(organization, first), in_first, True),
        (_request(organization, second), in_first, False),
        (_request(organization, workspace), other_org, False),
        (_request(organization, workspace), deleted_run_test, False),
    ]
    for request, (_, execution, call), expected in cases:
        by_run = simulation_scope_for_run(request, test_execution_id=execution.id)
        by_call = simulation_scope_for_run(request, call_execution_id=call.id)
        assert (by_run is not None) is expected
        assert (by_call is not None) is expected
        assert v3_reads_execution(request, execution) is expected
        assert v3_reads_call(request, call) is expected

    # Intended difference: the call of a soft-deleted test execution is refused
    # here, while the v3 call detail page does not look at that flag.
    request = _request(organization, workspace)
    TestExecution.objects.filter(pk=readable[1].pk).update(deleted=True)
    assert simulation_scope_for_run(request, call_execution_id=readable[2].id) is None
    assert v3_reads_call(request, readable[2]) is True
