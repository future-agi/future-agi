"""Project-scoped rehosting and bounded, optional capture-time pinning."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ee.agenthub.insights.evidence import own_storage_in_scope
from ee.voice.services import recording_provenance
from ee.voice.services.audio_analysis.errors import AudioProvenanceError
from ee.voice.services.audio_provenance import validate_manifest, validate_object_key
from ee.voice.services.types.voice import RecordingUrls
from ee.voice.services.vapi_service import VapiService
from simulate.models.test_execution import CallExecution
from simulate.temporal.utils import async_storage
from tracer.utils.vapi_recording import VapiRecordingService


@pytest.fixture
def recording_case(monkeypatch, settings):
    settings.UPLOAD_BUCKET_NAME = "recordings"
    settings.VOICE_AUDIO_METRICS_ENABLED = True
    call = SimpleNamespace(
        audio_analysis_generation=0,
        provider_call_data={},
        test_execution=SimpleNamespace(
            agent_definition=SimpleNamespace(
                observability_provider=SimpleNamespace(project_id="project-a")
            ),
            run_test=SimpleNamespace(organization_id="org"),
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
    monkeypatch.setattr(async_storage, "_is_fagi_storage_url", lambda _: False)
    monkeypatch.setattr(
        VapiRecordingService, "is_authenticated_download", lambda *a: True
    )
    lookup = Mock(
        side_effect=lambda key: (f"https://recordings.s3.amazonaws.com/{key}.wav", 0)
    )
    monkeypatch.setattr(async_storage, "_existing_rehosted_audio", lookup)
    storage = Mock()
    storage.head_object.return_value = {"ETag": '"etag"'}
    monkeypatch.setattr(recording_provenance, "get_audio_storage", lambda: storage)
    return call, service, lookup, storage


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [0, 1, 2])
async def test_vapi_generation_retains_project_scope(recording_case, generation):
    call, service, lookup, _ = recording_case
    call.audio_analysis_generation = generation
    result = await service.extract_and_persist_recordings(
        "call",
        recording_context={"direction": "inbound", "recording_owner_account": "system"},
    )
    kinds = (
        "recording",
        "stereo_recording",
        "assistant_recording",
        "customer_recording",
    )
    expected_bases = [
        "call-recordings/project-a/vapi/call/"
        f"{'g' + str(generation) + '-' if generation else ''}{kind}"
        for kind in kinds
    ]
    assert [
        invocation.args[0] for invocation in lookup.call_args_list
    ] == expected_bases
    assert [a.object_key for a in result.provenance.artifacts] == [
        f"{base}.wav" for base in expected_bases
    ]
    for artifact in result.provenance.artifacts:
        assert own_storage_in_scope(artifact.object_key, "project-a")
        assert not own_storage_in_scope(artifact.object_key, "project-b")
        validate_object_key(artifact.object_key, "call")
        with pytest.raises(AudioProvenanceError, match="analysis_error"):
            validate_object_key(artifact.object_key, "other-call")
    assert (
        validate_manifest(result.provenance, "call", "recordings").provider_role
        == "customer"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_vapi_pinning_respects_feature_flag(
    recording_case, settings, monkeypatch, enabled
):
    _, service, _, storage = recording_case
    settings.VOICE_AUDIO_METRICS_ENABLED = enabled
    capture = AsyncMock(wraps=recording_provenance.recording_artifacts)
    monkeypatch.setattr(recording_provenance, "recording_artifacts", capture)
    result = await service.extract_and_persist_recordings("call")
    assert capture.await_args.kwargs["pin_versions"] is enabled
    assert len(result.provenance.artifacts) == 4
    assert storage.head_object.call_count == (4 if enabled else 0)
    assert all(
        artifact.object_version_or_etag == ('"etag"' if enabled else None)
        for artifact in result.provenance.artifacts
    )
    assert result.provider_call_data["vapi"]["recording"]


def four_recordings():
    def url(kind):
        return f"https://recordings.s3.amazonaws.com/call-recordings/project/vapi/call/{kind}.wav"

    return RecordingUrls(
        recording_url=url("recording"),
        stereo_recording_url=url("stereo_recording"),
        assistant_recording_url=url("assistant_recording"),
        customer_recording_url=url("customer_recording"),
    )


@pytest.mark.asyncio
async def test_capture_flag_off_never_heads(recording_case, settings):
    *_, storage = recording_case
    settings.VOICE_AUDIO_METRICS_ENABLED = False
    artifacts = await recording_provenance.recording_artifacts(
        four_recordings(), pin_versions=True
    )
    assert len(artifacts) == 4
    assert all(a.object_version_or_etag is None for a in artifacts)
    storage.head_object.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attempt_timeout,total_budget,attempts", [(1.0, 0.2, 1), (0.05, 0.8, 2)]
)
async def test_four_slow_heads_are_bounded_and_cannot_pin_late(
    recording_case, monkeypatch, attempt_timeout, total_budget, attempts
):
    *_, storage = recording_case
    # Test scaled budgets while exercising real blocking thread work and timers.
    monkeypatch.setattr(
        recording_provenance, "PIN_TOTAL_TIMEOUT_SECONDS", total_budget, raising=False
    )
    monkeypatch.setattr(
        recording_provenance,
        "PIN_ATTEMPT_TIMEOUT_SECONDS",
        attempt_timeout,
        raising=False,
    )

    def slow_head(**kwargs):
        time.sleep(1.0)
        return {"ETag": '"too-late"'}

    storage.head_object.side_effect = slow_head
    started = time.monotonic()
    artifacts = await recording_provenance.recording_artifacts(
        four_recordings(), pin_versions=True
    )
    elapsed = time.monotonic() - started
    assert elapsed < total_budget + 0.2
    assert storage.head_object.call_count == 4 * attempts
    assert len(artifacts) == 4
    assert all(a.object_version_or_etag is None for a in artifacts)
    await asyncio.sleep(1.1)
    assert all(a.object_version_or_etag is None for a in artifacts)
