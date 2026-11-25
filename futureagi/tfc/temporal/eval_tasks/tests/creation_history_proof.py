"""Explicit two-checkout qualification; never record a candidate run as old.

Copy ONLY this manual driver to the base. Keep the raw outputs outside the
checkout. The collected replay tests use sanitized derivatives with the raw
hash and exact identity replacement recorded in each fixture's provenance.
"""

import hashlib
import json
import os
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from tfc.temporal.eval_tasks import get_activities, workflows
from tfc.temporal.eval_tasks.types import EvalTaskWorkflowInput

BASE = "ae0bf483a052580a2e57da454d51b5a43b90fbc2"


@pytest.mark.django_db(transaction=True)
async def test_capture(workflow_environment, eval_task):
    source = Path(workflows.__file__).resolve()
    checkout = source.parents[4]
    assert (
        subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        == BASE
    )
    subprocess.run(
        ["git", "-C", str(checkout), "diff", "--exit-code", "--quiet", BASE], check=True
    )
    allowed = Path(__file__).resolve().relative_to(checkout).as_posix()
    assert subprocess.check_output(
        ["git", "-C", str(checkout), "ls-files", "--others", "--exclude-standard"],
        text=True,
    ).splitlines() == [allowed]
    assert source == checkout / "futureagi/tfc/temporal/eval_tasks/workflows.py"
    from tfc.temporal.eval_tasks import activities

    assert Path(activities.__file__).resolve() == source.with_name("activities.py")
    out = Path(os.environ["HISTORICAL_REPLAY_HISTORY_DIR"])
    q = f"creation-base-{uuid.uuid4()}"
    workflow_id = f"eval-task-{eval_task.pk}"
    # A genuinely old, sampled historical task, with an empty closed window.
    now = timezone.now()
    eval_task.sampling_policy = {
        "version": 1,
        "sampling_unit": "session",
        "mode": "closed",
        "window": {
            "start": (now - timedelta(hours=2)).isoformat(),
            "end": (now - timedelta(hours=1)).isoformat(),
        },
    }
    await sync_to_async(eval_task.save)(update_fields=["sampling_policy"])
    # Real base workflow + registered base activities, not a mock patch toggle.
    async with Worker(
        workflow_environment.client,
        task_queue=q,
        identity=f"base-{BASE}",
        workflows=[workflows.HistoricalEvalTaskWorkflow],
        activities=get_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await workflow_environment.client.start_workflow(
            workflows.HistoricalEvalTaskWorkflow.run,
            EvalTaskWorkflowInput(str(eval_task.pk), task_queue=q),
            id=workflow_id,
            task_queue=q,
        )
        assert (await handle.result()).status == "completed"
        history = await handle.fetch_history()
    data = history.to_json().encode()
    with (out / "base-history.json").open("xb") as file:
        file.write(data)
    with (out / "base-provenance.json").open("x") as file:
        json.dump(
            {
                "base": BASE,
                "sha256": hashlib.sha256(data).hexdigest(),
                "workflow_id": workflow_id,
                "run_id": handle.first_execution_run_id,
            },
            file,
        )


async def test_unpatched_negative_control():
    """Run ONLY in a throwaway candidate checkout with the real patch call removed."""
    from temporalio.workflow import NondeterminismError

    from tfc.temporal.eval_tasks.tests.test_historical_creation_replay import (
        base_workflow_history,
        replay,
        without_timer_patch,
    )

    source = Path(workflows.__file__).resolve()
    root = source.parents[4]
    tree = os.environ["HISTORICAL_REPLAY_CANDIDATE_TREE"]
    original = subprocess.check_output(
        ["git", "-C", str(root), "show", f"{tree}:{source.relative_to(root)}"],
        text=True,
    )
    assert source.read_text() == without_timer_patch(original)
    # The negative uses the exact same recorded bytes as the positive replay.
    directory = Path(os.environ["HISTORICAL_REPLAY_HISTORY_DIR"])
    await replay(base_workflow_history("base", directory))
    mixed = base_workflow_history("mixed", directory)
    with pytest.raises(NondeterminismError) as mismatch:
        await replay(mixed)
    print(f"mixed replay without the timer patch gate: {mismatch.value}")
