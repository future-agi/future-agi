"""Recording rehost logs which recording, which tool and the outcome.

With ffmpeg missing from PATH, rehosting keeps the provider URL; the log says
which call and artifact, that ffmpeg is missing, and that the original URL is
kept, without the (possibly signed) URL itself."""

import io
import wave
from unittest.mock import Mock, patch

import pytest
from structlog.testing import capture_logs

from simulate.temporal.utils.async_storage import (
    convert_audio_url_to_s3_async_with_size,
    convert_audio_url_to_s3_sync,
)

pytestmark = pytest.mark.unit

SIGNED_URL = "https://provider.example/rec.wav?X-Signature=secret-token"


def _wav() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 800)
    return out.getvalue()


@pytest.fixture
def no_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))


@pytest.fixture
def storage():
    """No earlier rehosted object; uploads are recorded."""
    client = Mock()
    client.stat_object.side_effect = Exception("not found")
    with (
        patch(
            "simulate.temporal.utils.async_storage._is_fagi_storage_url",
            return_value=False,
        ),
        patch("tfc.utils.storage_client.get_storage_client", return_value=client),
        patch(
            "tfc.utils.storage.upload_audio_to_s3",
            side_effect=lambda _data, *, object_key: f"https://fagi/{object_key}",
        ) as upload,
    ):
        yield upload


def _response(audio_bytes: bytes) -> Mock:
    response = Mock()
    response.iter_content.return_value = [audio_bytes]
    return response


def _failed_event(logs):
    (event,) = [e for e in logs if e["event"] == "recording_rehost_failed"]
    return event


def test_sync_rehost_without_ffmpeg_logs_the_tool_and_the_kept_url(no_ffmpeg, storage):
    with (
        patch(
            "simulate.temporal.utils.async_storage.requests.get",
            return_value=_response(_wav()),
        ),
        capture_logs() as logs,
    ):
        url, size = convert_audio_url_to_s3_sync(
            "call-123", SIGNED_URL, "mono_combined", provider="retell"
        )

    assert (url, size) == (SIGNED_URL, 0)
    storage.assert_not_called()
    event = _failed_event(logs)
    assert event["call_id"] == "call-123"
    assert event["artifact_type"] == "mono_combined"
    assert event["provider"] == "retell"
    assert event["code"] == "ffmpeg_unavailable"
    assert event["missing_tool"] == "ffmpeg"
    assert event["outcome"] == "kept_original_url"
    assert event["error"].startswith("ffmpeg is not installed in this backend image")
    assert "secret-token" not in str(logs)


async def test_async_rehost_without_ffmpeg_logs_the_tool_and_the_kept_url(
    no_ffmpeg, storage
):
    with (
        patch("tfc.utils.ssrf_guard.assert_url_host_public"),
        patch(
            "simulate.temporal.utils.async_storage.download_audio_from_url_async",
            return_value=_wav(),
        ),
        capture_logs() as logs,
    ):
        url, size = await convert_audio_url_to_s3_async_with_size(
            "call-456", SIGNED_URL, "stereo", provider="bland"
        )

    assert (url, size) == (SIGNED_URL, 0)
    event = _failed_event(logs)
    assert (event["call_id"], event["artifact_type"], event["provider"]) == (
        "call-456",
        "stereo",
        "bland",
    )
    assert event["code"] == "ffmpeg_unavailable"
    assert event["outcome"] == "kept_original_url"
    assert "secret-token" not in str(logs)


def test_other_rehost_failure_logs_no_error_text(storage):
    with (
        patch(
            "simulate.temporal.utils.async_storage.requests.get",
            side_effect=RuntimeError(f"GET {SIGNED_URL} timed out"),
        ),
        capture_logs() as logs,
    ):
        url, _ = convert_audio_url_to_s3_sync(
            "call-123", SIGNED_URL, "mono_combined", provider="retell"
        )

    assert url == SIGNED_URL
    event = _failed_event(logs)
    assert event["error_type"] == "RuntimeError"
    assert event["outcome"] == "kept_original_url"
    assert "code" not in event
    assert "secret-token" not in str(logs)


def test_successful_rehost_logs_the_outcome(storage):
    with (
        patch(
            "simulate.temporal.utils.async_storage.requests.get",
            return_value=_response(b"bytes"),
        ),
        patch(
            "simulate.temporal.utils.async_storage._detected_audio_extension",
            return_value="wav",
        ),
        capture_logs() as logs,
    ):
        url, size = convert_audio_url_to_s3_sync(
            "call-123", SIGNED_URL, "mono_combined", provider="retell"
        )

    assert url.startswith("https://fagi/") and size == 5
    (event,) = [e for e in logs if e["event"] == "recording_rehosted"]
    assert event["call_id"] == "call-123"
    assert event["artifact_type"] == "mono_combined"
    assert event["size_bytes"] == 5
    assert event["outcome"] == "rehosted"


def test_vapi_rehost_without_ffmpeg_logs_the_tool_and_keeps_the_url(no_ffmpeg):
    from ee.voice.services.types.voice import PersistAudioInput
    from ee.voice.services.vapi_service import VapiService

    with patch.dict(
        "os.environ",
        {"VAPI_API_KEY": "test-key", "VAPI_API_BASE_URL": "https://api.vapi.ai"},
    ):
        service = VapiService(api_key="test-key")
    vapi_url = "https://storage.vapi.ai/rec.wav"
    with (
        patch(
            "ee.voice.services.vapi_service.download_audio_from_url",
            return_value=_wav(),
        ),
        patch(
            "tracer.utils.vapi_recording.VapiRecordingService.download_artifact_sync",
            return_value=_wav(),
        ),
        patch("tfc.utils.storage_client.get_storage_client") as client,
        capture_logs() as logs,
    ):
        out = service.persist_audio_to_s3(
            PersistAudioInput(
                audio_url=vapi_url, url_type="mono_combined", call_id="cid"
            )
        )

    assert out == vapi_url
    client.assert_not_called()
    event = _failed_event(logs)
    assert event["call_id"] == "cid"
    assert event["artifact_type"] == "mono_combined"
    assert event["provider"] == "vapi"
    assert event["code"] == "ffmpeg_unavailable"
    assert event["missing_tool"] == "ffmpeg"
    assert event["outcome"] == "kept_original_url"
