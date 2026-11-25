"""Replay two server histories with different status replies. The checked-in fixtures
redact only the initiating client's identity. Provenance hashes the replayed bytes
(``sha256``) and records the raw digest and replacement for each sanitized derivative.
"""

import hashlib
import importlib.util
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner
from temporalio.workflow import NondeterminismError

from tfc.temporal.eval_tasks import workflows

BASE = "ae0bf483a052580a2e57da454d51b5a43b90fbc2"
FIXTURES = Path(__file__).parent / "fixtures" / "historical-creation-base"
STATUS = "set_eval_task_status_activity"
RECONCILE = "reconcile_eval_task_activity"
REAP = "reap_stale_running_activity"


def events(history, kind):
    return [
        event for event in history.events if event.HasField(f"{kind}_event_attributes")
    ]


def exchange(history, name, identity):
    """Join by event IDs; don't mistake the preceding activity's reply for this one."""
    (scheduled,) = [
        e
        for e in events(history, "activity_task_scheduled")
        if e.activity_task_scheduled_event_attributes.activity_type.name == name
    ]
    (started,) = [
        e
        for e in events(history, "activity_task_started")
        if e.activity_task_started_event_attributes.scheduled_event_id
        == scheduled.event_id
    ]
    (completed,) = [
        e
        for e in events(history, "activity_task_completed")
        if e.activity_task_completed_event_attributes.started_event_id
        == started.event_id
    ]
    assert started.activity_task_started_event_attributes.identity == identity
    assert started.activity_task_started_event_attributes.attempt == 1
    assert (
        completed.activity_task_completed_event_attributes.scheduled_event_id
        == scheduled.event_id
    )
    (args,) = scheduled.activity_task_scheduled_event_attributes.input.payloads
    (result,) = completed.activity_task_completed_event_attributes.result.payloads
    return scheduled, json.loads(args.data), json.loads(result.data)


def base_workflow_history(label, directory=FIXTURES):
    path = directory / f"{label}-history.json"
    assert path.is_file(), f"Missing {label} replay history: {path}"
    provenance = json.loads((directory / f"{label}-provenance.json").read_text())
    data = path.read_bytes()
    assert provenance["base"] == BASE
    assert hashlib.sha256(data).hexdigest() == provenance["sha256"]
    history = WorkflowHistory.from_json(provenance["workflow_id"], data.decode())
    started = history.events[0].workflow_execution_started_event_attributes
    assert started.workflow_type.name == "HistoricalEvalTaskWorkflow"
    assert history.events[-1].HasField("workflow_execution_completed_event_attributes")
    assert started.original_execution_run_id == provenance["run_id"]
    assert {
        e.workflow_task_completed_event_attributes.identity
        for e in events(history, "workflow_task_completed")
    } == {f"base-{BASE}"}
    assert not events(history, "workflow_task_failed")
    assert not events(history, "timer_started")
    assert not any(
        b"historical-first-closed-reconcile-delay-v1" in payload.data
        for e in events(history, "marker_recorded")
        for detail in e.marker_recorded_event_attributes.details.values()
        for payload in detail.payloads
    )
    names = [
        e.activity_task_scheduled_event_attributes.activity_type.name
        for e in events(history, "activity_task_scheduled")
    ]
    assert names == [
        "get_workflow_labels_activity",
        STATUS,
        RECONCILE,
        REAP,
        "get_eval_task_state_activity",
        "claim_eval_batch_activity",
        "finalize_eval_task_activity",
    ]
    identity = f"base-{BASE}"
    if label == "mixed":
        assert re.fullmatch("[0-9a-f]{40}", provenance["candidate_tree"])
        identity = f"new-{provenance['candidate_tree']}"
    _, _, ack = exchange(history, STATUS, identity)
    scheduled, args, _ = exchange(history, RECONCILE, identity)
    assert args == {"task_id": ack["task_id"]}
    assert "require_running" not in exchange(history, REAP, identity)[1]
    assert ack["changed"] and ack["status"] == "running"
    if label == "mixed":
        deadline = datetime.fromisoformat(ack["first_reconcile_at"])
        assert deadline == datetime.fromisoformat(provenance["created_at"]) + timedelta(
            seconds=120
        )
        # Replaying an unguarded candidate MUST emit StartTimer here: an old
        # workflow that processed the reply after its deadline isn't a witness.
        assert scheduled.event_time.ToDatetime(tzinfo=UTC) < deadline
    else:
        assert "first_reconcile_at" not in ack
    return history


async def replay(history, workflow_class=workflows.HistoricalEvalTaskWorkflow):
    await Replayer(
        workflows=[workflow_class],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.parametrize("label", ["base", "mixed"])
async def test_replay_base_historical_workflow(label):
    await replay(base_workflow_history(label))


def without_timer_patch(source):
    call = (
        "deadline is not None and workflow.patched(\n"
        '                "historical-first-closed-reconcile-delay-v1"\n            )'
    )
    assert source.count(call) == 1
    return source.replace(call, "deadline is not None")


async def test_replay_without_timer_patch(tmp_path, monkeypatch):
    # Load a separate copy of the actual candidate without the gate; do not
    # mock Temporal's patch API or change the workflow used in positive replays.
    mutant = tmp_path / "unguarded_workflows.py"
    mutant.write_text(without_timer_patch(Path(workflows.__file__).read_text()))
    spec = importlib.util.spec_from_file_location(mutant.stem, mutant)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    await replay(base_workflow_history("base"), module.HistoricalEvalTaskWorkflow)
    mixed = base_workflow_history("mixed")
    with pytest.raises(
        NondeterminismError, match=r"Timer machine.*ActivityTaskScheduled"
    ):
        await replay(mixed, module.HistoricalEvalTaskWorkflow)
