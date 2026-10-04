"""R01/R12/R13, AC01/AC12: atomic recording save and deletion hygiene."""

import copy
from unittest.mock import AsyncMock, Mock

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from ee.voice.services.audio_analysis.envelope import empty_envelope
from simulate.tests.test_call_execution_audio_metrics_api import (
    audio_call as audio_call,
)
from simulate.views.run_test import CallExecutionDeleteView


def test_delete_succeeds_when_temporal_has_no_workflow(audio_call, auth_client):
    """A pending analysis must not block deletion when its workflow is gone.

    The test database has no Temporal server, so termination raises
    WorkflowExecutionNotFoundError (a DoesNotExist subclass). Deletion must
    still return 204 and the tombstone must be written. This is the contract
    the delete view's try/except exists for.
    """
    from simulate.models.test_execution import CallExecution

    env = empty_envelope()
    env.update(state="pending", analysis_id="analysis")
    audio_call.audio_metrics = env
    audio_call.save(update_fields=["audio_metrics"])

    response = auth_client.delete(
        f"/simulate/call-executions/{audio_call.pk}/delete/"
    )
    assert response.status_code == 204, response.content
    deleted = CallExecution.all_objects.get(pk=audio_call.pk)
    assert deleted.deleted is True
    assert deleted.deleted_at is not None


def test_finalizer_idempotent_and_retains_terminal_vqi(audio_call):
    from asgiref.sync import async_to_sync

    from ee.voice.temporal.activities.audio_analysis import (
        FinalizeFailureInput,
        finalize_audio_analysis_failure,
    )

    env = empty_envelope()
    env.update(state="pending", analysis_id="analysis")
    for entry in env["metrics"].values():
        entry.update(state="pending", reason=None)
    env["metrics"]["voice_quality_index"].update(
        state="unavailable", reason="model_unavailable"
    )
    audio_call.audio_metrics = env
    audio_call.save(update_fields=["audio_metrics"])
    input = FinalizeFailureInput(str(audio_call.id), "analysis", 0, "timeout")
    async_to_sync(finalize_audio_analysis_failure)(input)
    audio_call.refresh_from_db()
    terminal = copy.deepcopy(audio_call.audio_metrics)
    assert terminal["state"] == "failed"
    assert terminal["metrics"]["voice_quality_index"]["reason"] == "model_unavailable"
    async_to_sync(finalize_audio_analysis_failure)(input)
    audio_call.refresh_from_db()
    assert audio_call.audio_metrics == terminal


@pytest.mark.asyncio
async def test_fetch_and_persist_saves_provenance_same_write(monkeypatch):
    from dataclasses import asdict
    from types import SimpleNamespace

    from temporalio.testing import ActivityEnvironment

    from ee.voice.services.audio_provenance import unsupported_provenance
    from ee.voice.services.types.voice import CostBreakdown, RecordingUrls
    from ee.voice.services.voice_service_manager import VoiceServiceManager
    from ee.voice.temporal.activities.voice_large import fetch_and_persist_call_result
    from simulate.models.run_test import CreateCallExecution
    from simulate.models.test_execution import CallExecution
    from simulate.temporal.types.activities import FetchAndPersistCallResultInput

    provenance = unsupported_provenance("unknown_agent_track")
    manager = Mock()
    manager.fetch_and_store_call_data = AsyncMock(return_value=(4, True, True))
    manager.extract_and_persist_recordings = AsyncMock(
        return_value=RecordingUrls(
            recording_url="https://recordings.s3.amazonaws.com/call-recordings/call/combined.wav",
            provenance=provenance,
            provider_call_data={"vapi": {"recording": {}}},
        )
    )
    manager.extract_costs = AsyncMock(return_value=CostBreakdown(total=None))
    monkeypatch.setattr(VoiceServiceManager, "__new__", lambda cls, *a, **kw: manager)
    call = SimpleNamespace(ended_reason=None, test_execution=None, asave=AsyncMock())
    query = Mock()
    query.select_related.return_value = query
    query.aget = AsyncMock(return_value=call)
    monkeypatch.setattr(CallExecution, "objects", query)
    create_query = Mock()
    create_query.filter.return_value.aupdate = AsyncMock()
    monkeypatch.setattr(CreateCallExecution, "objects", create_query)
    result = await ActivityEnvironment().run(
        fetch_and_persist_call_result,
        FetchAndPersistCallResultInput(
            "call", "completed", "provider-call", "vapi", "inbound"
        ),
    )
    assert result.success
    call.asave.assert_awaited_once()
    assert {"recording_url", "provider_call_data", "audio_provenance"} <= set(
        call.asave.call_args.kwargs["update_fields"]
    )
    assert call.audio_provenance == asdict(provenance)
    assert (
        manager.extract_and_persist_recordings.call_args.kwargs["recording_context"][
            "recording_owner_account"
        ]
        == "system"
    )
