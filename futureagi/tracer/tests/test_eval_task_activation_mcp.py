"""The generated MCP update tool delegates to the shared activation policy."""

from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from mcp_server.api_executor import MCPRequestContext, executor
from mcp_server.generated_registry import registry
from tracer.models.eval_task import EvalTask, RunType
from tracer.tests.test_eval_task_activation_paths import historical


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_mcp_mode_change_persists_floor_before_dispatch(
    user, workspace, eval_task, monkeypatch
):
    activation = timezone.now()
    historical(eval_task, activation)
    clock = {"now": activation}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    context = MCPRequestContext(
        user=user, organization=user.organization, workspace=workspace
    )
    tool = registry.get("update_eval_task")
    assert tool.request["method"].upper() == "PATCH"
    assert tool.request["path"] == "/tracer/eval-task/update_eval_task/"
    snapshots = []

    def dispatch(task, **kwargs):
        assert not connection.in_atomic_block
        saved = EvalTask.no_workspace_objects.get(pk=task.pk)
        snapshots.append((saved.run_type, saved.start_time, saved.continuous_cursor))

    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync", dispatch
    )
    for elapsed in (timedelta(), timedelta(hours=1)):
        clock["now"] = activation + elapsed
        result = executor.execute_sync(
            tool,
            {
                "eval_task_id": str(eval_task.pk),
                "run_type": "continuous",
                "edit_type": "edit_rerun",
            },
            context,
        )
        assert result["task_id"] == str(eval_task.pk), result
        assert result["edit_type"] == "edit_rerun", result
    assert snapshots == [(RunType.CONTINUOUS, activation, None)] * 2
