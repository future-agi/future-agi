"""
Per-project dedup wiring for the eval-clustering trigger.

Real coalescing is Temporal-server behaviour (USE_EXISTING conflict
policy), which a unit test can't exercise. What we *can* and must pin
down is the wiring that makes the server coalesce:

  * a deterministic per-project workflow id is used, and
  * the conflict policy is forwarded to client.start_workflow when
    provided, and
  * the default path (no policy) is byte-identical to before — so every
    existing .delay/.apply_async caller is unaffected.

If those three hold, N rapid same-project triggers all hand Temporal the
same workflow id + USE_EXISTING, which is exactly what collapses them to
one in-flight run.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tfc.temporal.drop_in.runner import _start_activity_async


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _patched_client():
    """Mock Temporal client whose start_workflow records its call."""
    client = MagicMock()
    client.namespace = "test-ns"
    client.start_workflow = AsyncMock(return_value=MagicMock())
    return client


def _start(**kwargs):
    """
    Invoke _start_activity_async with internal deps patched, return the
    kwargs that reached client.start_workflow.
    """
    client = _patched_client()
    with patch(
        "tfc.temporal.common.client.get_client", AsyncMock(return_value=client)
    ), patch("tfc.temporal.drop_in.workflow.TaskRunnerWorkflow", MagicMock()), patch(
        "tfc.temporal.drop_in.workflow.TaskRunnerInput", MagicMock()
    ):
        _run(
            _start_activity_async(
                "cluster_eval_results_task",
                ("proj-1",),
                {},
                "agent_compass",
                kwargs.get("task_id", "cluster_eval_results_task-abcd1234"),
                **(
                    {"id_conflict_policy": kwargs["id_conflict_policy"]}
                    if "id_conflict_policy" in kwargs
                    else {}
                ),
            )
        )
    assert client.start_workflow.await_count == 1
    return client.start_workflow.await_args.kwargs


def test_deterministic_workflow_id_from_task_id():
    """A per-project task_id yields a deterministic workflow id."""
    call = _start(task_id="eval-cluster-proj-1")
    assert call["id"] == "task-eval-cluster-proj-1"


def test_conflict_policy_forwarded_when_provided():
    """id_conflict_policy reaches start_workflow when passed."""
    sentinel = object()
    call = _start(task_id="eval-cluster-proj-1", id_conflict_policy=sentinel)
    assert call.get("id_conflict_policy") is sentinel


def test_default_path_unchanged_no_conflict_policy():
    """
    Zero blast radius: with no policy, start_workflow is called WITHOUT
    an id_conflict_policy kwarg — identical to pre-change behaviour for
    every existing caller.
    """
    call = _start(task_id="cluster_eval_results_task-abcd1234")
    assert "id_conflict_policy" not in call


def test_rapid_same_project_calls_all_coalesceable():
    """
    The TH-4789 acceptance proxy: 100 rapid triggers for the same project
    must all hand Temporal the *same* workflow id and USE_EXISTING, which
    is precisely what makes the server collapse them to <=1 in-flight run.
    """
    from temporalio.common import WorkflowIDConflictPolicy

    ids = set()
    policies = set()
    for _ in range(100):
        call = _start(
            task_id="eval-cluster-proj-1",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        ids.add(call["id"])
        policies.add(call.get("id_conflict_policy"))

    assert ids == {"task-eval-cluster-proj-1"}
    assert policies == {WorkflowIDConflictPolicy.USE_EXISTING}


def test_apply_async_forwards_task_id_and_policy():
    """
    apply_async (what the re-enabled trigger calls) must forward task_id
    + id_conflict_policy into start_activity, else the wiring above never
    gets the deterministic id.
    """
    from tfc.temporal.drop_in.decorator import temporal_activity

    @temporal_activity(queue="agent_compass")
    def dummy_task(project_id):
        return project_id

    sentinel = object()
    with patch(
        "tfc.temporal.drop_in.runner.start_activity", return_value="wf-1"
    ) as mock_start:
        dummy_task.apply_async(
            args=("proj-1",),
            task_id="eval-cluster-proj-1",
            id_conflict_policy=sentinel,
        )

    assert mock_start.call_count == 1
    _, kw = mock_start.call_args
    assert kw["task_id"] == "eval-cluster-proj-1"
    assert kw["id_conflict_policy"] is sentinel


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
