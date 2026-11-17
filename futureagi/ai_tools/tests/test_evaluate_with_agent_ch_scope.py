"""Real PG/ClickHouse authorization at the agent-evaluation entry point.

Only the paid AI work is mocked. A post-cutover span/session has no legacy PG
row; its owning Project remains the org/workspace authorization anchor.
"""

import uuid
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from ai_tools.base import ToolResult
from ai_tools.tests.conftest import run_tool
from ai_tools.tests.fixtures import make_project, make_trace
from ai_tools.tests.test_evaluate_with_agent import (
    _ORCHESTRATOR_OUTPUT,
    _SCOUT_BRIEF,
    _ai_patches,
)

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]


@pytest.fixture
def evaluation_mocks():
    with ExitStack() as stack:
        stack.enter_context(patch("tfc.ee_gating.check_ee_feature", return_value=None))
        gather, summary, scout, orchestrator = [
            stack.enter_context(mock) for mock in _ai_patches()
        ]
        scout.return_value.run.return_value = dict(_SCOUT_BRIEF)
        orchestrator.return_value.run.return_value = dict(_ORCHESTRATOR_OUTPUT)
        yield gather, summary, scout, orchestrator


@pytest.fixture
def source_factory(tool_context):
    import clickhouse_connect

    from conftest import _require_safe_ch25_test_target
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace_session import TraceSession
    from tracer.services.clickhouse.v2 import get_v2_config

    config = get_v2_config()
    _require_safe_ch25_test_target(host=config["host"], database=config["database"])
    ch = clickhouse_connect.get_client(
        host=config["host"],
        port=config["http_port"],
        username=config["user"],
        password=config["password"],
        database=config["database"],
    )
    owned = []

    def create(storage, scope, project, *, deleted=False):
        source_id = str(uuid.uuid4())
        if storage == "pg":
            if scope == "span":
                trace = make_trace(tool_context, project=project)
                ObservationSpan.no_workspace_objects.create(
                    id=source_id, project=project, trace=trace, name="legacy span"
                )
            else:
                TraceSession.no_workspace_objects.create(id=source_id, project=project)
            return source_id

        assert not ObservationSpan.no_workspace_objects.filter(id=source_id).exists()
        assert not TraceSession.no_workspace_objects.filter(id=source_id).exists()
        now = datetime.now(UTC)
        if scope == "span":
            table, id_column = "spans", "id"
            columns = [
                "project_id",
                "org_id",
                "observation_type",
                "start_time",
                "trace_id",
                "id",
                "name",
                "is_deleted",
            ]
            values = [
                str(project.id),
                str(project.organization_id),
                "llm",
                now,
                str(uuid.uuid4()),
                source_id,
                "CH-only span",
                int(deleted),
            ]
        else:
            table, id_column = "trace_sessions", "trace_session_id"
            columns = [
                "project_id",
                "trace_session_id",
                "external_session_id",
                "first_seen",
                "version",
                "is_deleted",
            ]
            values = [
                str(project.id),
                source_id,
                "CH-only session",
                now,
                now,
                int(deleted),
            ]
        ch.insert(table, [values], column_names=columns)
        owned.append((table, id_column, source_id))
        return source_id

    try:
        yield create
    finally:
        for table, id_column, source_id in owned:
            ch.command(
                f"ALTER TABLE {table} DELETE WHERE {id_column} = %(id)s "
                "SETTINGS mutations_sync=2",
                parameters={"id": source_id},
            )
        ch.close()


def _evaluate(context, scope, source_id):
    return run_tool(
        "evaluate_with_agent",
        {"source_id": source_id, "input_scope": scope, "criteria": "Is it helpful?"},
        context,
    )


def _assert_denied(result, scope, source_id, mocks):
    # Identical to an unknown id: no tenant/project names or available-id hints.
    assert result == ToolResult.not_found(scope.title(), source_id)
    for mock in mocks:
        mock.assert_not_called()


_SCOPE_CASES = [
    ("selected", "same", True),
    ("selected", "other", False),
    ("selected", "foreign", False),
    ("selected", "legacy", False),
    ("default", "same", True),
    ("default", "legacy", True),
    ("default", "other", False),
    ("default", "foreign", False),
    ("default", "foreign_legacy", False),
    ("none", "same", True),
    ("none", "other", True),
    ("none", "legacy", True),
    ("none", "foreign", False),
]


@pytest.mark.parametrize("storage", ["ch", "pg"])
@pytest.mark.parametrize("scope", ["span", "session"])
@pytest.mark.parametrize(
    "caller_workspace,source_workspace,accepted",
    _SCOPE_CASES,
    ids=[f"{caller}-{source}" for caller, source, _ in _SCOPE_CASES],
)
def test_source_scope(
    tool_context,
    source_factory,
    evaluation_mocks,
    storage,
    scope,
    caller_workspace,
    source_workspace,
    accepted,
):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace
    from tracer.models.project import Project

    workspace = tool_context.workspace
    Workspace.no_workspace_objects.filter(id=workspace.id).update(
        is_default=caller_workspace == "default"
    )
    workspace.is_default = caller_workspace == "default"
    context = replace(
        tool_context, workspace=None if caller_workspace == "none" else workspace
    )
    org = tool_context.organization
    target_workspace = workspace
    if source_workspace.startswith("foreign"):
        org = Organization.objects.create(name=f"Foreign {uuid.uuid4().hex}")
    if source_workspace in ("other", "foreign"):
        target_workspace = Workspace.no_workspace_objects.create(
            name=f"other-{uuid.uuid4().hex}",
            organization=org,
            created_by=tool_context.user,
            is_default=False,
        )
    project = make_project(tool_context, organization=org, workspace=target_workspace)
    if source_workspace in ("legacy", "foreign_legacy"):
        # Bypass AutoWorkspaceField only to model a real pre-workspace project.
        Project.no_workspace_objects.filter(id=project.id).update(workspace=None)
        project.workspace = None
    source_id = source_factory(storage, scope, project)
    result = _evaluate(context, scope, source_id)
    if accepted:
        assert not result.is_error, result.content
        evaluation_mocks[3].return_value.run.assert_called_once()
    else:
        _assert_denied(result, scope, source_id, evaluation_mocks)


@pytest.mark.parametrize("scope", ["span", "session"])
@pytest.mark.parametrize("state", ["missing", "deleted", "no_projects"])
def test_ch_miss_does_not_start_evaluation(
    tool_context,
    source_factory,
    evaluation_mocks,
    scope,
    state,
):
    from accounts.models.workspace import Workspace

    project = make_project(tool_context)
    source_id = (
        str(uuid.uuid4())
        if state == "missing"
        else source_factory("ch", scope, project, deleted=state == "deleted")
    )
    context = tool_context
    if state == "no_projects":
        empty_workspace = Workspace.no_workspace_objects.create(
            name="empty-workspace",
            organization=tool_context.organization,
            created_by=tool_context.user,
        )
        context = replace(tool_context, workspace=empty_workspace)
    _assert_denied(
        _evaluate(context, scope, source_id), scope, source_id, evaluation_mocks
    )
