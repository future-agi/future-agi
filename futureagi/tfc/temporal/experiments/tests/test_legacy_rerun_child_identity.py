"""Exercise real rerun parent code against an open-child Temporal model.

No worker, provider or database is used by these tests. Child execution and
cell writes are modeled; this is not a server-history replay or an API test.
"""

import asyncio
from collections import Counter
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import fields
from types import SimpleNamespace

import pytest

from tfc.temporal.experiments import workflows
from tfc.temporal.experiments.types import RerunCellsV2WorkflowInput


PATCH_ID = "legacy-rerun-parent-child-id-v1"
CONFIG_ID = "12345678-1234-1234-1234-123456789abc"
OTHER_CONFIG_ID = "87654321-1234-1234-1234-123456789abc"
PARENT = ContextVar("rerun_parent")
HAS_NATIVE_RUNS = "native_runs" in {
    field.name for field in fields(RerunCellsV2WorkflowInput)
}


def _input(kind, row_id, config_id=CONFIG_ID, native_runs=None):
    value = RerunCellsV2WorkflowInput(
        experiment_id="experiment-1",
        dataset_id="snapshot-1",
        prompt_config_ids=[config_id] if kind == "prompt" else [],
        agent_config_ids=[config_id] if kind == "agent" else [],
        row_ids=[row_id],
        failed_only=True,
        eval_only=kind == "eval",
        task_queue="retry-test",
    )
    if native_runs is not None:
        value.native_runs = native_runs
    return value


def _old_child_id(kind, config_id=CONFIG_ID):
    prefix = "rerun-prompt-v2" if kind == "prompt" else "rerun-agent"
    return f"{prefix}-experiment-1-0-{config_id[:8]}"


class _OpenChildModel:
    """Reject duplicate open IDs and process only the rows passed to a child."""

    def __init__(self, monkeypatch, patched=True):
        self.release = asyncio.Event()
        self.attempted = {}
        self.attempts = []
        self.open_ids = set()
        self.children = []
        self.processing_calls = Counter()
        self.failed_parents = []
        self.patch_calls = []
        self.info_calls = []
        self.patch_enabled = patched
        self.cells = {
            "A": {"status": "error", "value": "old error A"},
            "B": {"status": "error", "value": "old error B"},
            "C": {"status": "pass", "value": {"answer": "keep me"}},
        }
        monkeypatch.setattr(workflows.workflow, "patched", self.patched)
        monkeypatch.setattr(workflows.workflow, "info", self.info)
        monkeypatch.setattr(
            workflows.workflow, "execute_activity", self.execute_activity
        )
        monkeypatch.setattr(
            workflows.workflow, "start_child_workflow", self.start_child
        )

    def patched(self, patch_id):
        self.patch_calls.append(patch_id)
        assert patch_id == PATCH_ID
        return self.patch_enabled

    def info(self):
        parent = PARENT.get()
        self.info_calls.append(parent)
        return parent

    async def execute_activity(self, name, value, **kwargs):
        if name in ("mark_experiment_running_activity", "setup_experiment_activity"):
            return {"status": "READY"}
        if name == "resolve_edt_columns_activity":
            return {"edt_column_ids": [], "row_ids": []}
        if name == "check_experiment_status_activity":
            running = any(cell["status"] == "running" for cell in self.cells.values())
            return {"status": "RUNNING" if running else "Completed"}
        if name == "mark_experiment_failed_activity":
            self.failed_parents.append(PARENT.get().workflow_id)
            return None
        raise AssertionError(f"Unexpected activity: {name}")

    async def start_child(self, run, value, *, id, task_queue):
        parent_id = PARENT.get().workflow_id
        self.attempts.append((id, value))
        self.attempted[parent_id].set()
        if id in self.open_ids:
            raise RuntimeError(f"Child workflow already running: {id}")
        assert task_queue == "retry-test"
        assert run in (workflows.ProcessPromptV2Workflow.run, workflows.ProcessAgentWorkflow.run)
        self.open_ids.add(id)

        async def process_selected_rows():
            try:
                await self.release.wait()
                for row_id in value.rerun_row_ids:
                    self.processing_calls[row_id] += 1
                    self.cells[row_id] = {"status": "pass", "value": f"new {row_id}"}
                return {"completed_rows": len(value.rerun_row_ids), "failed_rows": 0}
            finally:
                self.open_ids.remove(id)

        child = asyncio.create_task(process_selected_rows())
        self.children.append(child)
        return child

    def admit(self, parent_id, value, run_id="parent-run-1"):
        self.attempted[parent_id] = asyncio.Event()
        # Model the API's selected-cell reset, before a legacy parent starts.
        if not getattr(value, "native_runs", None):
            for row_id in value.row_ids:
                self.cells[row_id] = {"status": "running", "value": ""}

        async def run_parent():
            token = PARENT.set(SimpleNamespace(workflow_id=parent_id, run_id=run_id))
            try:
                return await workflows.RerunCellsV2Workflow().run(value)
            finally:
                PARENT.reset(token)

        return asyncio.create_task(run_parent())

    async def wait_for_child_attempt(self, parent_id):
        await asyncio.wait_for(self.attempted[parent_id].wait(), timeout=2)


async def _overlap(monkeypatch, kind, second_config):
    model = _OpenChildModel(monkeypatch)
    successful_cell = deepcopy(model.cells["C"])
    parents = [model.admit("accepted-request-A", _input(kind, "A"))]
    try:
        await model.wait_for_child_attempt("accepted-request-A")
        assert len(model.open_ids) == 1
        assert not model.children[0].done()
        assert not parents[0].done()
        parents.append(
            model.admit("accepted-request-B", _input(kind, "B", second_config))
        )
        await model.wait_for_child_attempt("accepted-request-B")
        assert not model.children[0].done(), "First child must still be running"
    finally:
        model.release.set()
        results = await asyncio.gather(*parents, return_exceptions=True)

    child_ids = [child_id for child_id, _ in model.attempts]
    # Baseline fails here: B requested A's still-open child, so B never ran.
    assert len(child_ids) == 2
    assert len(set(child_ids)) == 2, child_ids
    assert all(not isinstance(result, BaseException) for result in results), results
    assert all(parent.done() and not parent.cancelled() for parent in parents)
    assert [result.status for result in results] == ["Completed", "Completed"]
    assert [result.total_rows_processed for result in results] == [1, 1]
    assert [result.failed_rows for result in results] == [0, 0]
    assert model.processing_calls == Counter({"A": 1, "B": 1})
    assert model.cells["A"]["status"] == model.cells["B"]["status"] == "pass"
    assert model.cells["C"] == successful_cell
    assert not model.open_ids
    assert not model.failed_parents
    assert [value.rerun_row_ids for _, value in model.attempts] == [["A"], ["B"]]
    assert all(value.failed_only for _, value in model.attempts)
    assert all(value.dataset_id == "snapshot-1" for _, value in model.attempts)
    assert all(not getattr(value, "native_context", None) for _, value in model.attempts)
    return model


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prompt", "agent"])
async def test_overlapping_legacy_retries_process_each_selected_row_once(monkeypatch, kind):
    model = await _overlap(monkeypatch, kind, CONFIG_ID)
    assert [child_id for child_id, _ in model.attempts] == [
        f"{_old_child_id(kind)}-accepted-request-A",
        f"{_old_child_id(kind)}-accepted-request-B",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prompt", "agent"])
async def test_different_variant_retries_remain_independent(monkeypatch, kind):
    await _overlap(monkeypatch, kind, OTHER_CONFIG_ID)


async def _single(monkeypatch, kind, *, patched=True, parent_id="accepted-request-A",
                  run_id="parent-run-1", native_runs=None):
    model = _OpenChildModel(monkeypatch, patched=patched)
    before = deepcopy(model.cells)
    model.release.set()
    result = await model.admit(
        parent_id, _input(kind, "A", native_runs=native_runs), run_id=run_id
    )
    assert result.status == "Completed"
    assert result.failed_rows == 0
    assert model.cells["B"] == before["B"]
    assert model.cells["C"] == before["C"]
    assert not model.failed_parents
    assert not model.open_ids
    assert len(model.attempts) == 1
    if native_runs is None:
        assert result.total_rows_processed == 1
        assert model.processing_calls == Counter({"A": 1})
    return model


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prompt", "agent"])
async def test_unpatched_history_keeps_recorded_legacy_child_id(monkeypatch, kind):
    model = await _single(monkeypatch, kind, patched=False)
    assert model.attempts[0][0] == _old_child_id(kind)
    assert not model.info_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prompt", "agent"])
async def test_legacy_child_identity_is_stable_for_parent_replay_and_retry(monkeypatch, kind):
    identities = []
    for run_id in ("first-run", "first-run", "retried-run"):
        model = await _single(monkeypatch, kind, run_id=run_id)
        identities.append(model.attempts[0][0])
    assert identities == [f"{_old_child_id(kind)}-accepted-request-A"] * 3


@pytest.mark.asyncio
@pytest.mark.skipif(not HAS_NATIVE_RUNS, reason="This base has no native subset plans")
@pytest.mark.parametrize("kind", ["prompt", "agent", "eval"])
async def test_native_plan_identity_does_not_use_legacy_parent_patch(monkeypatch, kind):
    identities = []
    for parent_id, plan_id in (("parent-A", "plan-1"), ("parent-B", "plan-1"),
                               ("parent-C", "plan-2")):
        context = {
            "plan_id": plan_id,
            "plan_hash": "immutable-plan-hash",
            "execution_mode": {"prompt": "prompt", "agent": "graph", "eval": "eval_only"}[kind],
        }
        model = await _single(
            monkeypatch, kind, parent_id=parent_id, native_runs={CONFIG_ID: context}
        )
        child_id, child_input = model.attempts[0]
        identities.append(child_id)
        if kind == "prompt":
            expected = f"rerun-prompt-v2-experiment-1-{plan_id}-0-{CONFIG_ID[:8]}"
        elif kind == "agent":
            expected = f"rerun-agent-experiment-1-{plan_id}-{CONFIG_ID}"
        else:
            expected = f"rerun-eval-v2-experiment-1-{plan_id}-{CONFIG_ID}"
        assert child_id == expected
        assert child_input.native_context == context
        assert child_input.rerun_row_ids == []
        assert child_input.failed_only is False
        assert not model.patch_calls
        assert not model.info_calls
    assert identities[0] == identities[1]
    assert identities[0] != identities[2]
