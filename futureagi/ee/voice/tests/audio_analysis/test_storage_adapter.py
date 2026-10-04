"""R12/R13, AC12/AC13: deployed storage versions and scoped rehosting."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from minio.error import S3Error

from ee.voice.services.audio_analysis.decode import fetch_audio
from ee.voice.services.audio_analysis.errors import AudioDeterministicError
from ee.voice.services.audio_storage import AudioStorage


def test_conditional_fetch_closes_and_releases_storage_response():
    client = Mock()
    client.stat_object.return_value = SimpleNamespace(
        size=3, etag="etag", version_id=None
    )
    response = Mock(headers={"ETag": '"etag"'})
    response.read.side_effect = [b"wav", b""]
    client.get_object.return_value = response
    result = fetch_audio(
        AudioStorage(client),
        bucket="recordings",
        key="call-recordings/call/agent.wav",
        call_id="call",
        expected_version='"etag"',
    )
    assert result == b"wav"
    client.get_object.assert_called_once_with(
        "recordings",
        "call-recordings/call/agent.wav",
        version_id=None,
        request_headers={"If-Match": '"etag"'},
    )
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


def test_deployment_access_denied_is_sanitized():
    client = Mock()
    client.stat_object.side_effect = S3Error(
        SimpleNamespace(status=403),
        "AccessDenied",
        "secret-detail",
        "secret-key",
        "id",
        "host",
    )
    with pytest.raises(
        AudioDeterministicError, match="provider_access_denied"
    ) as error:
        fetch_audio(
            AudioStorage(client),
            bucket="recordings",
            key="call-recordings/call/agent.wav",
            call_id="call",
        )
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_rehost_generation_never_reuses_old_audio(monkeypatch):
    from simulate.temporal.utils import async_storage
    from tracer.utils.vapi_recording import VapiRecordingService

    monkeypatch.setattr(async_storage, "_is_fagi_storage_url", lambda _: False)
    monkeypatch.setattr(
        VapiRecordingService, "is_authenticated_download", lambda *a: True
    )
    looked_up = []

    def existing(key):
        looked_up.append(key)
        return "https://recordings.s3.amazonaws.com/" + key + ".wav", 3

    monkeypatch.setattr(async_storage, "_existing_rehosted_audio", existing)
    for generation in (0, 1):
        await async_storage.convert_audio_url_to_s3_async_with_size(
            "call",
            "https://source",
            "customer_recording",
            call_scoped=True,
            recording_generation=generation,
        )
    assert looked_up == [
        "call-recordings/call/0/customer_recording",
        "call-recordings/call/1/customer_recording",
    ]


def test_deployment_transport_never_follows_redirect(monkeypatch):
    from ee.voice.services.audio_storage import get_audio_storage
    from tfc.utils import storage_client

    transport = Mock()
    client = SimpleNamespace(_http=transport)
    monkeypatch.setattr(storage_client, "get_storage_client", lambda: client)
    adapted = get_audio_storage().client
    adapted._http.urlopen(
        "GET",
        "https://configured-storage/recordings/key",
        headers={"Authorization": "test"},
    )
    assert transport.urlopen.call_args.kwargs["redirect"] is False
    assert client._http is transport
    adapted._http.clear()
    transport.clear.assert_not_called()
