import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models import Organization
from model_hub.models.ai_model import AIModel
from simulate.models import (
    AgentDefinition,
    CallExecution,
    HostedHarnessJob,
    RunTest,
    Scenarios,
    SimulatorAgent,
    TestExecution,
)
from simulate.services import run_traces_v3
from simulate.services.agent_setup import store_agent
from simulate.tests.test_agent_setup_service import source_agent
from tracer.models.project import Project
from tracer.services.clickhouse.client import CHError
from tracer.services.simulation_project import ensure_simulation_project

URL = "/simulate/v3/test-executions/{}/traces/"
NOTHING = {"project_id": None, "traces": []}


class FakeClickHouse:
    """Spans as (project id, job id, row); the read keeps one project's rows of one job and call."""

    def __init__(self):
        self.rows = []
        self.error = None
        self.calls = []

    def execute_read(self, query, params=None, timeout_ms=None, settings=None):
        self.calls.append(
            SimpleNamespace(query=query, params=params, timeout_ms=timeout_ms)
        )
        if self.error is not None:
            raise self.error
        kept = [
            row
            for project_id, job_id, row in self.rows
            if project_id == params["project_id"] and job_id == params["job_id"]
        ]
        kept = [row for row in kept if row[6] == params["call_execution_id"]]
        kept.sort(key=lambda row: (bool(row[1]), -row[3].timestamp(), row[0]))
        return kept[: params["limit"]], [], 1.0


@pytest.fixture
def agent(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="code agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def simulator_agent(db, organization, workspace):
    return SimulatorAgent.objects.create(
        name="Traces simulator",
        prompt="You are a test simulator.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def version(agent):
    return agent.create_version(description="", commit_message="first", status="active")


def _execution(organization, workspace, agent, simulator_agent, version=None):
    run_test = RunTest.no_workspace_objects.create(
        name="Traces run",
        agent_definition=agent,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        simulator_agent=simulator_agent,
        agent_definition=agent,
        agent_version=version,
    )


@pytest.fixture
def execution(db, organization, workspace, agent, simulator_agent, version):
    return _execution(organization, workspace, agent, simulator_agent, version)


@pytest.fixture
def scenario(db, organization, workspace, agent):
    return Scenarios.objects.create(
        name="Book a ride",
        description="Test scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
    )


@pytest.fixture
def call(db, execution, scenario):
    return CallExecution.objects.create(test_execution=execution, scenario=scenario)


def _job(organization, workspace, **fields):
    return HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest="digest",
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload={},
        **fields,
    )


@pytest.fixture
def job(db, organization, workspace, execution):
    return _job(
        organization, workspace, test_execution=execution, terminal_at=timezone.now()
    )


def _project(organization, workspace, user, name):
    return Project.objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        name=name,
        trace_type="observe",
    )


@pytest.fixture
def hidden_project(db, agent):
    # Made the way the platform makes it, so the run's project lookup is the real one.
    return ensure_simulation_project(agent)


@pytest.fixture
def clickhouse(monkeypatch):
    fake = FakeClickHouse()
    monkeypatch.setattr(run_traces_v3, "get_clickhouse_client", lambda: fake)
    return fake


def _store(version, kind):
    store_agent(
        agent_version_id=version.id, agent=source_agent(kind=kind), sdk_version="0.2.3"
    )


@pytest.fixture
def source_setup(version):
    _store(version, "source")


def _row(
    trace_id, start_time, *, name="turn", status_text="OK", latency_ms=12, call_id=""
):
    return (trace_id, True, name, start_time, status_text, latency_ms, call_id)


def _rootless(trace_id, start_time, call_id=""):
    return (trace_id, False, "", start_time, "", 0, call_id)


def _get(client, execution, call):
    return client.get(URL.format(execution.id), {"call_execution_id": str(call.id)})


def _parsed(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_a_calls_traces_listed_from_the_hidden_project_for_a_bound_source_agent(
    auth_client,
    execution,
    job,
    call,
    hidden_project,
    clickhouse,
    source_setup,
    organization,
    workspace,
    user,
):
    production = _project(organization, workspace, user, "Production")
    newest = timezone.now().replace(microsecond=0)
    older = newest - timedelta(minutes=3)
    hidden, this_job, this_call = str(hidden_project.id), str(job.id), str(call.id)
    clickhouse.rows = [
        (
            hidden,
            this_job,
            _row(
                "trace-a",
                older,
                name="verify_identity",
                status_text="ERROR",
                latency_ms=420,
                call_id=this_call,
            ),
        ),
        (hidden, str(uuid.uuid4()), _row("trace-x", newest, call_id=this_call)),
        (
            hidden,
            this_job,
            _row(
                "trace-b",
                newest,
                name="book_ride",
                latency_ms=1830,
                call_id=this_call,
            ),
        ),
        (str(production.id), this_job, _row("trace-p", newest, call_id=this_call)),
        (hidden, this_job, _rootless("trace-r", older, call_id=this_call)),
        (hidden, this_job, _row("trace-u", newest)),
        (
            hidden,
            this_job,
            _row("trace-o", newest, call_id=str(uuid.uuid4())),
        ),
    ]

    response = _get(auth_client, execution, call)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["project_id"] == hidden
    assert [trace["trace_id"] for trace in body["traces"]] == [
        "trace-r",
        "trace-b",
        "trace-a",
    ]
    rootless, linked, older_trace = body["traces"]
    assert rootless == {
        "trace_id": "trace-r",
        "name": None,
        "start_time": None,
        "status": None,
        "latency_ms": None,
        "call_execution_id": this_call,
    }
    assert (linked["name"], linked["status"], linked["latency_ms"]) == (
        "book_ride",
        "OK",
        1830,
    )
    assert _parsed(linked["start_time"]) == newest
    assert linked["call_execution_id"] == this_call
    assert (older_trace["name"], older_trace["status"]) == ("verify_identity", "ERROR")


def test_the_statement_is_bounded_to_the_project_the_job_its_window_and_the_call(
    auth_client, execution, job, call, hidden_project, clickhouse, source_setup
):
    _get(auth_client, execution, call)

    (read,) = clickhouse.calls
    assert read.query.startswith("SELECT trace_id, ")
    assert "FROM spans FINAL " in read.query
    assert "WHERE project_id = %(project_id)s " in read.query
    assert "AND resource_attrs.fi.simulation.job_id.:String = %(job_id)s " in read.query
    assert "AND start_time >= %(window_start)s " in read.query
    assert "AND start_time <= %(window_end)s " in read.query
    assert "GROUP BY trace_id HAVING call_execution_id = %(call_execution_id)s " in (
        read.query
    )
    assert read.query.endswith(
        "ORDER BY has_root ASC, root_start_time DESC, trace_id LIMIT %(limit)s"
    )
    assert read.timeout_ms == 2000
    assert read.params == {
        "project_id": str(hidden_project.id),
        "job_id": str(job.id),
        "window_start": job.created_at,
        "window_end": job.terminal_at,
        "call_execution_id": str(call.id),
        "limit": 200,
    }


def test_the_root_span_is_the_one_with_no_parent(
    auth_client, execution, job, call, hidden_project, clickhouse, source_setup
):
    _get(auth_client, execution, call)

    (read,) = clickhouse.calls
    root = "parent_span_id = ''"
    assert f"countIf({root}) > 0 AS has_root" in read.query
    assert f"argMinIf(name, start_time, {root}) AS root_name" in read.query
    assert f"minIf(start_time, {root}) AS root_start_time" in read.query
    assert f"argMinIf(status, start_time, {root}) AS root_status" in read.query
    assert f"argMinIf(latency_ms, start_time, {root}) AS root_latency_ms" in read.query
    assert "!=" not in read.query


def test_the_call_is_the_one_on_the_earliest_span_that_names_one(
    auth_client, execution, job, call, hidden_project, clickhouse, source_setup
):
    _get(auth_client, execution, call)

    (read,) = clickhouse.calls
    assert (
        "argMinIf(attrs_string['fi.simulator.call_execution_id'], start_time, "
        "mapContains(attrs_string, 'fi.simulator.call_execution_id')) "
        "AS call_execution_id" in read.query
    )
    assert "argMax" not in read.query


def test_a_first_launch_is_listed_by_the_job_id_alone(
    auth_client, execution, job, call, hidden_project, clickhouse, source_setup
):
    clickhouse.rows = [
        (
            str(hidden_project.id),
            str(job.id),
            _row("trace-a", timezone.now(), call_id=str(call.id)),
        )
    ]

    response = _get(auth_client, execution, call)

    assert [trace["trace_id"] for trace in response.json()["traces"]] == ["trace-a"]
    (read,) = clickhouse.calls
    assert "test_execution" not in read.query
    assert str(execution.id) not in {str(value) for value in read.params.values()}


def test_a_run_still_going_is_read_up_to_now(
    auth_client,
    execution,
    call,
    hidden_project,
    clickhouse,
    source_setup,
    organization,
    workspace,
):
    live_job = _job(organization, workspace, test_execution=execution, terminal_at=None)
    before = timezone.now()

    _get(auth_client, execution, call)

    window_end = clickhouse.calls[0].params["window_end"]
    assert live_job.created_at <= before <= window_end <= timezone.now()


def test_one_calls_traces(
    auth_client,
    execution,
    job,
    call,
    scenario,
    hidden_project,
    clickhouse,
    source_setup,
):
    other_call = CallExecution.objects.create(
        test_execution=execution, scenario=scenario
    )
    now = timezone.now()
    hidden, this_job = str(hidden_project.id), str(job.id)
    clickhouse.rows = [
        (hidden, this_job, _row("trace-a", now, call_id=str(call.id))),
        (hidden, this_job, _row("trace-b", now, call_id=str(other_call.id))),
        (hidden, this_job, _row("trace-c", now)),
    ]

    response = _get(auth_client, execution, call)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["project_id"] == hidden
    assert [trace["trace_id"] for trace in response.json()["traces"]] == ["trace-a"]
    (read,) = clickhouse.calls
    assert "HAVING call_execution_id = %(call_execution_id)s" in read.query
    assert read.params["call_execution_id"] == str(call.id)


def test_a_read_without_a_call_is_refused_and_reads_nothing(
    auth_client, execution, job, call, hidden_project, clickhouse, source_setup
):
    clickhouse.rows = [
        (
            str(hidden_project.id),
            str(job.id),
            _row("trace-a", timezone.now(), call_id=str(call.id)),
        ),
        (str(hidden_project.id), str(job.id), _row("trace-u", timezone.now())),
    ]

    response = auth_client.get(URL.format(execution.id))

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert clickhouse.calls == []


@pytest.mark.parametrize("value", ["not-a-uuid", ""])
def test_a_call_id_that_is_not_a_uuid_is_refused_and_reads_nothing(
    auth_client, execution, job, hidden_project, clickhouse, source_setup, value
):
    response = auth_client.get(URL.format(execution.id), {"call_execution_id": value})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert clickhouse.calls == []


def test_a_call_that_is_not_this_runs_is_refused_and_reads_nothing(
    auth_client, execution, job, hidden_project, clickhouse, source_setup
):
    response = auth_client.get(
        URL.format(execution.id), {"call_execution_id": str(uuid.uuid4())}
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert clickhouse.calls == []


def test_a_call_of_another_run_is_refused_and_reads_nothing(
    auth_client,
    execution,
    job,
    scenario,
    simulator_agent,
    hidden_project,
    clickhouse,
    source_setup,
):
    sibling = TestExecution.objects.create(
        run_test=execution.run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        simulator_agent=simulator_agent,
        agent_definition=execution.agent_definition,
    )
    foreign_call = CallExecution.objects.create(
        test_execution=sibling, scenario=scenario
    )

    response = _get(auth_client, execution, foreign_call)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert clickhouse.calls == []


def test_an_unbound_run_lists_nothing(
    auth_client, execution, call, hidden_project, clickhouse, source_setup
):
    response = _get(auth_client, execution, call)

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == NOTHING
    assert clickhouse.calls == []


@pytest.mark.parametrize("kind", ["endpoint", None])
def test_an_agent_that_is_not_source_lists_nothing(
    auth_client, execution, job, call, version, hidden_project, clickhouse, kind
):
    if kind is not None:
        _store(version, kind)

    assert _get(auth_client, execution, call).json() == NOTHING
    assert clickhouse.calls == []


def test_the_runs_own_version_decides_the_kind(
    auth_client, execution, job, call, agent, source_setup, hidden_project, clickhouse
):
    newer = agent.create_version(
        description="", commit_message="second", status="active"
    )
    _store(newer, "endpoint")
    clickhouse.rows = [
        (
            str(hidden_project.id),
            str(job.id),
            _row("trace-a", timezone.now(), call_id=str(call.id)),
        )
    ]

    response = _get(auth_client, execution, call)

    assert [trace["trace_id"] for trace in response.json()["traces"]] == ["trace-a"]


def _own_agent_execution(organization, workspace, run_agent, simulator_agent, own):
    """A run of ``run_agent``'s run test whose execution names ``own`` instead."""
    run_test = RunTest.no_workspace_objects.create(
        name="Own agent run",
        agent_definition=run_agent,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        simulator_agent=simulator_agent,
        agent_definition=own,
        agent_version=own.create_version(
            description="", commit_message="own", status="active"
        ),
    )


def test_the_executions_own_agent_decides_the_project(
    auth_client,
    organization,
    workspace,
    agent,
    simulator_agent,
    scenario,
    clickhouse,
):
    own = AgentDefinition.objects.create(
        agent_name="own code agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    run = _own_agent_execution(organization, workspace, agent, simulator_agent, own)
    _store(run.agent_version, "source")
    own_project = ensure_simulation_project(own)
    run_agent_project = ensure_simulation_project(agent)
    own_job = _job(
        organization, workspace, test_execution=run, terminal_at=timezone.now()
    )
    own_call = CallExecution.objects.create(test_execution=run, scenario=scenario)
    clickhouse.rows = [
        (
            str(own_project.id),
            str(own_job.id),
            _row("trace-own", timezone.now(), call_id=str(own_call.id)),
        ),
        (
            str(run_agent_project.id),
            str(own_job.id),
            _row("trace-run-test", timezone.now(), call_id=str(own_call.id)),
        ),
    ]

    response = _get(auth_client, run, own_call)

    assert response.json()["project_id"] == str(own_project.id)
    assert [trace["trace_id"] for trace in response.json()["traces"]] == ["trace-own"]
    assert clickhouse.calls[0].params["project_id"] == str(own_project.id)


def test_an_agent_with_no_hidden_project_lists_nothing(
    auth_client, execution, job, call, clickhouse, source_setup
):
    assert not Project.with_simulation.filter(
        simulation_agent=execution.agent_definition
    ).exists()

    assert _get(auth_client, execution, call).json() == NOTHING
    assert clickhouse.calls == []


def test_another_agents_hidden_project_is_not_this_runs(
    auth_client,
    organization,
    workspace,
    execution,
    job,
    call,
    clickhouse,
    source_setup,
):
    other = AgentDefinition.objects.create(
        agent_name="other code agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    other_project = ensure_simulation_project(other)
    clickhouse.rows = [
        (
            str(other_project.id),
            str(job.id),
            _row("trace-other", timezone.now(), call_id=str(call.id)),
        )
    ]

    assert _get(auth_client, execution, call).json() == NOTHING
    assert clickhouse.calls == []


def test_a_deleted_hidden_project_lists_nothing(
    auth_client, execution, job, call, hidden_project, clickhouse, source_setup
):
    clickhouse.rows = [
        (
            str(hidden_project.id),
            str(job.id),
            _row("trace-a", timezone.now(), call_id=str(call.id)),
        )
    ]
    Project.with_simulation.filter(pk=hidden_project.pk).update(deleted=True)

    assert _get(auth_client, execution, call).json() == NOTHING
    assert clickhouse.calls == []


@pytest.mark.parametrize(
    "error", [CHError("boom"), TimeoutError("ClickHouse read deadline exhausted")]
)
def test_a_clickhouse_failure_is_the_views_error(
    auth_client, execution, job, call, hidden_project, clickhouse, source_setup, error
):
    clickhouse.error = error

    response = _get(auth_client, execution, call)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Traces are unavailable right now"
    assert body["code"] == "run_traces_unavailable"


def test_a_run_of_another_organization_is_not_found(
    auth_client, hidden_project, clickhouse, source_setup
):
    stranger = Organization.objects.create(name="Other Organization")
    foreign_agent = AgentDefinition.no_workspace_objects.create(
        agent_name="Foreign agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=stranger,
    )
    foreign_version = foreign_agent.create_version(
        description="", commit_message="first", status="active"
    )
    _store(foreign_version, "source")
    foreign_simulator = SimulatorAgent.no_workspace_objects.create(
        name="Foreign simulator",
        prompt="You are a test simulator.",
        voice_provider="elevenlabs",
        voice_name="marissa",
        model="gpt-4",
        organization=stranger,
    )
    foreign = _execution(
        stranger, None, foreign_agent, foreign_simulator, foreign_version
    )
    _job(stranger, None, test_execution=foreign, terminal_at=timezone.now())

    response = auth_client.get(
        URL.format(foreign.id), {"call_execution_id": str(uuid.uuid4())}
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert clickhouse.calls == []
