"""Bulk snapshots and eval-only writes preserve asynchronous audio results."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from ee.voice.services.audio_analysis.envelope import empty_envelope
from simulate.models import AgentDefinition, Scenarios
from simulate.models.run_test import CreateCallExecution, RunTest
from simulate.models.test_execution import CallExecution
from simulate.models.test_execution import TestExecution as Execution
from simulate.views import run_test


@pytest.fixture
def rerun_call(db, organization, workspace, monkeypatch):
    monkeypatch.setenv("SYSTEM_VOICE_PROVIDER", "livekit")
    agent = AgentDefinition.objects.create(
        agent_name="Rerun audio target",
        agent_type="voice",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    scenario = Scenarios.objects.create(
        name="Rerun audio scenario",
        source="synthetic",
        organization=organization,
        workspace=workspace,
    )
    run = RunTest.objects.create(
        name="Rerun audio run",
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
    )
    execution = Execution.objects.create(
        run_test=run, agent_definition=agent, status="completed"
    )
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status="completed",
        phone_number="+15555550100",
        audio_analysis_generation=4,
        audio_metrics=empty_envelope(4),
        audio_provenance={"recording": "original"},
        eval_outputs={"evaluation": {"score": 1}},
        call_metadata={"eval_started": True, "eval_completed": True, "keep": True},
    )
    call.audio_metrics.update(state="pending", analysis_id="analysis-4")
    call.audio_metrics["metrics"]["average_pitch_hz"].update(
        state="available", value=220.0, reason=None
    )
    call.save(update_fields=["audio_metrics"])
    return call


@pytest.mark.parametrize("rerun_type", ["call_and_eval", "eval_only"])
def test_bulk_rerun_snapshots_audio_before_reset(rerun_call, rerun_type):
    before = deepcopy(rerun_call.audio_metrics)
    provenance = deepcopy(rerun_call.audio_provenance)
    calls = CallExecution.objects.filter(pk=rerun_call.pk)

    successful, failed = (
        run_test.TestExecutionRerunView()._prepare_call_executions_for_rerun_bulk(
            calls, rerun_type
        )
    )

    assert successful == [str(rerun_call.pk)]
    assert failed == []
    rerun_call.refresh_from_db()
    snapshot = rerun_call.snapshots.get()
    assert snapshot.rerun_type == rerun_type
    assert snapshot.audio_metrics == before
    assert rerun_call.eval_outputs == {}
    if rerun_type == "call_and_eval":
        assert rerun_call.audio_metrics is None
        assert rerun_call.audio_provenance is None
        assert rerun_call.audio_analysis_generation == 5
        assert CreateCallExecution.objects.filter(call_execution=rerun_call).exists()
    else:
        assert rerun_call.audio_metrics == before
        assert rerun_call.audio_provenance == provenance
        assert rerun_call.audio_analysis_generation == 4


@pytest.mark.parametrize("path", ["helper", "api"])
def test_eval_only_reset_preserves_interleaved_audio_commit(
    rerun_call, path, user, workspace, monkeypatch
):
    """Commit audio after the rerun loads its row but before its eval-only save."""
    before = deepcopy(rerun_call.audio_metrics)
    committed = deepcopy(before)
    committed.update(state="partial", computed_at="2026-11-06T00:00:00Z")
    committed["metrics"]["average_pitch_hz"].update(value=240.0)
    provenance = {"recording": "committed", "etag": "immutable-etag"}
    old_timestamp = timezone.now() - timedelta(days=1)
    CallExecution.objects.filter(pk=rerun_call.pk).update(updated_at=old_timestamp)
    rerun_call.refresh_from_db()
    interleavings = []
    original_snapshot = run_test._save_eval_snapshot

    def commit_after_snapshot(call):
        assert call.audio_metrics == before
        original_snapshot(call)
        CallExecution.objects.filter(pk=call.pk).update(
            audio_metrics=committed, audio_provenance=provenance
        )
        assert call.audio_metrics == before  # The rerun's loaded instance is stale.
        interleavings.append(call.pk)

    if path == "helper":
        monkeypatch.setattr(run_test, "_save_eval_snapshot", commit_after_snapshot)
        dispatch = Mock()
        monkeypatch.setattr(
            run_test._run_simulate_evaluations_task, "apply_async", dispatch
        )
        successful, failed, pending_calls, pending_evals = (
            run_test._rerun_call_executions([rerun_call], "eval_only")
        )
        assert successful == [str(rerun_call.pk)]
        assert failed == []
        assert not pending_calls and pending_evals
        dispatch.assert_called_once_with(args=(str(rerun_call.pk),))
    else:
        monkeypatch.setattr(
            run_test.CallExecutionRerunView,
            "_save_eval_snapshot",
            lambda self, call: commit_after_snapshot(call),
        )
        monkeypatch.setattr(run_test, "_hosted_execution_eligible", lambda *a: False)
        dispatch = Mock(return_value={"merged": False, "workflow_id": "eval-rerun"})
        monkeypatch.setattr("simulate.temporal.client.rerun_call_executions", dispatch)
        request = APIRequestFactory().post(
            "/",
            {"rerun_type": "eval_only", "call_execution_ids": [str(rerun_call.pk)]},
            format="json",
        )
        request.organization = user.organization
        request.workspace = workspace
        force_authenticate(request, user=user)
        response = run_test.CallExecutionRerunView.as_view()(
            request, test_execution_id=rerun_call.test_execution_id
        )
        assert response.status_code == 200, response.data
        assert response.data["success_count"] == 1, response.data
        assert response.data["dispatch_error"] is None
        dispatch.assert_called_once()

    assert interleavings == [rerun_call.pk]
    rerun_call.refresh_from_db()
    assert rerun_call.audio_metrics == committed
    assert rerun_call.audio_provenance == provenance
    assert rerun_call.audio_analysis_generation == 4
    assert rerun_call.snapshots.get().audio_metrics == before
    assert rerun_call.eval_outputs == {}
    assert rerun_call.call_metadata["eval_started"] is False
    assert rerun_call.call_metadata["eval_completed"] is False
    assert rerun_call.call_metadata["keep"] is True
    assert rerun_call.updated_at > old_timestamp
