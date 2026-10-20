"""The feed previews a cluster's traces from the cluster's own project.

A trace id can exist in several projects and organizations (a replay, a
re-import, a shared voice provider account). The issue detail's success and
representative previews and the Overview's working-trace reel read the trace's
root span by id; read unscoped they showed whichever copy started last — on
dev, 568 of 1,146 feed (cluster, trace) pairs whose trace has several copies
resolved to another project's copy. The sidebar's model / model version read
the trace's first LLM span by id the same way and kept the copy whose LLM span
started first — on dev, 518 of 1,062 such pairs. They now read the cluster's
project.

The ClickHouse reader is faked with per-project copies of each span, returned
in the real reads' ``(trace_id, start_time, id)`` order; Postgres runs for
real.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.queries import feed

pytestmark = pytest.mark.django_db

SUCCESS_TRACE = str(uuid.uuid4())
MEMBER_TRACE = str(uuid.uuid4())
# Only the other projects' copies of this trace carry an LLM span.
NO_OWN_LLM_TRACE = str(uuid.uuid4())


def _project(organization, workspace, name):
    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


class _Roots:
    """``get_reader()`` stand-in: roots and LLM spans of every copy, scoped by
    ``project_id`` and ordered by ``(trace_id, start_time, id)`` like the real
    reads."""

    def __init__(self, roots, llm_spans=()):
        self.roots = roots
        self.llm_spans = llm_spans

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def roots_by_trace_ids(self, trace_ids, *, project_id=None, **_):
        rows = [
            root
            for root in self.roots
            if root.trace_id in trace_ids
            and (not project_id or root.project_id == str(project_id))
        ]
        return sorted(rows, key=lambda r: (r.trace_id, r.start_time, r.id))

    def first_span_by_type(self, trace_id, observation_type, *, project_id=None):
        rows = [
            span
            for span in self.llm_spans
            if span.trace_id == trace_id
            and span.observation_type == observation_type.lower()
            and (not project_id or span.project_id == str(project_id))
        ]
        return min(rows, key=lambda r: (r.start_time, r.id), default=None)


@pytest.fixture
def cluster(organization, workspace, user):
    """A scanner cluster in ``own``. Its success and member traces are also
    held by ``other`` (same organization) and ``foreign`` (another
    organization), whose roots start later, so an unscoped read keeps them.
    Their LLM spans start earlier than ``own``'s, so an unscoped first-LLM-span
    read keeps theirs too."""
    own = _project(organization, workspace, "own")
    other = _project(organization, workspace, "other")
    foreign_org = Organization.objects.create(name=f"Foreign {uuid.uuid4().hex[:8]}")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="Foreign Workspace",
        organization=foreign_org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    foreign = _project(foreign_org, foreign_workspace, "foreign")
    group = TraceErrorGroup.objects.create(
        project=own,
        cluster_id="F4-SCOPE-1",
        error_type="tool_failure",
        issue_group="Tool Failures",
        title="Tool call failed",
        success_trace_id=SUCCESS_TRACE,
    )
    ErrorClusterTraces.objects.create(cluster=group, trace_id=MEMBER_TRACE)

    roots = [
        SimpleNamespace(
            id=f"root-{project.name}",
            project_id=str(project.id),
            trace_id=trace_id,
            parent_span_id="",
            start_time=datetime(2026, 9, 22, 16, 27, second, tzinfo=UTC),
            attrs_string={
                "input.value": f"{project.name} input",
                "output.value": f"{project.name} output",
            },
            input=None,
            output=None,
        )
        for trace_id in (SUCCESS_TRACE, MEMBER_TRACE)
        for project, second in ((own, 1), (other, 5), (foreign, 9))
    ]
    llm_spans = [
        SimpleNamespace(
            id=f"llm-{project.name}",
            project_id=str(project.id),
            trace_id=trace_id,
            observation_type="llm",
            start_time=datetime(2026, 9, 22, 16, 27, second, tzinfo=UTC),
            model=f"{project.name}-model",
            attrs_string={"gen_ai.request.model_version": f"{project.name}-v1"},
        )
        for trace_id, copies in (
            (MEMBER_TRACE, ((own, 30), (other, 20), (foreign, 10))),
            (NO_OWN_LLM_TRACE, ((other, 20), (foreign, 10))),
        )
        for project, second in copies
    ]
    with (
        patch.object(feed, "get_reader", lambda: _Roots(roots, llm_spans)),
        patch.object(feed, "is_voice_project", return_value=False),
        patch.object(feed, "_fetch_trends_batch", return_value={}),
        patch.object(feed, "_fetch_users_affected_batch", return_value={}),
        patch.object(feed, "_fetch_sessions_batch", return_value={}),
    ):
        yield group


def test_detail_previews_show_the_clusters_copy(cluster):
    detail = feed.get_cluster_detail(cluster.cluster_id, [str(cluster.project_id)])

    assert detail.representative_trace.trace_id == MEMBER_TRACE
    assert (detail.representative_trace.input, detail.representative_trace.output) == (
        "own input",
        "own output",
    )
    assert detail.success_trace.trace_id == SUCCESS_TRACE
    assert (detail.success_trace.input, detail.success_trace.output) == (
        "own input",
        "own output",
    )


def test_working_trace_reel_shows_the_clusters_copy(cluster):
    reel = feed._fetch_success_trace_pass_reel(
        cluster.cluster_id, str(cluster.project_id)
    )

    assert [step["text"] for step in reel] == ["own input", "own output"]


def test_sidebar_model_comes_from_the_clusters_copy(cluster):
    sidebar = feed.get_sidebar(cluster.cluster_id, [str(cluster.project_id)])

    assert sidebar.ai_metadata.trace_id == MEMBER_TRACE
    assert (sidebar.ai_metadata.model, sidebar.ai_metadata.model_version) == (
        "own-model",
        "own-v1",
    )


def test_sidebar_shows_no_model_when_only_other_copies_call_one(cluster):
    ErrorClusterTraces.objects.create(cluster=cluster, trace_id=NO_OWN_LLM_TRACE)

    sidebar = feed.get_sidebar(
        cluster.cluster_id, [str(cluster.project_id)], trace_id=NO_OWN_LLM_TRACE
    )

    assert sidebar.ai_metadata.trace_id == NO_OWN_LLM_TRACE
    assert (sidebar.ai_metadata.model, sidebar.ai_metadata.model_version) == (
        None,
        None,
    )
