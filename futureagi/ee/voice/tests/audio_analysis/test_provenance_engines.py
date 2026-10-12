"""R01-R04, AC01/AC02: engine-emitted manifests use recording ownership."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ee.voice.services.livekit.service import LivekitService
from ee.voice.services.retell_service import RetellService
from ee.voice.services.vapi_service import VapiService
from simulate.models.test_execution import CallExecution


@pytest.fixture
def recording_seam(monkeypatch, settings):
    settings.UPLOAD_BUCKET_NAME = "recordings"
    call = SimpleNamespace(
        id="call",
        audio_analysis_generation=0,
        provider_call_data={},
        test_execution=SimpleNamespace(
            agent_definition=None, run_test=SimpleNamespace(organization_id="org")
        ),
        asave=AsyncMock(),
    )
    manager = Mock()
    manager.select_related.return_value = manager
    manager.aget = AsyncMock(return_value=call)
    monkeypatch.setattr(CallExecution, "objects", manager)
    from simulate.temporal.utils import async_storage

    async def convert(call_id, url, url_type, **kwargs):
        assert (
            kwargs.get("call_scoped", False)
            if kwargs.get("provider") == "vapi"
            else True
        )
        return (
            f"https://recordings.s3.amazonaws.com/call-recordings/{call_id}/{url_type}.wav",
            0,
        )

    monkeypatch.setattr(
        async_storage, "convert_audio_url_to_s3_async_with_size", convert
    )
    from ee.voice.services import recording_provenance

    monkeypatch.setattr(
        recording_provenance,
        "get_audio_storage",
        lambda: SimpleNamespace(
            head_object=lambda **kw: {
                "ETag": '"etag"',
                "VersionId": None,
                "ContentLength": 100,
            }
        ),
    )
    return call


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "direction,owner,roles,bridge,expected",
    [
        ("inbound", "system", ["assistant", "customer"], False, "customer"),
        ("outbound", "client", ["assistant", "customer"], False, "assistant"),
        ("outbound", "system", ["assistant", "customer"], True, None),
        ("inbound", "system", ["combined"], False, None),
        ("inbound", "system", ["stereo"], False, None),
    ],
)
async def test_vapi_emits_owned_role(
    recording_seam, direction, owner, roles, bridge, expected
):
    service = VapiService.__new__(VapiService)
    service.api_key = "test-key"
    service._extract_recording_urls = Mock(
        return_value={role: f"https://source/{role}" for role in roles}
    )
    service._normalize_to_fagi_call_data = Mock(
        return_value=SimpleNamespace(
            recording_url="https://source/combined" if "combined" in roles else None,
            call_id="provider-call",
        )
    )
    result = await service.extract_and_persist_recordings(
        "call",
        recording_context={
            "direction": direction,
            "recording_owner_account": owner,
            "is_web_bridge": bridge,
            "tested_agent_platform": "vapi",
            "transport": "sip",
        },
    )
    prov = result.provenance
    if expected:
        assert prov.manifest_version == "vapi_role_v1"
        artifact = prov.artifacts[prov.target.artifact_index]
        assert artifact.provider_role == expected
        assert artifact.object_version_or_etag == '"etag"'
        assert artifact.object_key.startswith("call-recordings/call/")
        assert f"owner={owner}" in prov.target.evidence
    else:
        assert prov.manifest_version == "unsupported_v1"
        assert prov.target is None
        assert prov.unsupported_reason == "unknown_agent_track"


@pytest.mark.asyncio
async def test_retell_emits_unsupported(recording_seam):
    recording_seam.provider_call_data = {
        "retell": {"recording_url": "https://source/combined"}
    }
    result = await RetellService(api_key="test").extract_and_persist_recordings("call")
    assert result.provenance.manifest_version == "unsupported_v1"
    assert result.provenance.unsupported_reason == "unknown_agent_track"
    assert result.provenance.capture_origin == "provider_recording"
    assert result.provenance.target is None
    assert len(result.provenance.artifacts) == 1


@pytest.mark.asyncio
async def test_livekit_without_egress_emits_unsupported(recording_seam):
    service = LivekitService.__new__(LivekitService)
    result = await service.extract_and_persist_recordings("call")
    assert result.provenance.manifest_version == "unsupported_v1"
    assert result.provenance.unsupported_reason == "unknown_agent_track"
    assert result.provenance.capture_origin == "livekit_egress"
    assert result.provenance.target is None
