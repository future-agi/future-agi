"""Contract coverage for the multi-agent trial tool: which of an app's workers
did what in one trial."""

from uuid import uuid4

from django.urls import resolve
from rest_framework.response import Response

from mcp_server.api_executor import DjangoAPIExecutor
from mcp_server.generated_registry import GeneratedToolRegistry
from simulate.views.run_results_v3 import CallExecutionV3WorkersView

WORKERS = "/simulate/v3/call-executions/{call_execution_id}/workers/"


def test_call_workers_reads_the_trials_workers_view():
    tool = GeneratedToolRegistry.from_manifest().get("get_call_workers")

    assert tool.group == "simulation"
    assert tool.request["method"] == "GET"
    assert tool.request["path"] == WORKERS
    assert tool.request["parameters"]["path"] == ["call_execution_id"]
    assert tool.input_schema["required"] == ["call_execution_id"]
    assert tool.annotations["readOnlyHint"] is True
    assert tool.annotations["openWorldHint"] is False
    concrete = WORKERS.replace("{call_execution_id}", str(uuid4()))
    assert resolve(concrete).func.view_class is CallExecutionV3WorkersView


def test_pending_traces_and_no_hand_off_pass_through():
    """The view answers 202 with pending while it collects traces, and 204 when
    the trial handed nothing to another agent."""
    tool = GeneratedToolRegistry.from_manifest().get("get_call_workers")
    pending = {
        "pending": True,
        "next_read_at": "2026-11-26T10:00:00Z",
        "participants_declared": True,
    }

    assert (
        DjangoAPIExecutor._normalize_response(tool, Response(pending, status=202))
        == pending
    )
    assert DjangoAPIExecutor._normalize_response(tool, Response(status=204)) is None
