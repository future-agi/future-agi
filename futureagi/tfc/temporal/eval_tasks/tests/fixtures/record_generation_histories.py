"""Record the eval-task replay fixtures used by ``test_generation_replay.py``.

Run from the ``futureagi`` directory of the checkout whose workflow code should
produce the histories (``--label base`` on dev at #4199's merge base,
``--label new`` on the branch itself):

    DJANGO_SETTINGS_MODULE=tfc.settings.test \\
        python <this file> --label base --out <fixtures dir>

It starts a local Temporal dev server, runs each scenario with stub activities
(registered by name, so the workflow code under test is the checkout's own)
and writes one JSON history per run of the continue-as-new chain. No database
is touched. The workflow input is sent as a plain dict, which is exactly what
a newer starter's dataclass serialises to, so ``base`` can be handed a
``workflow_generation`` its own input type does not declare.

``record_histories`` is also what the e2e reverse-replay test calls against
its in-memory server, so the suite never depends on re-recorded files.
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

TASK_QUEUE = "synthetic-replay"
STALE_GENERATION = "stale-generation"
SELECTION_REJECTED = (
    "This evaluation task is too large for safe row selection. "
    "Reduce the row limit and retry."
)

# name -> (workflow type, input, scenario). Every scenario has its own task id,
# so the stub activities below can follow it across continue-as-new hops.
HISTORICAL_BASE = {"batch_size": 2, "max_concurrent": 2}
CONTINUOUS_BASE = {"batch_size": 2, "max_concurrent": 2, "poll_interval_seconds": 1}


def _scenarios(label: str) -> dict:
    gen = {"workflow_generation": "g-1"}
    out = {
        # Historical: drain with two continue-as-new hops, and a failed start.
        "historical-can": (
            "HistoricalEvalTaskWorkflow",
            {**HISTORICAL_BASE, "continue_as_new_after_batches": 1},
            {"batches": [["e1", "e2"], ["e3"], []]},
        ),
        "historical-fail": (
            "HistoricalEvalTaskWorkflow",
            dict(HISTORICAL_BASE),
            {"fail_reconcile": True},
        ),
        "historical-gen-can": (
            "HistoricalEvalTaskWorkflow",
            {**HISTORICAL_BASE, **gen, "continue_as_new_after_batches": 1},
            {"batches": [["e1", "e2"], ["e3"], []]},
        ),
        "historical-gen-fail": (
            "HistoricalEvalTaskWorkflow",
            {**HISTORICAL_BASE, **gen},
            {"fail_reconcile": True},
        ),
        # Continuous: one batch, continue-as-new, an empty poll, then a pause.
        "continuous-can": (
            "ContinuousEvalTaskWorkflow",
            {**CONTINUOUS_BASE, "continue_as_new_after_batches": 1},
            {"batches": [["e1"], []], "inactive_from_state_call": 3},
        ),
        "continuous-fail": (
            "ContinuousEvalTaskWorkflow",
            dict(CONTINUOUS_BASE),
            {"fail_reconcile": True},
        ),
        "continuous-gen-can": (
            "ContinuousEvalTaskWorkflow",
            {**CONTINUOUS_BASE, **gen, "continue_as_new_after_batches": 1},
            {"batches": [["e1"], []], "inactive_from_state_call": 3},
        ),
        "continuous-gen-fail": (
            "ContinuousEvalTaskWorkflow",
            {**CONTINUOUS_BASE, **gen},
            {"fail_reconcile": True},
        ),
    }
    if label == "new":
        # The fenced-out start: the task has moved to another generation.
        out["historical-gen-stale"] = (
            "HistoricalEvalTaskWorkflow",
            {**HISTORICAL_BASE, "workflow_generation": STALE_GENERATION},
            {},
        )
        out["continuous-gen-stale"] = (
            "ContinuousEvalTaskWorkflow",
            {**CONTINUOUS_BASE, "workflow_generation": STALE_GENERATION},
            {},
        )
    return out


class _Stubs:
    def __init__(self, scenarios: dict) -> None:
        self.by_task = {
            f"task-{name}": dict(scenario, state_calls=0)
            for name, (_, _, scenario) in scenarios.items()
        }

    def activities(self) -> list:
        s = self

        @activity.defn(name="get_workflow_labels_activity")
        async def labels(inp: dict) -> dict:
            return {
                "org_id": "org-1",
                "project_id": "project-1",
                "run_type": "historical",
                "task_name": inp["task_id"],
                "project_name": "p",
                "org_name": "o",
                "config_summary": "c",
            }

        @activity.defn(name="set_eval_task_status_activity")
        async def set_status(inp: dict) -> dict:
            return {"task_id": inp["task_id"], "changed": True, "status": "x"}

        @activity.defn(name="reconcile_eval_task_activity")
        async def reconcile(inp: dict) -> dict:
            if s.by_task[inp["task_id"]].get("fail_reconcile"):
                raise ApplicationError(
                    SELECTION_REJECTED,
                    type="EvalTaskSelectionRejected",
                    non_retryable=True,
                )
            return {}

        @activity.defn(name="reap_stale_running_activity")
        async def reap(inp: dict) -> dict:
            return {"requeued": 0, "failed": 0}

        @activity.defn(name="get_eval_task_state_activity")
        async def task_state(inp: dict) -> dict:
            sc = s.by_task[inp["task_id"]]
            sc["state_calls"] += 1
            inactive = inp.get("workflow_generation") == STALE_GENERATION or (
                sc["state_calls"] >= sc.get("inactive_from_state_call", 10**9)
            )
            return {
                "active": not inactive,
                "status": "paused" if inactive else "running",
                "has_undrained_work": False,
            }

        @activity.defn(name="claim_eval_batch_activity")
        async def claim(inp: dict) -> dict:
            batches = s.by_task[inp["task_id"]].get("batches") or []
            return {"entry_ids": batches.pop(0) if batches else []}

        @activity.defn(name="finalize_eval_task_activity")
        async def finalize(inp: dict) -> dict:
            return {"finalized": True}

        @activity.defn(name="run_eval_entry_activity")
        async def run_entry(inp: dict) -> dict:
            return {"status": "completed"}

        @activity.defn(name="requeue_eval_entries_activity")
        async def requeue(inp: dict) -> dict:
            return {}

        return [
            labels,
            set_status,
            reconcile,
            reap,
            task_state,
            claim,
            finalize,
            run_entry,
            requeue,
        ]


async def record_histories(client, label: str) -> dict[str, str]:
    """Run every scenario on this checkout's workflow code and return
    ``{"<label>-<scenario>-run<hop>.json": history JSON}``, one per run of
    each continue-as-new chain. Workflow ids and the task queue are unique per
    call, so a shared test server can host it."""
    from tfc.temporal.eval_tasks.workflows import (
        ContinuousEvalTaskWorkflow,
        HistoricalEvalTaskWorkflow,
    )

    scenarios = _scenarios(label)
    stubs = _Stubs(scenarios)
    queue = f"{TASK_QUEUE}-{uuid.uuid4().hex[:8]}"
    histories: dict[str, str] = {}
    async with Worker(
        client,
        task_queue=queue,
        workflows=[HistoricalEvalTaskWorkflow, ContinuousEvalTaskWorkflow],
        activities=stubs.activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        for name, (wf_type, wf_input, _) in scenarios.items():
            task_id = f"task-{name}"
            handle = await client.start_workflow(
                wf_type,
                {"task_id": task_id, "task_queue": queue, **wf_input},
                id=f"{label}-{name}-{uuid.uuid4().hex[:8]}",
                task_queue=queue,
            )
            try:
                await asyncio.wait_for(handle.result(), timeout=120)
            except WorkflowFailureError:
                pass
            run_id, hop = handle.first_execution_run_id, 0
            while run_id:
                history = await client.get_workflow_handle(
                    handle.id, run_id=run_id
                ).fetch_history()
                histories[f"{label}-{name}-run{hop}.json"] = (
                    json.dumps(json.loads(history.to_json()), indent=2) + "\n"
                )
                run_id = next(
                    (
                        e.workflow_execution_continued_as_new_event_attributes.new_execution_run_id
                        for e in history.events
                        if e.HasField(
                            "workflow_execution_continued_as_new_event_attributes"
                        )
                    ),
                    None,
                )
                hop += 1
    return histories


async def _record(label: str, out_dir: Path) -> None:
    from tfc.temporal.eval_tasks.search_attributes import ALL_KEYS

    env = await WorkflowEnvironment.start_local(search_attributes=ALL_KEYS)
    try:
        for name, text in (await record_histories(env.client, label)).items():
            (out_dir / name).write_text(text)
            print(name)
    finally:
        await env.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", choices=["base", "new"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.test")
    sys.path.insert(0, os.getcwd())
    import django

    django.setup()
    args.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(_record(args.label, args.out))


if __name__ == "__main__":
    main()
