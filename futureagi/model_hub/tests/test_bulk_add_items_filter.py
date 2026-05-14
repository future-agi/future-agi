"""Phase 2 — ``add-items`` endpoint filter-mode tests.

Covers:
  - Backward compat: the existing ``items`` payload still works.
  - Filter-mode: happy path, exclude_ids, duplicates, truncation 400.
  - Validation: both payload forms together, neither present,
    unsupported mode, unsupported source_type.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
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
        name="BulkAdd Observe Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def active_queue(db, auth_client):
    resp = auth_client.post(
        "/model-hub/annotation-queues/",
        {"name": "Bulk Test Queue"},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    # Some endpoints wrap the body in {"result": {...}}, others return the
    # object directly — handle both.
    body = resp.data.get("result", resp.data) if isinstance(resp.data, dict) else resp.data
    return AnnotationQueue.objects.get(id=body["id"])


def _add_items_url(queue_id):
    return f"/model-hub/annotation-queues/{queue_id}/items/add-items/"


def _api_filter(column_id, filter_type, filter_op, filter_value):
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


# --------------------------------------------------------------------------
# Backward compat — existing ``items`` payload
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddItemsEnumeratedRegression:
    def test_enumerated_happy_path(
        self, auth_client, active_queue, observe_project
    ):
        t = Trace.objects.create(project=observe_project, name="t1")
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {"items": [{"source_type": "trace", "source_id": str(t.id)}]},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 1
        assert result["duplicates"] == 0
        assert result["errors"] == []

    def test_enumerated_duplicate_detection(
        self, auth_client, active_queue, observe_project
    ):
        t = Trace.objects.create(project=observe_project, name="t-dup")
        payload = {"items": [{"source_type": "trace", "source_id": str(t.id)}]}
        auth_client.post(
            _add_items_url(active_queue.id), payload, format="json"
        )
        resp = auth_client.post(
            _add_items_url(active_queue.id), payload, format="json"
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 0
        assert result["duplicates"] == 1


# --------------------------------------------------------------------------
# Filter-mode — happy + exclude + duplicates + truncation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddItemsFilterMode:
    def test_filter_mode_no_filter_adds_all_project_traces(
        self, auth_client, active_queue, observe_project
    ):
        for i in range(3):
            Trace.objects.create(project=observe_project, name=f"t-{i}")

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 3
        assert result["duplicates"] == 0
        assert result["errors"] == []
        assert result["total_matching"] == 3

    def test_filter_mode_respects_exclude_ids(
        self, auth_client, active_queue, observe_project
    ):
        traces = [
            Trace.objects.create(project=observe_project, name=f"t-{i}")
            for i in range(5)
        ]
        exclude = [str(traces[0].id), str(traces[1].id)]

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                    "exclude_ids": exclude,
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 3
        assert result["total_matching"] == 3

    def test_filter_mode_counts_existing_as_duplicates(
        self, auth_client, active_queue, observe_project
    ):
        traces = [
            Trace.objects.create(project=observe_project, name=f"t-{i}")
            for i in range(3)
        ]
        # Pre-add one via the enumerated path.
        auth_client.post(
            _add_items_url(active_queue.id),
            {"items": [{"source_type": "trace", "source_id": str(traces[0].id)}]},
            format="json",
        )
        # Filter-add all — expect 2 fresh, 1 duplicate.
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 2
        assert result["duplicates"] == 1
        assert result["total_matching"] == 3

    def test_filter_mode_truncation_returns_400_selection_too_large(
        self, auth_client, active_queue, observe_project
    ):
        # Override the view-level cap for this test so we don't need to
        # seed 10_001 rows.
        import model_hub.views.annotation_queues as views_mod

        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            for i in range(3):
                Trace.objects.create(project=observe_project, name=f"t-{i}")
            resp = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        "mode": "filter",
                        "source_type": "trace",
                        "project_id": str(observe_project.id),
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap

        assert resp.status_code == 400, resp.data
        err = resp.data.get("error") or {}
        assert err.get("type") == "selection_too_large"
        assert err.get("total_matching") == 3
        assert err.get("cap") == 2

    def test_filter_mode_queue_item_count_matches_added(
        self, auth_client, active_queue, observe_project
    ):
        for i in range(4):
            Trace.objects.create(project=observe_project, name=f"t-{i}")

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 4

        # Verify via a separate GET that the queue actually holds those items.
        list_resp = auth_client.get(
            f"/model-hub/annotation-queues/{active_queue.id}/items/"
        )
        assert list_resp.status_code == 200, list_resp.data
        assert list_resp.data["count"] == 4


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddItemsValidation:
    def test_both_items_and_selection_rejected(
        self, auth_client, active_queue, observe_project
    ):
        t = Trace.objects.create(project=observe_project, name="t")
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "items": [{"source_type": "trace", "source_id": str(t.id)}],
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                },
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_neither_items_nor_selection_rejected(
        self, auth_client, active_queue
    ):
        resp = auth_client.post(_add_items_url(active_queue.id), {}, format="json")
        assert resp.status_code == 400

    def test_unsupported_selection_mode(
        self, auth_client, active_queue, observe_project
    ):
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "ids",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_unsupported_source_type(
        self, auth_client, active_queue, observe_project
    ):
        # All four source types (trace / observation_span / trace_session /
        # call_execution) are supported after Phase 8. This test keeps the
        # validation path covered by trying an obviously wrong value.
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "dataset_row",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 400


# --------------------------------------------------------------------------
# Phase 4 — filter-mode for source_type=observation_span
# --------------------------------------------------------------------------


def _make_span(project, trace, *, name, offset_minutes=0):
    now = timezone.now()
    return ObservationSpan.objects.create(
        id=f"sp-{name}-{trace.id.hex[:6]}",
        project=project,
        trace=trace,
        name=name,
        observation_type="llm",
        start_time=now + timezone.timedelta(minutes=offset_minutes)
        if hasattr(timezone, "timedelta")
        else now,
        end_time=now,
        parent_span_id=None,
    )


@pytest.fixture
def span_parent_trace(db, observe_project):
    return Trace.objects.create(project=observe_project, name="span-parent")


@pytest.mark.django_db
class TestAddItemsFilterModeSpan:
    def test_filter_mode_span_no_filter_adds_all(
        self, auth_client, active_queue, observe_project, span_parent_trace
    ):
        from datetime import timedelta

        now = timezone.now()
        for i in range(3):
            ObservationSpan.objects.create(
                id=f"sp-{i}-{span_parent_trace.id.hex[:6]}",
                project=observe_project,
                trace=span_parent_trace,
                name=f"sp-{i}",
                observation_type="llm",
                start_time=now - timedelta(minutes=i),
                end_time=now - timedelta(minutes=i) + timedelta(seconds=1),
                parent_span_id=None,
            )

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "observation_span",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 3
        assert result["duplicates"] == 0
        assert result["total_matching"] == 3

    def test_filter_mode_span_respects_exclude_ids(
        self, auth_client, active_queue, observe_project, span_parent_trace
    ):
        from datetime import timedelta

        now = timezone.now()
        spans = []
        for i in range(5):
            spans.append(
                ObservationSpan.objects.create(
                    id=f"spx-{i}-{span_parent_trace.id.hex[:6]}",
                    project=observe_project,
                    trace=span_parent_trace,
                    name=f"spx-{i}",
                    observation_type="llm",
                    start_time=now - timedelta(minutes=i),
                    end_time=now - timedelta(minutes=i) + timedelta(seconds=1),
                    parent_span_id=None,
                )
            )
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "observation_span",
                    "project_id": str(observe_project.id),
                    "exclude_ids": [spans[0].id, spans[1].id],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 3
        assert resp.data["result"]["total_matching"] == 3

    def test_filter_mode_span_counts_existing_as_duplicates(
        self, auth_client, active_queue, observe_project, span_parent_trace
    ):
        from datetime import timedelta

        now = timezone.now()
        spans = []
        for i in range(3):
            spans.append(
                ObservationSpan.objects.create(
                    id=f"spd-{i}-{span_parent_trace.id.hex[:6]}",
                    project=observe_project,
                    trace=span_parent_trace,
                    name=f"spd-{i}",
                    observation_type="llm",
                    start_time=now - timedelta(minutes=i),
                    end_time=now - timedelta(minutes=i) + timedelta(seconds=1),
                    parent_span_id=None,
                )
            )
        # Pre-add one via enumerated path
        auth_client.post(
            _add_items_url(active_queue.id),
            {
                "items": [
                    {"source_type": "observation_span", "source_id": spans[0].id}
                ]
            },
            format="json",
        )
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "observation_span",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 2
        assert resp.data["result"]["duplicates"] == 1

    def test_filter_mode_span_truncation_returns_400(
        self, auth_client, active_queue, observe_project, span_parent_trace
    ):
        from datetime import timedelta

        import model_hub.views.annotation_queues as views_mod

        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            now = timezone.now()
            for i in range(3):
                ObservationSpan.objects.create(
                    id=f"spt-{i}-{span_parent_trace.id.hex[:6]}",
                    project=observe_project,
                    trace=span_parent_trace,
                    name=f"spt-{i}",
                    observation_type="llm",
                    start_time=now - timedelta(minutes=i),
                    end_time=now - timedelta(minutes=i) + timedelta(seconds=1),
                    parent_span_id=None,
                )
            resp = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        "mode": "filter",
                        "source_type": "observation_span",
                        "project_id": str(observe_project.id),
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap

        assert resp.status_code == 400, resp.data
        err = resp.data.get("error") or {}
        assert err.get("type") == "selection_too_large"
        assert err.get("total_matching") == 3
        assert err.get("cap") == 2


# --------------------------------------------------------------------------
# Phase 6 — filter-mode for source_type=trace_session
# --------------------------------------------------------------------------


@pytest.fixture
def seeded_sessions_for_dispatch(db, observe_project):
    """3 sessions with spans for endpoint-level session dispatch tests."""
    from datetime import timedelta

    from tracer.models.trace_session import TraceSession

    now = timezone.now()
    sessions = []
    for i in range(3):
        s = TraceSession.objects.create(
            project=observe_project, name=f"ds-{i}", bookmarked=False
        )
        t = Trace.objects.create(project=observe_project, session=s, name=f"dt-{i}")
        ObservationSpan.objects.create(
            id=f"sp-disp-{i}-{s.id.hex[:6]}",
            project=observe_project,
            trace=t,
            name=f"sp-{i}",
            observation_type="llm",
            start_time=now - timedelta(minutes=i),
            end_time=now - timedelta(minutes=i) + timedelta(seconds=1),
            parent_span_id=None,
            cost=0.0,
            total_tokens=0,
        )
        sessions.append(s)
    return sessions


@pytest.mark.django_db
class TestAddItemsFilterModeSession:
    def test_filter_mode_session_no_filter_adds_all(
        self, auth_client, active_queue, observe_project, seeded_sessions_for_dispatch
    ):
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace_session",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 3
        assert resp.data["result"]["total_matching"] == 3

    @pytest.mark.api
    def test_filter_mode_session_date_filter_matches_list_endpoint(
        self, auth_client, active_queue, observe_project, seeded_sessions_for_dispatch
    ):
        now = timezone.now()
        recent_start = now - timedelta(days=7)
        recent_end = now + timedelta(days=1)
        recent_sessions = seeded_sessions_for_dispatch[:2]
        old_session = seeded_sessions_for_dispatch[2]

        TraceSession.objects.filter(id=recent_sessions[0].id).update(
            created_at=now - timedelta(days=2)
        )
        TraceSession.objects.filter(id=recent_sessions[1].id).update(
            created_at=now - timedelta(days=1)
        )
        TraceSession.objects.filter(id=old_session.id).update(
            created_at=now - timedelta(days=90)
        )

        filters = [
            _api_filter(
                "created_at",
                "datetime",
                "between",
                [recent_start.isoformat(), recent_end.isoformat()],
            )
        ]
        expected_ids = {str(session.id) for session in recent_sessions}

        list_resp = auth_client.get(
            "/tracer/trace-session/list_sessions/",
            {
                "project_id": str(observe_project.id),
                "filters": json.dumps(filters),
                "sort_params": "[]",
                "page_number": 0,
                "page_size": 20,
            },
        )
        assert list_resp.status_code == 200, list_resp.data
        list_ids = {
            row["session_id"]
            for row in list_resp.data["result"]["table"]
        }
        assert list_ids == expected_ids

        add_resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace_session",
                    "project_id": str(observe_project.id),
                    "filter": filters,
                }
            },
            format="json",
        )
        assert add_resp.status_code == 200, add_resp.data
        assert add_resp.data["result"]["added"] == 2
        assert add_resp.data["result"]["total_matching"] == 2

        queue_session_ids = {
            str(item.trace_session_id)
            for item in QueueItem.objects.filter(
                queue=active_queue,
                source_type="trace_session",
                deleted=False,
            )
        }
        assert queue_session_ids == expected_ids

    def test_filter_mode_session_respects_exclude_ids(
        self, auth_client, active_queue, observe_project, seeded_sessions_for_dispatch
    ):
        exclude = [str(seeded_sessions_for_dispatch[0].id)]
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace_session",
                    "project_id": str(observe_project.id),
                    "exclude_ids": exclude,
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 2

    def test_filter_mode_session_truncation_returns_400(
        self, auth_client, active_queue, observe_project, seeded_sessions_for_dispatch
    ):
        import model_hub.views.annotation_queues as views_mod

        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            resp = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        "mode": "filter",
                        "source_type": "trace_session",
                        "project_id": str(observe_project.id),
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap
        assert resp.status_code == 400, resp.data
        err = resp.data.get("error") or {}
        assert err.get("type") == "selection_too_large"
        assert err.get("total_matching") == 3
        assert err.get("cap") == 2


# --------------------------------------------------------------------------
# Phase 8 — filter-mode for source_type=call_execution
#
# For call_execution, ``selection.project_id`` is reinterpreted as the
# agent_definition_id — see Phase 8 PRD.
# --------------------------------------------------------------------------


@pytest.fixture
def seeded_call_executions_for_dispatch(db, organization, workspace):
    from simulate.models.agent_definition import AgentDefinition
    from simulate.models.run_test import RunTest
    from simulate.models.scenarios import Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution

    agent_def = AgentDefinition.objects.create(
        agent_name="ce-disp-agent",
        inbound=True,
        description="dispatch fixture",
        organization=organization,
        workspace=workspace,
    )
    run = RunTest.objects.create(name="ce-disp-run", organization=organization)
    te = TestExecution.objects.create(run_test=run, agent_definition=agent_def)
    scen = Scenarios.objects.create(
        name="ce-disp-scenario",
        source="dispatch",
        organization=organization,
        workspace=workspace,
    )
    ces = [
        CallExecution.objects.create(test_execution=te, scenario=scen)
        for _ in range(3)
    ]
    return agent_def, ces


@pytest.mark.django_db
class TestAddItemsFilterModeCallExecution:
    def test_filter_mode_ce_no_filter_adds_all(
        self, auth_client, active_queue, seeded_call_executions_for_dispatch
    ):
        agent_def, _ = seeded_call_executions_for_dispatch
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 3
        assert resp.data["result"]["total_matching"] == 3

    def test_filter_mode_ce_respects_exclude_ids(
        self, auth_client, active_queue, seeded_call_executions_for_dispatch
    ):
        agent_def, ces = seeded_call_executions_for_dispatch
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "exclude_ids": [str(ces[0].id)],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 2

    def test_filter_mode_ce_truncation_returns_400(
        self, auth_client, active_queue, seeded_call_executions_for_dispatch
    ):
        import model_hub.views.annotation_queues as views_mod

        agent_def, _ = seeded_call_executions_for_dispatch
        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            resp = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        "mode": "filter",
                        "source_type": "call_execution",
                        "project_id": str(agent_def.id),
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap
        assert resp.status_code == 400, resp.data
        err = resp.data.get("error") or {}
        assert err.get("type") == "selection_too_large"
        assert err.get("total_matching") == 3
        assert err.get("cap") == 2


# --------------------------------------------------------------------------
# Manual add-items + filter-mode + non-created_at filters on call_execution.
#
# Before _apply_call_execution_filters existed, the resolver only honored
# created_at filters and silently match-all'd everything else. The fixture
# below seeds calls with mixed status/duration so we can prove status
# and duration_seconds filters now actually narrow the result.
# --------------------------------------------------------------------------


@pytest.fixture
def seeded_mixed_call_executions(db, organization, workspace):
    from simulate.models.agent_definition import AgentDefinition
    from simulate.models.run_test import RunTest
    from simulate.models.scenarios import Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution

    agent_def = AgentDefinition.objects.create(
        agent_name="ce-mixed-agent",
        inbound=True,
        description="mixed-status fixture",
        organization=organization,
        workspace=workspace,
    )
    run = RunTest.objects.create(name="ce-mixed-run", organization=organization)
    te = TestExecution.objects.create(run_test=run, agent_definition=agent_def)
    scen = Scenarios.objects.create(
        name="ce-mixed-scenario",
        source="mixed",
        organization=organization,
        workspace=workspace,
    )
    completed_short = CallExecution.objects.create(
        test_execution=te, scenario=scen,
        status="completed", duration_seconds=10,
    )
    completed_long = CallExecution.objects.create(
        test_execution=te, scenario=scen,
        status="completed", duration_seconds=120,
    )
    failed = CallExecution.objects.create(
        test_execution=te, scenario=scen,
        status="failed", duration_seconds=30,
    )
    return agent_def, completed_short, completed_long, failed


@pytest.mark.django_db
class TestAddItemsFilterModeCallExecutionRichFilters:
    def test_filter_mode_status_filter_narrows_result(
        self, auth_client, active_queue, seeded_mixed_call_executions
    ):
        agent_def, *_ = seeded_mixed_call_executions
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "filter": [
                        {
                            "column_id": "status",
                            "filter_config": {
                                "filter_type": "categorical",
                                "filter_op": "equals",
                                "filter_value": "completed",
                            },
                        }
                    ],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        # Only the 2 completed calls — NOT the failed one. Pre-fix this
        # would have added all 3 because the filter was silently dropped.
        assert resp.data["result"]["added"] == 2
        assert resp.data["result"]["total_matching"] == 2

    def test_filter_mode_duration_range_narrows_result(
        self, auth_client, active_queue, seeded_mixed_call_executions
    ):
        agent_def, *_ = seeded_mixed_call_executions
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "filter": [
                        {
                            "column_id": "duration_seconds",
                            "filter_config": {
                                "filter_type": "number",
                                "filter_op": "less_than",
                                "filter_value": 60,
                            },
                        }
                    ],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        # 10s + 30s match; 120s excluded.
        assert resp.data["result"]["added"] == 2
        assert resp.data["result"]["total_matching"] == 2

    def test_filter_mode_unsupported_column_returns_400(
        self, auth_client, active_queue, seeded_mixed_call_executions
    ):
        agent_def, *_ = seeded_mixed_call_executions
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "filter": [
                        {
                            "column_id": "totally_made_up_column",
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "equals",
                                "filter_value": "x",
                            },
                        }
                    ],
                }
            },
            format="json",
        )
        # ValueError from resolver -> bad_request. Better than the old
        # silent match-all behaviour.
        assert resp.status_code == 400, resp.data
        body = resp.data.get("result") or resp.data.get("message") or ""
        assert "totally_made_up_column" in str(body) or "cannot apply" in str(body)

    def test_filter_mode_combined_status_and_duration(
        self, auth_client, active_queue, seeded_mixed_call_executions
    ):
        agent_def, *_ = seeded_mixed_call_executions
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "filter": [
                        {
                            "column_id": "status",
                            "filter_config": {
                                "filter_type": "categorical",
                                "filter_op": "equals",
                                "filter_value": "completed",
                            },
                        },
                        {
                            "column_id": "duration_seconds",
                            "filter_config": {
                                "filter_type": "number",
                                "filter_op": "less_than",
                                "filter_value": 60,
                            },
                        },
                    ],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        # Only completed_short (10s, completed) matches both filters.
        assert resp.data["result"]["added"] == 1
        assert resp.data["result"]["total_matching"] == 1
