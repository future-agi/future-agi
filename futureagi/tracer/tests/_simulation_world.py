"""The world the hidden simulation project tests read, shared by their modules.

Real projects, runs and agents in the database, with the ClickHouse copies of
their traces in memory. ``test_simulation_managers`` sends the by-id reads
through the HTTP client and ``test_simulation_mcp_tools`` through the MCP tools.
The ``world`` fixture is imported by name into each module that uses it.
"""

import uuid
from types import SimpleNamespace

import pytest
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.ai_model import AIModel
from simulate.models.agent_definition import AgentDefinition
from simulate.models.run_test import RunTest
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.project import Project
from tracer.services.simulation_project import ensure_simulation_project
from tracer.tests.test_detail_identity_collision import (
    STARTED,
    _copy,
    _PhysicalCopies,
)
from tracer.tests.test_simulation_project import (
    _agent_without_context,
    _other_organization,
    _run,
)

RUN_PARAMETERS = ("test_execution_id", "call_execution_id")


def _agent(organization, workspace, name):
    return AgentDefinition.objects.create(
        agent_name=name,
        description=f"{name} description",
        agent_type="text",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


def _project(manager, organization, workspace, name, deleted=False, **extra):
    return manager.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        deleted=deleted,
        **extra,
    )


# The answer of each read for a trace it will not serve (a missing trace).
REFUSED_STATUS = {
    "trace": status.HTTP_400_BAD_REQUEST,
    "voice": status.HTTP_404_NOT_FOUND,
    "span": status.HTTP_400_BAD_REQUEST,
    "physical": status.HTTP_404_NOT_FOUND,
}
PHYSICAL_SERVICE = "svc"
PHYSICAL_HOUR = STARTED.replace(minute=0, second=0, microsecond=0)


class _RunScopeCopies(_PhysicalCopies):
    """The physical copies, plus the exact read of one physical span.

    The exact read passes no ``timeout_ms`` and names its project
    ``physical_project_id``; every call is recorded.
    """

    def execute_ch_query(self, query, params, *, timeout_ms=None, settings=None):
        if "physical_project_id" not in params:
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )
        self.calls.append((query, params))
        rows = [
            {
                **row,
                "service_name": PHYSICAL_SERVICE,
                "start_hour": PHYSICAL_HOUR,
                "_version": str(row["_version"]),
                "is_deleted": 0,
            }
            for row in self.rows
            if row["project_id"] == params["physical_project_id"]
            and row["trace_id"] == params["physical_trace_id"]
            and row["id"] == params["physical_span_id"]
            and row["observation_type"] == params["physical_observation_type"]
        ]
        return SimpleNamespace(data=rows)


def _place(rows, project, tag, trace_id, version):
    """Put a conversation root and one child of ``trace_id`` into ``project``."""
    rows.append(_copy(project, trace_id, f"{tag}-root", version=version))
    rows.append(
        _copy(
            project,
            trace_id,
            f"{tag}-child",
            version=version,
            parent=f"{tag}-root",
            kind="llm",
        )
    )
    return SimpleNamespace(
        trace_id=trace_id, span_id=f"{tag}-child", project=project, version=version
    )


@pytest.fixture
def world(auth_client, user, organization, workspace, monkeypatch):
    """Real projects, runs and agents; only the ClickHouse rows are in memory."""
    prod = _project(Project.objects, organization, workspace, "production")
    empty = _project(Project.objects, organization, workspace, "empty")
    agent_x = _agent_without_context(organization, workspace, "X")
    agent_y = _agent_without_context(organization, workspace, "Y")
    agent_z = _agent_without_context(organization, workspace, "Z, no hidden project")
    agent_w = _agent_without_context(organization, workspace, "W, deleted")
    hidden_x = ensure_simulation_project(agent_x)
    hidden_y = ensure_simulation_project(agent_y)
    hidden_w = ensure_simulation_project(agent_w)
    AgentDefinition.objects.filter(pk=agent_w.pk).update(deleted=True)

    rows = []
    # T is held by the production project (newest write) and by X's hidden one.
    t_prod = _place(rows, prod, "t", str(uuid.uuid4()), 20)
    t_hidden = _place(rows, hidden_x, "t", t_prod.trace_id, 10)
    h = _place(rows, hidden_x, "h", str(uuid.uuid4()), 10)
    hy = _place(rows, hidden_y, "hy", str(uuid.uuid4()), 10)
    hw = _place(rows, hidden_w, "hw", str(uuid.uuid4()), 10)

    _, execution_x, call_x = _run(organization, workspace, None, agent_x)
    _, execution_by_run_test, _ = _run(organization, workspace, agent_x, None)
    _, execution_y, _ = _run(organization, workspace, None, agent_y)
    _, execution_z, _ = _run(organization, workspace, None, agent_z)
    _, execution_w, _ = _run(organization, workspace, None, agent_w)
    deleted_run, deleted_execution, _ = _run(organization, workspace, None, agent_x)
    RunTest.objects.filter(pk=deleted_run.pk).update(deleted=True)

    other_organization, other_organization_workspace = _other_organization(user)
    foreign_agent = _agent_without_context(
        other_organization, other_organization_workspace, "Foreign"
    )
    ensure_simulation_project(foreign_agent)
    _, foreign_execution, foreign_call = _run(
        other_organization, other_organization_workspace, None, foreign_agent
    )
    # A run of the other organization with no workspace, naming this
    # organization's agent: the default-workspace rule matches a run without a
    # workspace, so only the organization filter refuses it.
    _, foreign_unscoped_execution, foreign_unscoped_call = _run(
        other_organization, None, None, agent_x
    )
    second_workspace = Workspace.objects.create(
        name="Second", organization=organization, created_by=user
    )
    _, second_workspace_execution, _ = _run(
        organization, second_workspace, None, agent_x
    )

    analytics = _RunScopeCopies(rows)
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", lambda: analytics)
    monkeypatch.setattr(
        "tracer.views.observation_span.V2AnalyticsQueryService", lambda: analytics
    )
    monkeypatch.setattr(
        "tracer.views.trace.ObservabilityService.process_raw_logs",
        lambda *_args, **_kwargs: {},
    )
    return SimpleNamespace(
        client=auth_client,
        analytics=analytics,
        prod=prod,
        empty=empty,
        hidden_x=hidden_x,
        hidden_y=hidden_y,
        hidden_w=hidden_w,
        t_prod=t_prod,
        t_hidden=t_hidden,
        h=h,
        hy=hy,
        hw=hw,
        run_x=execution_x.id,
        call_x=call_x.id,
        run_x_by_run_test=execution_by_run_test.id,
        run_y=execution_y.id,
        run_z=execution_z.id,
        run_w=execution_w.id,
        run_deleted=deleted_execution.id,
        run_foreign=foreign_execution.id,
        call_foreign=foreign_call.id,
        run_foreign_unscoped=foreign_unscoped_execution.id,
        call_foreign_unscoped=foreign_unscoped_call.id,
        run_second_workspace=second_workspace_execution.id,
    )


def _carried(analytics):
    """Every project id any recorded statement was bound to."""
    ids = set()
    for _query, params in analytics.calls:
        ids.update(params.get("detail_project_ids", ()))
        if "physical_project_id" in params:
            ids.add(params["physical_project_id"])
    return ids


def _ids(*projects):
    return {str(project.id) for project in projects}


def _all_hidden(w):
    return _ids(w.hidden_x, w.hidden_y, w.hidden_w)


# (case, build(world) -> (query params, target), projects no statement may carry)
# Case 3 names a run whose own agent (Y) is readable, so Y's project may be
# carried; the target still lives in X's, which must never be.
REFUSALS = [
    ("no run, hidden trace", lambda w: ({}, w.h), _all_hidden),
    (
        "no run, project_id is the hidden project",
        lambda w: ({"project_id": str(w.hidden_x.id)}, w.h),
        _all_hidden,
    ),
    (
        "run of another agent asks for this agent's trace",
        lambda w: ({"test_execution_id": w.run_y}, w.h),
        lambda w: _ids(w.hidden_x, w.hidden_w),
    ),
    (
        "run of another organization",
        lambda w: (
            {"test_execution_id": w.run_foreign, "project_id": str(w.hidden_x.id)},
            w.h,
        ),
        _all_hidden,
    ),
    (
        "run of another organization, no workspace, naming this agent",
        lambda w: ({"test_execution_id": w.run_foreign_unscoped}, w.h),
        _all_hidden,
    ),
    (
        "call of another organization, no workspace, naming this agent",
        lambda w: ({"call_execution_id": w.call_foreign_unscoped}, w.h),
        _all_hidden,
    ),
    (
        "run in another workspace",
        lambda w: ({"test_execution_id": w.run_second_workspace}, w.h),
        _all_hidden,
    ),
    ("unknown run", lambda w: ({"test_execution_id": uuid.uuid4()}, w.h), _all_hidden),
    (
        "call of another organization",
        lambda w: ({"call_execution_id": w.call_foreign}, w.h),
        _all_hidden,
    ),
    (
        "agent with no hidden project",
        lambda w: ({"test_execution_id": w.run_z}, w.h),
        _all_hidden,
    ),
    (
        "deleted agent, its own hidden trace",
        lambda w: ({"test_execution_id": w.run_w}, w.hw),
        _all_hidden,
    ),
    (
        "deleted run test",
        lambda w: ({"test_execution_id": w.run_deleted}, w.h),
        _all_hidden,
    ),
]


# (case, build(world) -> (query params, target, project served or None))
SERVES = [
    (
        "test_execution_id of a run of the agent",
        lambda w: ({"test_execution_id": w.run_x}, w.h, w.hidden_x),
    ),
    (
        "call_execution_id, no project_id",
        lambda w: ({"call_execution_id": w.call_x}, w.h, w.hidden_x),
    ),
    (
        "test_execution_id of a run of another agent, its own trace",
        lambda w: ({"test_execution_id": w.run_y}, w.hy, w.hidden_y),
    ),
    (
        "agent named on the run test only",
        lambda w: ({"test_execution_id": w.run_x_by_run_test}, w.h, w.hidden_x),
    ),
    (
        "project_id is the hidden project",
        lambda w: (
            {"test_execution_id": w.run_x, "project_id": str(w.hidden_x.id)},
            w.h,
            w.hidden_x,
        ),
    ),
    (
        "project_id is a visible project",
        lambda w: (
            {"test_execution_id": w.run_x, "project_id": str(w.prod.id)},
            w.h,
            None,
        ),
    ),
    (
        "same trace id in production and hidden, with the run",
        lambda w: ({"test_execution_id": w.run_x}, w.t_hidden, w.hidden_x),
    ),
    (
        "same trace id in production and hidden, without the run",
        lambda w: ({}, w.t_prod, w.prod),
    ),
]


def second_organization(world, user):
    """A second organization of ``user``, and an API key of it.

    ``user.organization`` stays the organization of the ``world``. The new one
    has its own agent, hidden project with one trace in the in-memory copies,
    visible project, and run; the user owns it. ``own_run`` and ``own_call``
    belong to the user's own organization and have no workspace, so a workspace
    rule cannot refuse them for the key: only the organization rule can.
    """
    other, other_workspace = _other_organization(user)
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=other,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=other_workspace,
        role="Workspace Owner",
        level=Level.OWNER,
        is_active=True,
        organization_membership=membership,
    )
    agent = _agent_without_context(other, other_workspace, "Second organization")
    hidden = ensure_simulation_project(agent)
    visible = _project(
        Project.no_workspace_objects, other, other_workspace, "second production"
    )
    trace = _place(world.analytics.rows, hidden, "s", str(uuid.uuid4()), 10)
    _, execution, call = _run(other, other_workspace, None, agent)
    own_agent = AgentDefinition.no_workspace_objects.get(
        pk=world.hidden_x.simulation_agent_id
    )
    _, own_execution, own_call = _run(user.organization, None, None, own_agent)
    key = OrgApiKey.no_workspace_objects.create(
        organization=other,
        workspace=other_workspace,
        user=user,
        type="user",
        enabled=True,
        api_key=f"fi-{uuid.uuid4().hex}",
        secret_key=f"sk-{uuid.uuid4().hex}",
    )
    return SimpleNamespace(
        organization=other,
        workspace=other_workspace,
        api_key=key,
        hidden=hidden,
        visible=visible,
        trace=trace,
        run=execution.id,
        call=call.id,
        own_run=own_execution.id,
        own_call=own_call.id,
    )
