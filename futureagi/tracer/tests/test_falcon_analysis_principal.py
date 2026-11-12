"""Background Falcon analysis must construct the real agent and keep its caller."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.test_workspace_landing import landing as landing
from accounts.tests.workspace_write_support import scope_no_egress as scope_no_egress
from ai_tools.base import ToolContext
from ai_tools.tools.tracing.analyze_project_traces import (
    AnalyzeProjectTracesInput,
    AnalyzeProjectTracesTool,
)
from ee.falcon_ai import agent
from ee.falcon_ai.models import Conversation
from ee.falcon_ai.tool_scope import authenticated_tool_scope
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.project import Project
from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.tasks import error_analysis

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def analysis_case(landing, scope_no_egress, monkeypatch):
    project = Project._base_manager.create(
        name="Request-bound analysis",
        organization=landing.org,
        workspace=landing.default,
        user=landing.user,
        model_type="generative_llm",
        trace_type="observe",
    )
    monkeypatch.setattr("ee.usage.deployment.DeploymentMode.is_oss", lambda: False)
    # Keep the REAL AgentLoop constructor/signature; replace provider clients only.
    monkeypatch.setattr(agent, "FalconLLMClient", Mock())
    monkeypatch.setattr(agent, "PromptBuilder", Mock())
    monkeypatch.setattr(agent, "ContextManager", Mock())
    run = AsyncMock(
        return_value={"content": "No errors", "input_tokens": 0, "output_tokens": 0}
    )
    monkeypatch.setattr(agent.AgentLoop, "run", run)
    factory = Mock(wraps=agent.AgentLoop)
    monkeypatch.setattr(agent, "AgentLoop", factory)
    monkeypatch.setattr("tracer.services.clickhouse.client.ClickHouseClient", Mock())
    clear = Mock()
    failed = Mock()
    monkeypatch.setattr("tracer.queries.deep_analysis_state.clear", clear)
    monkeypatch.setattr("tracer.queries.deep_analysis_state.set_failed", failed)
    yield SimpleNamespace(
        landing=landing,
        project=project,
        trace_id=str(uuid4()),
        run=run,
        factory=factory,
        clear=clear,
        failed=failed,
    )
    clear_workspace_context()


def test_background_analysis_uses_real_agent_constructor(analysis_case):
    case = analysis_case
    result = error_analysis._falcon_analyze_single_trace(
        case.trace_id, str(case.project.pk), **principal(case)
    )
    assert result["success"], result
    case.run.assert_awaited_once()
    assert Conversation._base_manager.filter(organization=case.landing.org).count() == 1
    assert TraceErrorAnalysis._base_manager.filter(
        trace_id=case.trace_id, project=case.project
    ).exists()
    from tracer.queries.background_analysis_state import get_state

    receipt = get_state(case.trace_id)
    assert receipt.status == "completed"
    assert receipt.project_id == case.project.pk
    case.clear.assert_not_called()
    case.failed.assert_not_called()
    context = case.factory.call_args.kwargs["tool_context"]
    assert context.user.pk == case.landing.user.pk
    assert context.organization.pk == case.landing.org.pk
    assert context.workspace.pk == case.landing.default.pk
    assert (
        Conversation._base_manager.get(organization=case.landing.org).user_id
        == case.landing.user.pk
    )


def principal(case):
    return {
        "requesting_user_id": str(case.landing.user.pk),
        "organization_id": str(case.landing.org.pk),
        "workspace_id": str(case.landing.default.pk),
    }


@pytest.mark.parametrize(
    "boundary",
    [
        "missing-principal",
        "inactive-user",
        "removed-org",
        "removed-workspace",
        "inactive-workspace",
        "foreign-project",
        "moved-project",
    ],
)
def test_background_analysis_refuses_unavailable_request_principal(
    analysis_case, boundary
):
    case = analysis_case
    state = case.landing
    kwargs = principal(case)
    if boundary == "missing-principal":
        kwargs = {}
    elif boundary == "inactive-user":
        User._base_manager.filter(pk=state.user.pk).update(is_active=False)
    elif boundary == "removed-org":
        OrganizationMembership._base_manager.filter(user=state.user).update(
            is_active=False
        )
    elif boundary == "removed-workspace":
        WorkspaceMembership._base_manager.filter(
            user=state.user, workspace=state.default
        ).delete()
    elif boundary == "inactive-workspace":
        Workspace._base_manager.filter(pk=state.default.pk).update(is_active=False)
    elif boundary == "foreign-project":
        org = Organization.objects.create(name="Moved project org")
        Project._base_manager.filter(pk=case.project.pk).update(organization=org)
    else:
        Project._base_manager.filter(pk=case.project.pk).update(workspace=state.other)
    before = list(Conversation._base_manager.order_by("pk").values())
    result = error_analysis._falcon_analyze_single_trace(
        case.trace_id, str(case.project.pk), **kwargs
    )
    assert not result["success"], result
    assert result["code"] == "workspace_unavailable", result
    case.factory.assert_not_called()
    case.run.assert_not_awaited()
    assert list(Conversation._base_manager.order_by("pk").values()) == before
    assert not TraceErrorAnalysis._base_manager.filter(trace_id=case.trace_id).exists()


def test_background_analysis_restores_callers_context(analysis_case):
    from tfc.middleware.workspace_context import (
        get_current_user,
        get_current_workspace,
        workspace_context,
    )

    case = analysis_case
    with workspace_context(case.landing.other, case.landing.org, case.landing.owner):
        result = error_analysis._falcon_analyze_single_trace(
            case.trace_id, str(case.project.pk), **principal(case)
        )
        assert result["success"], result
        assert get_current_workspace() == case.landing.other
        assert get_current_user() == case.landing.owner


def test_trace_tool_dispatches_request_principal(analysis_case, monkeypatch):
    from unittest.mock import MagicMock

    case = analysis_case
    reader = MagicMock()
    reader.__enter__.return_value.root_ids_by_trace_ids.return_value = {
        case.trace_id: "root"
    }
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    monkeypatch.setattr("tracer.queries.deep_analysis_state.set_running", Mock())
    dispatch = Mock()
    monkeypatch.setattr(error_analysis.analyze_traces_on_demand, "delay", dispatch)
    context = ToolContext(case.landing.user, case.landing.org, case.landing.default)
    with authenticated_tool_scope(context):
        result = AnalyzeProjectTracesTool().execute(
            AnalyzeProjectTracesInput(
                project_id=case.project.pk, trace_ids=[case.trace_id]
            ),
            context,
        )
    assert not result.is_error, result
    dispatch.assert_called_once_with(
        str(case.project.pk), [case.trace_id], **principal(case)
    )


def test_analysis_batch_forwards_request_principal(analysis_case, monkeypatch):
    case = analysis_case
    worker = Mock(return_value={"success": False})
    monkeypatch.setattr(error_analysis, "_falcon_analyze_single_trace", worker)
    generation = str(uuid4())
    error_analysis.analyze_traces_on_demand(
        str(case.project.pk),
        [case.trace_id],
        generations={case.trace_id: generation},
        **principal(case),
    )
    worker.assert_called_once_with(
        case.trace_id, str(case.project.pk), generation=generation, **principal(case)
    )


def test_legacy_batch_principal_adapter(analysis_case, monkeypatch):
    """Old queued call shape projects None generation, never an admitted ID."""
    case = analysis_case
    worker = Mock(return_value={"success": False})

    def legacy_worker(trace_id, project_id, *, generation=None, **principal_args):
        assert generation is None
        return worker(trace_id, project_id, **principal_args)

    monkeypatch.setattr(error_analysis, "_falcon_analyze_single_trace", legacy_worker)
    error_analysis.analyze_traces_on_demand(
        str(case.project.pk), [case.trace_id], **principal(case)
    )
    worker.assert_called_once_with(
        case.trace_id, str(case.project.pk), **principal(case)
    )


def test_legacy_cache_clear_remains_separate_from_durable_completion(analysis_case):
    """The retained cache API is callable; background completion never uses it."""
    from tracer.queries import deep_analysis_state

    case = analysis_case
    deep_analysis_state.clear(case.trace_id)
    case.clear.assert_called_once_with(case.trace_id)
