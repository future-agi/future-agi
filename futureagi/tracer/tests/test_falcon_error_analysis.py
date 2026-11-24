"""Held EE workers must neither start Falcon nor publish a trace analysis."""

import uuid
from contextvars import Context
from unittest.mock import Mock

import httpx
import pytest

from tracer.models.trace_error_analysis import TraceErrorAnalysis
from tracer.tasks.error_analysis import _falcon_analyze_single_trace
from tracer.tests._falcon_analysis import read_call, scripted_tool_stream
from tracer.tests._falcon_hold import assert_worker_refused, no_background_work

pytestmark = pytest.mark.requires_ee


@pytest.fixture
def global_analysis_skill(db):
    from ee.falcon_ai.builtin_skills_loader import seed_builtin_skills
    from ee.falcon_ai.models import Skill

    # Use the same loader as migrate, without a request's tenant defaults.
    # A transaction test may have flushed migration data between test cases.
    Context().run(seed_builtin_skills)
    return Skill.no_workspace_objects.get(
        slug="analyze-trace-errors",
        is_builtin=True,
        is_active=True,
        organization__isnull=True,
        workspace__isnull=True,
    )


@pytest.mark.django_db(transaction=True)
def test_held_worker_does_not_start_agent_or_record_clean_result(
    project, global_analysis_skill
):
    from ee.falcon_ai.models import Skill

    trace_id = str(uuid.uuid4())
    tenant_skill, _ = Skill.no_workspace_objects.get_or_create(
        organization=project.organization,
        slug=global_analysis_skill.slug,
        defaults={
            "workspace": project.workspace,
            "name": "Analyze trace errors",
            "instructions": "Tenant-only instructions.",
        },
    )
    assert tenant_skill.pk != global_analysis_skill.pk
    assert tenant_skill.organization_id == project.organization_id
    assert global_analysis_skill.organization_id is None
    assert global_analysis_skill.workspace_id is None

    with no_background_work(trace_id, stream=scripted_tool_stream(read_call(trace_id))):
        result = _falcon_analyze_single_trace(trace_id, str(project.id))
        assert_worker_refused(result, trace_id)
    assert not TraceErrorAnalysis.objects.filter(trace_id=trace_id).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_organization", [False, True])
@pytest.mark.parametrize("existing_analysis", [False, True])
def test_held_worker_leaves_reused_trace_id_unchanged(
    project, same_organization, existing_analysis
):
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tracer.models.project import Project

    trace_id = str(uuid.uuid4())
    if existing_analysis:
        TraceErrorAnalysis.objects.create(
            trace_id=trace_id, project=project, overall_score=None
        )

    other_org = project.organization
    other_workspace = project.workspace
    if not same_organization:
        other_org = Organization.objects.create(name="Other organization")
        other_user = User.objects.create_user(
            email=f"other-{uuid.uuid4().hex}@futureagi.com",
            password="test-password",
            organization=other_org,
        )
        other_workspace = Workspace.objects.create(
            name="Other workspace", organization=other_org, created_by=other_user
        )
    other_project = Project.objects.create(
        name="Other project",
        organization=other_org,
        workspace=other_workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )
    # Keep the collision real, including the previously populated local row.
    other_analysis = TraceErrorAnalysis.objects.create(
        trace_id=trace_id,
        project=other_project,
        overall_score=None,
        insights="Keep this organization's analysis unchanged.",
    )
    other_before = (
        TraceErrorAnalysis.objects.filter(pk=other_analysis.pk).values().get()
    )

    with no_background_work(trace_id, stream=scripted_tool_stream(read_call(trace_id))):
        result = _falcon_analyze_single_trace(trace_id, str(project.id))
        assert_worker_refused(result, trace_id)

    assert (
        TraceErrorAnalysis.objects.filter(pk=other_analysis.pk).values().get()
        == other_before
    )
    local = TraceErrorAnalysis.objects.filter(project=project, trace_id=trace_id)
    assert local.count() == int(existing_analysis)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("partial_response", [False, True])
def test_held_worker_does_not_open_failing_stream(project, partial_response):
    trace_id = str(uuid.uuid4())

    async def failed_stream(messages, tools=None):
        if partial_response:
            yield {
                "choices": [
                    {"delta": {"content": "Inspecting trace..."}, "finish_reason": None}
                ]
            }
        raise httpx.RemoteProtocolError("Provider stream interrupted")

    with no_background_work(trace_id, stream=failed_stream):
        result = _falcon_analyze_single_trace(trace_id, str(project.id))
        assert_worker_refused(result, trace_id)
    assert not TraceErrorAnalysis.objects.filter(trace_id=trace_id).exists()


def test_falcon_analysis_still_skips_oss_without_creating_agent(monkeypatch):
    from ee.falcon_ai import agent
    from ee.usage.deployment import DeploymentMode

    trace_id = str(uuid.uuid4())
    monkeypatch.setattr(DeploymentMode, "is_oss", staticmethod(lambda: True))
    constructor = Mock()
    monkeypatch.setattr(agent, "AgentLoop", constructor)

    result = _falcon_analyze_single_trace(trace_id, str(uuid.uuid4()))

    assert_worker_refused(result, trace_id)
    constructor.assert_not_called()
