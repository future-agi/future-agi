from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def test_pause_requires_workflow_id():
    with pytest.raises(CommandError, match="workflow-id"):
        call_command("backfill_vapi_minimal", action="pause")


def test_proof_gate_requires_single_shard():
    with pytest.raises(CommandError, match="requires --shards 1"):
        call_command(
            "backfill_vapi_minimal",
            source="observability",
            project_id="11111111-1111-1111-1111-111111111111",
            proof_gate=10,
            shards=2,
        )


def test_pause_sends_signal(mocker):
    signal = mocker.patch(
        "simulate.management.commands.backfill_vapi_minimal.signal_workflow_sync",
        return_value=True,
    )
    call_command("backfill_vapi_minimal", action="pause", workflow_id="wf-1")
    signal.assert_called_once_with("wf-1", "pause")


def test_status_queries_workflow(mocker, capsys):
    query = mocker.patch(
        "simulate.management.commands.backfill_vapi_minimal.query_workflow_sync",
        return_value={"state": "running", "processed": 10},
    )
    call_command("backfill_vapi_minimal", action="status", workflow_id="wf-1")
    query.assert_called_once_with("wf-1", "status")
    assert "processed" in capsys.readouterr().out


def test_reconcile_invokes_sample(mocker, capsys):
    sample = mocker.patch(
        "simulate.management.commands.backfill_vapi_minimal.reconcile_backfill_sample",
        return_value={"call_execution": 3},
    )
    call_command("backfill_vapi_minimal", action="reconcile", source="simulation")
    sample.assert_called_once()
    assert "call_execution" in capsys.readouterr().out


def test_start_fail_closed_no_terminate(mocker):
    from temporalio.common import WorkflowIDReusePolicy

    start = mocker.patch(
        "simulate.management.commands.backfill_vapi_minimal.start_workflow_sync",
        return_value=SimpleNamespace(id="wf"),
    )
    call_command(
        "backfill_vapi_minimal",
        action="start",
        source="simulation",
        run_id="rerun_safe_1",
        shards=1,
        batch_size=1,
        limit=1,
    )
    assert start.call_count == 1
    kwargs = start.call_args.kwargs
    assert kwargs["cancel_existing"] is False
    assert kwargs["task_queue"] == "backfill"
    assert (
        kwargs["id_reuse_policy"]
        is WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
    )


def test_start_multi_shard_cancels_partial_on_failure(mocker):
    calls = {"n": 0}

    def start_side_effect(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("temporal down")
        return SimpleNamespace(id=kwargs["workflow_id"])

    start = mocker.patch(
        "simulate.management.commands.backfill_vapi_minimal.start_workflow_sync",
        side_effect=start_side_effect,
    )
    cancel = mocker.patch(
        "simulate.management.commands.backfill_vapi_minimal.cancel_workflow_sync",
        return_value=True,
    )
    with pytest.raises(CommandError, match="Multi-shard start failed"):
        call_command(
            "backfill_vapi_minimal",
            action="start",
            source="simulation",
            run_id="partial_fail_1",
            shards=3,
            batch_size=1,
            limit=1,
        )
    assert start.call_count == 2
    assert cancel.call_count == 1


def test_reconcile_passes_source_and_project(mocker):
    sample = mocker.patch(
        "simulate.management.commands.backfill_vapi_minimal.reconcile_backfill_sample",
        return_value={"mode": "map_only_all_projects"},
    )
    call_command(
        "backfill_vapi_minimal",
        action="reconcile",
        source="observability",
        project_id="11111111-1111-1111-1111-111111111111",
    )
    inp = sample.call_args.args[0]
    assert inp.source == "observability"
    assert inp.project_id == "11111111-1111-1111-1111-111111111111"
