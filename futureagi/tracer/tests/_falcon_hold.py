"""Contracts for the accounting hold. Never replace readiness or authorization."""

from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pkgutil import resolve_name
from unittest.mock import Mock, patch

import pytest

from ai_tools.base import ToolContext
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail

HOLD_CODE = "FALCON_BACKGROUND_ACCOUNTING_UNAVAILABLE"
HOLD_MESSAGE = (
    "Background multi-trace analysis is unavailable right now. "
    "No analysis was started and no results will appear on the Feed. "
    "Tell the user this explicitly; do not promise background results."
)


def assert_hold(code, message):
    assert code == HOLD_CODE
    assert HOLD_MESSAGE in message


def assert_worker_refused(result, trace_id):
    assert result["success"] is False and result["trace_id"] == trace_id
    assert "error_count" not in result
    assert_hold(result["error_code"], result["error"])


def assert_activity_refused(project_id, trace_ids):
    from temporalio.exceptions import ApplicationError

    from tracer.tasks.error_analysis import analyze_traces_on_demand

    with pytest.raises(ApplicationError) as denied:
        analyze_traces_on_demand(project_id, trace_ids)
    assert denied.value.non_retryable is True
    assert_hold(denied.value.type, str(denied.value))


def assert_tool_refused(context: ToolContext, outside_workspace, project_id, trace_ids):
    from ai_tools.registry import registry

    tool = registry.get("analyze_project_traces")
    params = {"project_id": project_id, "trace_ids": trace_ids}
    # The generic refusal does not disclose whether this project exists.
    hidden = tool.run(params, replace(context, workspace=outside_workspace))
    assert hidden.is_error and hidden.data is None
    assert_hold(hidden.error_code, hidden.content)
    denied = tool.run(params, context)
    assert denied.is_error and denied.data is None
    assert_hold(denied.error_code, denied.content)


def _snapshot(trace_ids):
    from ee.falcon_ai.models import Conversation, FalconUsage

    return [
        list(rows.order_by("pk").values())
        for rows in (
            TraceErrorAnalysis.objects.filter(trace_id__in=trace_ids),
            TraceErrorDetail.objects.filter(analysis__trace_id__in=trace_ids),
            Conversation.no_workspace_objects.all(),
            FalconUsage.objects.all(),
        )
    ]


@contextmanager
def no_background_work(*trace_ids, stream=None, summary=None):
    """Assert the listed calls were absent and the selected rows did not change."""
    from agentic_eval.core.embeddings.embedding_manager import ModelManager

    before = _snapshot(trace_ids)
    calls = []

    def probe(path, scenario=None):
        effect = scenario
        if effect is None:
            effect = AssertionError(f"Unexpected call: {path}")
        call = Mock(name=path, side_effect=effect)
        calls.append(call)
        return call

    # Resolve owners before patching: a newly imported sibling module must
    # not keep a patched reader or emitter in one of its module-level imports.
    patches = []
    scenarios = {
        "ee.falcon_ai.llm_client.FalconLLMClient.stream_completion": stream,
        "litellm.completion": summary,
    }
    for path in (
        "tracer.services.clickhouse.v2.get_reader",
        "tracer.queries.error_analysis.get_reader",
        "tracer.queries.feed.get_reader",
        "tracer.services.clickhouse.client.ClickHouseClient.__init__",
        "tracer.services.clickhouse.client.ClickHouseClient.insert",
        "tracer.queries.deep_analysis_state.set_running",
        "tracer.queries.deep_analysis_state.set_failed",
        "tracer.queries.deep_analysis_state.clear",
        "tracer.tasks.error_analysis.analyze_traces_on_demand.delay",
        "tracer.tasks.error_analysis.analyze_traces_on_demand.apply_async",
        "tracer.tasks.error_analysis.ThreadPoolExecutor",
        "ee.falcon_ai.agent.AgentLoop.__init__",
        "ee.falcon_ai.agent.AgentLoop._load_mcp_tools",
        "ee.falcon_ai.llm_client.FalconLLMClient.stream_completion",
        "ee.licensing.managed_ai.stream_chat_completion",
        "litellm.completion",
        "ee.usage.services.emitter.emit",
        "ee.usage.utils.usage_entries.log_and_deduct_cost_for_api_request",
        "tracer.queries.error_analysis.TraceErrorAnalysisDB.ingest_trace_error_embeddings",
        "agentic_eval.core.database.ch_vector.ClickHouseVectorDB.bulk_upsert_vectors",
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager.bulk_insert_vectors",
        "tracer.tasks.error_analysis.cluster_project_errors.delay",
        "tracer.tasks.error_analysis.cluster_project_errors.apply_async",
        "tracer.queries.error_clustering.ErrorClusteringDB.cluster_unclustered_errors",
        "ee.agenthub.traceerroragent.error_cluster.ErrorEmbeddingClusterer._compute_centroid",
        "ee.agenthub.traceerroragent.error_cluster.ErrorEmbeddingClusterer._update_centroid_incremental",
        "tracer.queries.error_clustering.ErrorClusteringDB.create_new_cluster",
        "tracer.queries.error_clustering.ErrorClusteringDB.add_to_existing_cluster",
    ):
        owner, _, attr = path.rpartition(".")
        patches.append(
            patch.object(resolve_name(owner), attr, probe(path, scenarios.get(path)))
        )

    with ExitStack() as stack:
        for boundary in patches:
            stack.enter_context(boundary)
        stack.enter_context(
            patch.object(ModelManager, "text_model", property(probe("text_model")))
        )
        yield
        for call in calls:
            call.assert_not_called()
        assert _snapshot(trace_ids) == before
