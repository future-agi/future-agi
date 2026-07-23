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
