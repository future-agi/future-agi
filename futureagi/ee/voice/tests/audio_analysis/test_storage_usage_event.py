"""Recording storage is metered once per call, generation and artifact type."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ee.usage.schemas.event_types import BillingEventType
from ee.voice.services.vapi_service import VapiService
from simulate.models.test_execution import CallExecution


@pytest.mark.asyncio
async def test_recording_storage_events_are_generation_scoped(monkeypatch):
    call = SimpleNamespace(
        audio_analysis_generation=0,
        provider_call_data={},
        test_execution=SimpleNamespace(
            agent_definition=None, run_test=SimpleNamespace(organization_id="org")
        ),
    )
    manager = Mock()
    manager.select_related.return_value = manager
    manager.aget = AsyncMock(return_value=call)
    monkeypatch.setattr(CallExecution, "objects", manager)
    service = VapiService.__new__(VapiService)
    service.api_key = "test-key"
    service._extract_recording_urls = Mock(
        return_value={
            role: f"https://source/{role}"
            for role in ("stereo", "assistant", "customer")
        }
    )
    service._normalize_to_fagi_call_data = Mock(
        return_value=SimpleNamespace(
            recording_url="https://source/combined", call_id="provider-call"
        )
    )

    async def upload(call_id, url, url_type, **kwargs):
        assert kwargs["recording_generation"] == call.audio_analysis_generation
        return (
            f"https://recordings.s3.amazonaws.com/call-recordings/{call_id}/"
            f"{call.audio_analysis_generation}/{url_type}.wav",
            1024,
        )

    monkeypatch.setattr(
        "simulate.temporal.utils.async_storage.convert_audio_url_to_s3_async_with_size",
        upload,
    )
    monkeypatch.setattr(
        "ee.voice.services.recording_provenance.recording_artifacts",
        AsyncMock(return_value=[]),
    )
    emit = Mock()
    monkeypatch.setattr("ee.usage.services.emitter.emit", emit)

    async def upload_event_ids():
        emit.reset_mock()
        await service.extract_and_persist_recordings("call")
        events = [invocation.args[0] for invocation in emit.call_args_list]
        assert len(events) == 4
        for event in events:
            assert event.org_id == "org"
            assert event.event_type == BillingEventType.VOICE_RECORDING_STORAGE.value
            assert event.amount == 1024
            assert event.properties["source"] == "simulate"
            assert event.properties["source_id"] == "call"
        return {event.properties["artifact_type"]: event.event_id for event in events}

    original = await upload_event_ids()
    assert set(original) == {
        "recording",
        "stereo_recording",
        "assistant_recording",
        "customer_recording",
    }
    assert len(set(original.values())) == 4
    assert await upload_event_ids() == original  # Retry of this generation.
    call.audio_analysis_generation += 1
    rerun = await upload_event_ids()
    assert set(rerun) == set(original)
    assert set(rerun.values()).isdisjoint(original.values())
    assert await upload_event_ids() == rerun  # Retry of the rerun generation.
