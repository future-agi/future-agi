import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession


def make_same_org_other_workspace_trace(organization, user, *, trace_type="experiment"):
    suffix = uuid.uuid4().hex[:8]
    other_workspace = Workspace.objects.create(
        name=f"Other Trace Workspace {suffix}",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_project = Project.objects.create(
        name=f"Other Trace Project {suffix}",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type=trace_type,
        metadata={},
    )
    other_project_version = ProjectVersion.objects.create(
        project=other_project,
        name=f"Other Trace Run {suffix}",
        version="v1",
        metadata={},
        config=[],
    )
    other_session = TraceSession.objects.create(
        project=other_project,
        name=f"Other Trace Session {suffix}",
    )
    other_trace = Trace.objects.create(
        project=other_project,
        project_version=other_project_version,
        session=other_session if trace_type == "observe" else None,
        name=f"Other Trace {suffix}",
        input={"prompt": "hidden"},
        output={"response": "hidden"},
        tags=["hidden"],
    )
    other_span = ObservationSpan.objects.create(
        id=f"other_trace_span_{suffix}",
        project=other_project,
        project_version=other_project_version,
        trace=other_trace,
        name="Other Trace Span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=3),
        end_time=timezone.now(),
        latency_ms=250,
        cost=0.02,
        status="OK",
    )
    return (
        other_workspace,
        other_project,
        other_project_version,
        other_session,
        other_trace,
        other_span,
    )


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestTraceWorkspaceScopeAPI:
    def test_create_rejects_same_org_other_workspace_project(
        self, auth_client, organization, user
    ):
        _, other_project, other_project_version, _, _, _ = (
            make_same_org_other_workspace_trace(organization, user)
        )

        response = auth_client.post(
            "/tracer/trace/",
            {
                "project": str(other_project.id),
                "project_version": str(other_project_version.id),
                "name": "Cross Workspace Trace",
                "input": {"prompt": "cross"},
                "output": {"response": "cross"},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Trace.no_workspace_objects.filter(
            project=other_project,
            name="Cross Workspace Trace",
        ).exists()

    def test_patch_rejects_same_org_other_workspace_project(
        self, auth_client, organization, user, trace
    ):
        _, other_project, _, _, _, _ = make_same_org_other_workspace_trace(
            organization, user
        )

        response = auth_client.patch(
            f"/tracer/trace/{trace.id}/",
            {"project": str(other_project.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        trace.refresh_from_db()
        assert trace.project_id != other_project.id

    def test_update_tags_rejects_same_org_other_workspace_trace_without_mutating(
        self, auth_client, organization, user
    ):
        _, _, _, _, other_trace, _ = make_same_org_other_workspace_trace(
            organization, user
        )

        response = auth_client.patch(
            f"/tracer/trace/{other_trace.id}/tags/",
            {"tags": ["leaked"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        other_trace.refresh_from_db()
        assert other_trace.tags == ["hidden"]

    def test_custom_actions_reject_same_org_other_workspace_project_or_run(
        self, auth_client, organization, user
    ):
        _, other_project, other_project_version, _, other_trace, _ = (
            make_same_org_other_workspace_trace(
                organization, user, trace_type="observe"
            )
        )

        list_response = auth_client.get(
            "/tracer/trace/list_traces/",
            {"project_version_id": str(other_project_version.id)},
        )
        index_response = auth_client.get(
            "/tracer/trace/get_trace_id_by_index/",
            {
                "project_version_id": str(other_project_version.id),
                "trace_id": str(other_trace.id),
            },
        )
        observe_index_response = auth_client.get(
            "/tracer/trace/get_trace_id_by_index_observe/",
            {
                "project_id": str(other_project.id),
                "trace_id": str(other_trace.id),
            },
        )
        export_response = auth_client.get(
            "/tracer/trace/get_trace_export_data/",
            {"project_id": str(other_project.id)},
        )
        eval_names_response = auth_client.get(
            "/tracer/trace/get_eval_names/",
            {"project_id": str(other_project.id)},
        )
        graph_response = auth_client.post(
            "/tracer/trace/get_graph_methods/",
            {
                "project_id": str(other_project.id),
                "property": "Average",
                "filters": [],
                "interval": "day",
                "req_data_config": {"type": "SYSTEM_METRIC", "id": "latency"},
            },
            format="json",
        )
        agent_graph_response = auth_client.get(
            "/tracer/trace/agent_graph/",
            {"project_id": str(other_project.id)},
        )
        compare_response = auth_client.post(
            "/tracer/trace/compare_traces/",
            {
                "project_version_ids": [str(other_project_version.id)],
                "index": 0,
            },
            format="json",
        )

        assert list_response.status_code == status.HTTP_400_BAD_REQUEST
        assert index_response.status_code == status.HTTP_400_BAD_REQUEST
        assert observe_index_response.status_code == status.HTTP_400_BAD_REQUEST
        assert export_response.status_code == status.HTTP_400_BAD_REQUEST
        assert eval_names_response.status_code == status.HTTP_400_BAD_REQUEST
        assert graph_response.status_code == status.HTTP_400_BAD_REQUEST
        assert agent_graph_response.status_code == status.HTTP_400_BAD_REQUEST
        assert compare_response.status_code == status.HTTP_200_OK
        assert compare_response.data["result"]["total_traces"] == 0
        assert compare_response.data["result"]["trace_comparison"] == {}

    def test_get_properties_returns_graph_property_catalog(self, auth_client):
        response = auth_client.get("/tracer/trace/get_properties/")

        assert response.status_code == status.HTTP_200_OK
        assert "Average" in response.data["result"]
        assert "P95" in response.data["result"]

    def test_agent_graph_propagates_clickhouse_failure_without_pg_fallback(
        self, auth_client, project, observation_span, child_span, monkeypatch
    ):
        # Post-migration contract (DECISIONS #027): agent_graph is CH-only.
        # The legacy "CH fails → silently rebuild graph from PG" path was
        # removed because PG is no longer the source of truth — falling back
        # would return a partial graph that operators wrongly trust. CH errors
        # now surface as a 4xx with a diagnostic so the data pipeline gets
        # paged instead of silently degrading.
        monkeypatch.setattr(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService.execute_ch_query",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ch down")),
        )

        # AST-walk guard (per codex P2 findings 2026-05-25 and 2026-05-26):
        # asserting "got 400" is necessary but not sufficient — a future
        # regression could re-introduce the PG-fallback helper, have it ALSO
        # error, and still hand the operator a 400. The contract is "PG
        # fallback is never called from agent_graph". We assert this
        # statically by walking the function's bytecode and rejecting calls
        # to ANY name that pattern-matches a PG-fallback path. Catches:
        #   • direct call: _build_agent_graph_pg(...)
        #   • aliased call: _pg_agent_graph_fallback(...) or _agent_graph_pg(...)
        #   • module-attr call: trace_helpers._build_agent_graph_pg(...)
        # Stays robust against tracer.views.trace circular-import path at
        # test setup time that defeated a runtime-sentinel approach.
        import inspect
        from django.urls import get_resolver
        get_resolver().reverse_dict  # forces URL conf + view imports
        import sys
        trace_module = sys.modules["tracer.views.trace"]
        agent_graph_fn = trace_module.TraceView.agent_graph
        src = inspect.getsource(agent_graph_fn)

        # Forbid ANY identifier matching a PG-fallback pattern. The check is
        # over the function source so aliases and module-prefixed calls are
        # caught equally — re.search rather than substring to allow word-
        # boundary detection.
        import re
        forbidden = [
            r"\b_build_agent_graph_pg\b",
            r"\b_agent_graph_pg\b",
            r"\b_pg_agent_graph\w*\b",
            r"\b_fallback_to_pg\b",
            r"\bagent_graph_pg_fallback\b",
        ]
        for pat in forbidden:
            assert not re.search(pat, src), (
                f"PG-fallback identifier {pat!r} appears in agent_graph view "
                f"source. Post-D-027 contract: agent_graph is CH-only. Remove "
                f"any PG-fallback invocation in tracer/views/trace.py::agent_graph."
            )

        response = auth_client.get(
            "/tracer/trace/agent_graph/",
            {"project_id": str(project.id)},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            f"expected 400 when CH fails (no PG fallback); got {response.status_code}: "
            f"{getattr(response, 'data', None)!r}"
        )
        # Diagnostic must mention agent_graph so operators can route the alert.
        body = getattr(response, "data", {}) or {}
        message = (body.get("message") or body.get("detail") or "").lower()
        assert "agent graph" in message, (
            f"diagnostic must identify the failing endpoint; got {body!r}"
        )

    def test_generic_delete_cascades_trace_spans_and_eval_logs(
        self, auth_client, trace, observation_span, custom_eval_config
    ):
        eval_log = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=custom_eval_config,
            output_float=0.8,
        )

        response = auth_client.delete(f"/tracer/trace/{trace.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        trace.refresh_from_db()
        observation_span.refresh_from_db()
        eval_log.refresh_from_db()
        assert trace.deleted is True
        assert observation_span.deleted is True
        assert eval_log.deleted is True
