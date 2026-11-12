"""OBS-SW-30: execute the real dispatch body without Django or worker startup."""

import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest


@pytest.mark.parametrize(
    ("lifecycle_status", "force"),
    [("aborted", False), ("completed", True)],
    ids=["aborted-retry", "forced-completed-rerun"],
)
def test_lifecycle_dispatch_imports_task_before_enqueue(
    monkeypatch, lifecycle_status, force
):
    trace_id = str(UUID(int=1))
    project_id = UUID(int=2)
    generation = str(UUID(int=3))
    project_ids = [str(project_id)]
    lifecycle = SimpleNamespace(status=lifecycle_status, project_id=project_id)
    state = Mock(spec=["get_state", "begin", "abort"])
    state.get_state.return_value = lifecycle
    state.begin.return_value = generation

    task = Mock(spec=["delay"])
    tasks_module = ModuleType("tracer.tasks")
    tasks_module.run_deep_analysis_on_demand = task
    monkeypatch.setitem(sys.modules, tasks_module.__name__, tasks_module)
    gate_module = ModuleType("tfc.capabilities.background_analysis")
    gate_module.background_analysis_enabled = Mock(return_value=True)
    monkeypatch.setitem(sys.modules, gate_module.__name__, gate_module)

    feed_path = Path(__file__).resolve().parents[1] / "queries" / "feed.py"
    tree = ast.parse(feed_path.read_text(), filename=str(feed_path))
    dispatch = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch_deep_analysis"
    )
    namespace = {
        "DeepAnalysisDispatchResponse": SimpleNamespace,
        "background_analysis_state": state,
        "_cluster_qs_for_access": Mock(
            return_value=SimpleNamespace(exists=Mock(return_value=True))
        ),
        "_cluster_has_trace": Mock(return_value=True),
    }
    # Keep the production body intact, including its local imports and handler.
    exec(
        compile(ast.Module(body=[dispatch], type_ignores=[]), str(feed_path), "exec"),
        namespace,
    )

    response = namespace["dispatch_deep_analysis"](
        "cluster", trace_id, project_ids, force=force
    )

    assert response.status == "running"
    assert response.trace_id == trace_id
    state.get_state.assert_called_once_with(trace_id, project_ids=project_ids)
    state.begin.assert_called_once_with(project_id, trace_id)
    task.delay.assert_called_once_with(
        trace_id, True, project_id=str(project_id), generation=generation
    )
    state.abort.assert_not_called()


def _dispatch_with_switch_off(monkeypatch, lifecycle_status, force):
    trace_id = str(UUID(int=1))
    project_id = UUID(int=2)
    state = Mock(spec=["get_state", "begin", "abort"])
    state.get_state.return_value = SimpleNamespace(
        status=lifecycle_status, project_id=project_id
    )
    state.begin.return_value = str(UUID(int=3))

    task = Mock(spec=["delay"])
    tasks_module = ModuleType("tracer.tasks")
    tasks_module.run_deep_analysis_on_demand = task
    monkeypatch.setitem(sys.modules, tasks_module.__name__, tasks_module)
    gate = Mock(return_value=False)
    gate_module = ModuleType("tfc.capabilities.background_analysis")
    gate_module.background_analysis_enabled = gate
    monkeypatch.setitem(sys.modules, gate_module.__name__, gate_module)

    feed_path = Path(__file__).resolve().parents[1] / "queries" / "feed.py"
    tree = ast.parse(feed_path.read_text(), filename=str(feed_path))
    dispatch = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch_deep_analysis"
    )
    # No legacy-state names: a lifecycle trace must never read the legacy path.
    namespace = {
        "DeepAnalysisDispatchResponse": SimpleNamespace,
        "background_analysis_state": state,
        "_cluster_qs_for_access": Mock(
            return_value=SimpleNamespace(exists=Mock(return_value=True))
        ),
        "_cluster_has_trace": Mock(return_value=True),
    }
    exec(
        compile(ast.Module(body=[dispatch], type_ignores=[]), str(feed_path), "exec"),
        namespace,
    )
    response = namespace["dispatch_deep_analysis"](
        "cluster", trace_id, [str(project_id)], force=force
    )
    return response, trace_id, state, task, gate


@pytest.mark.parametrize(
    ("lifecycle_status", "force"),
    [
        ("retry_pending", False),
        ("running", False),
        ("aborted", False),
        ("aborted", True),
        ("completed", True),
    ],
    ids=[
        "retry-pending",
        "running",
        "aborted-retry",
        "forced-aborted-retry",
        "forced-completed-rerun",
    ],
)
def test_lifecycle_dispatch_switch_off_never_begins_or_enqueues(
    monkeypatch, lifecycle_status, force
):
    response, trace_id, state, task, gate = _dispatch_with_switch_off(
        monkeypatch, lifecycle_status, force
    )

    assert response.status == "temporarily_unavailable"
    assert response.trace_id == trace_id
    gate.assert_called_once_with()
    state.begin.assert_not_called()
    state.abort.assert_not_called()
    task.delay.assert_not_called()


def test_lifecycle_completed_result_reads_done_with_switch_off(monkeypatch):
    response, trace_id, state, task, gate = _dispatch_with_switch_off(
        monkeypatch, "completed", False
    )

    assert response.status == "done"
    assert response.trace_id == trace_id
    gate.assert_not_called()
    state.begin.assert_not_called()
    task.delay.assert_not_called()
