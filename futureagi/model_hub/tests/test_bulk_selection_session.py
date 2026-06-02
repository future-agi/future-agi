"""Phase 6 — Session resolver tests.

Mirrors Phase 1/4 structure for ``resolve_filtered_session_ids``.
Parity is checked against ``/tracer/trace-sessions/list_sessions/``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.services.bulk_selection import (
    ResolveResult,
    resolve_filtered_session_ids,
)
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def observe_project(db, organization, workspace):
    return Project.objects.create(
        name="BulkSel Session Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def seeded_sessions(db, observe_project):
    """10 sessions, each with one trace + one span, staggered start_times.

    Latest-first ordering: ``[-1]`` is newest.
    """
    now = timezone.now()
    sessions = []
    for i in range(10):
        session = TraceSession.objects.create(
            project=observe_project,
            name=f"session-{i}",
            bookmarked=False,
        )
        trace = Trace.objects.create(
            project=observe_project,
            session=session,
            name=f"trace-{i}",
        )
        ObservationSpan.objects.create(
            id=f"sp-sess-{i}-{session.id.hex[:8]}",
            project=observe_project,
            trace=trace,
            name=f"sp-{i}",
            observation_type="llm",
            start_time=now + timedelta(minutes=i),
            end_time=now + timedelta(minutes=i, seconds=1),
            parent_span_id=None,
            cost=0.0,
            total_tokens=0,
        )
        sessions.append(session)
    return sessions


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestBaseline:
    def test_no_filter_returns_all_project_sessions(
        self, observe_project, seeded_sessions, organization
    ):
        result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        assert isinstance(result, ResolveResult)
        assert result.total_matching == 10
        assert len(result.ids) == 10
        assert result.truncated is False

    def test_no_filter_ordered_by_start_time_desc(
        self, observe_project, seeded_sessions, organization
    ):
        result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        assert result.ids[0] == seeded_sessions[-1].id
        assert result.ids[-1] == seeded_sessions[0].id

    def test_none_filters_equivalent_to_empty(
        self, observe_project, seeded_sessions, organization
    ):
        result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=None,  # type: ignore[arg-type]
            organization=organization,
        )
        assert result.total_matching == 10


# --------------------------------------------------------------------------
# exclude_ids
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestExcludeIds:
    def test_excludes_given_ids_from_result(
        self, observe_project, seeded_sessions, organization
    ):
        exclude = {seeded_sessions[0].id, seeded_sessions[1].id}
        result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=exclude,
            organization=organization,
        )
        assert result.total_matching == 8
        assert len(result.ids) == 8
        for excluded_id in exclude:
            assert excluded_id not in result.ids

    def test_exclude_accepts_list_and_tuple(
        self, observe_project, seeded_sessions, organization
    ):
        list_result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=[seeded_sessions[0].id],
            organization=organization,
        )
        assert list_result.total_matching == 9

        tuple_result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=(seeded_sessions[1].id,),
            organization=organization,
        )
        assert tuple_result.total_matching == 9

    def test_exclude_none_is_noop(
        self, observe_project, seeded_sessions, organization
    ):
        result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=None,
            organization=organization,
        )
        assert result.total_matching == 10


# --------------------------------------------------------------------------
# Cap enforcement
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestCap:
    def test_cap_truncates_ids(
        self, observe_project, seeded_sessions, organization
    ):
        result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            cap=4,
        )
        assert len(result.ids) == 4
        # Capped resolvers return a cap+1 sentinel instead of a precise count
        # so preview calls do not run an extra full COUNT(*) on large tables.
        assert result.total_matching == 5
        assert result.truncated is True

    def test_cap_above_total_is_not_truncated(
        self, observe_project, seeded_sessions, organization
    ):
        result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            cap=100,
        )
        assert result.truncated is False
        assert len(result.ids) == 10

    def test_cap_returns_most_recent_first(
        self, observe_project, seeded_sessions, organization
    ):
        result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            cap=3,
        )
        assert result.ids == [s.id for s in seeded_sessions[-1:-4:-1]]


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestIsolation:
    def test_org_isolation(
        self, observe_project, seeded_sessions, organization, db
    ):
        other_org = Organization.objects.create(name="Other Session Org")
        other_project = Project.objects.create(
            name="Other Session Project",
            organization=other_org,
            workspace=None,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        with pytest.raises(Project.DoesNotExist):
            resolve_filtered_session_ids(
                project_id=other_project.id,
                filters=[],
                organization=organization,
            )

    def test_workspace_isolation(
        self, observe_project, seeded_sessions, organization, workspace, user, db
    ):
        other_ws = Workspace.objects.create(
            name="Other WS",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            workspace=other_ws,
        )
        assert result.total_matching == 0
        assert result.ids == []

    def test_project_scoping(
        self, observe_project, seeded_sessions, organization, workspace
    ):
        sibling = Project.objects.create(
            name="Sibling Session Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        sibling_session = TraceSession.objects.create(project=sibling, name="sib")
        sibling_trace = Trace.objects.create(project=sibling, session=sibling_session, name="st")
        ObservationSpan.objects.create(
            id=f"sib-{sibling_session.id.hex[:8]}",
            project=sibling,
            trace=sibling_trace,
            name="sib-span",
            observation_type="llm",
            start_time=timezone.now(),
            end_time=timezone.now(),
            parent_span_id=None,
        )
        result = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        assert sibling_session.id not in result.ids
        assert result.total_matching == 10


# --------------------------------------------------------------------------
# List-endpoint parity
# --------------------------------------------------------------------------


def _list_endpoint_session_ids(auth_client, project_id, filters):
    import json

    resp = auth_client.get(
        "/tracer/trace-session/list_sessions/",
        {
            "project_id": str(project_id),
            "filters": json.dumps(filters),
            "sort_params": "[]",
            "page_number": 0,
            "page_size": 200,
        },
    )
    assert resp.status_code == 200, resp.data
    table = (resp.data.get("result") or {}).get("table", [])
    ids = set()
    for row in table:
        sid = row.get("session_id") or row.get("id") or row.get("trace__session_id")
        if sid is not None:
            ids.add(str(sid))
    return ids


@pytest.mark.django_db
class TestParityWithListEndpoint:
    def test_parity_no_filter(
        self, auth_client, observe_project, seeded_sessions, organization
    ):
        resolver = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        list_ids = _list_endpoint_session_ids(auth_client, observe_project.id, [])
        assert {str(i) for i in resolver.ids} == list_ids

    def test_parity_empty_filter_after_exclude(
        self, auth_client, observe_project, seeded_sessions, organization
    ):
        excluded = {seeded_sessions[0].id, seeded_sessions[5].id}
        resolver = resolve_filtered_session_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=excluded,
            organization=organization,
        )
        list_ids = _list_endpoint_session_ids(auth_client, observe_project.id, [])
        expected = list_ids - {str(i) for i in excluded}
        assert {str(i) for i in resolver.ids} == expected
