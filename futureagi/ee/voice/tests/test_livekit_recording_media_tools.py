"""The LiveKit channel split names a missing ffmpeg before it reads the
recording; a stereo copy needs no ffmpeg."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tfc.utils.media_tools import MediaToolUnavailable

pytestmark = pytest.mark.unit


@pytest.fixture
def no_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))


def test_channel_split_names_ffmpeg_before_reading_the_recording(no_ffmpeg):
    from ee.voice.services.livekit import recording

    with (
        patch.object(recording, "_s3_stream_iter") as source,
        patch.object(
            subprocess, "Popen", side_effect=AssertionError("ffmpeg must not start")
        ),
        pytest.raises(MediaToolUnavailable) as caught,
    ):
        recording.stream_egress_to_s3(
            MagicMock(), "egress/call.ogg", "calls/c.mp3", ["-af", "pan=mono|c0=c0"]
        )
    assert str(caught.value).startswith("ffmpeg is not installed in this backend")
    assert "call recording channel split" in str(caught.value)
    source.assert_not_called()


def test_stereo_copy_needs_no_ffmpeg(no_ffmpeg):
    from ee.voice.services.livekit import recording

    body = MagicMock()
    with (
        patch.object(recording, "_create_s3_client") as client,
        patch.object(
            recording, "upload_stream_to_s3", return_value="s3://calls/c.ogg"
        ) as upload,
    ):
        client.return_value.get_object.return_value = {"Body": body}
        url = recording.stream_egress_to_s3(
            MagicMock(), "egress/call.ogg", "calls/c.ogg"
        )
    assert url == "s3://calls/c.ogg"
    upload.assert_called_once_with(body, "calls/c.ogg")


# ---------------------------------------------------------------------------
# extract_and_persist_recordings: a failed track keeps the raw egress file and
# is recorded on the call, with ffmpeg missing from PATH (no_ffmpeg).
# ---------------------------------------------------------------------------


def _livekit_service():
    from ee.voice.services.livekit.config import LiveKitConfig

    config = LiveKitConfig(
        url="ws://localhost:7880",
        api_key="test-api-key",
        api_secret="test-api-secret-min-32-characters",
        sip_inbound_trunk_id="trunk-123",
        sip_outbound_trunk_id="trunk-456",
        egress_bucket="livekit-egress",
        s3_access_key="minioadmin",
        s3_secret_key="minioadmin",
        s3_region="us-east-1",
        backend_callback_url="http://localhost:8000",
    )
    with patch(
        "ee.voice.services.livekit.service.LiveKitConfig.from_env",
        return_value=config,
    ):
        from ee.voice.services.livekit.service import LivekitService

        return LivekitService()


def _extract(provider_call_data, *, stream=None, real_split=False):
    """Run extract_and_persist_recordings on a stubbed call and egress.

    real_split: the ffmpeg tracks go through the real stream_egress_to_s3
    (its ffmpeg check), with only the S3 copy stubbed for the stereo track."""
    import asyncio
    from unittest.mock import AsyncMock

    from livekit.protocol.egress import EgressStatus

    from ee.voice.services.livekit import recording

    service = _livekit_service()
    call = MagicMock()
    call.provider_call_data = provider_call_data
    call.asave = AsyncMock()
    egress_info = MagicMock()
    egress_info.status = EgressStatus.EGRESS_COMPLETE
    egress_info.file_results = [MagicMock(filename="raw/room.ogg")]

    with (
        patch(
            "simulate.models.test_execution.CallExecution.objects.aget",
            new=AsyncMock(return_value=call),
        ),
        patch.object(service, "_create_api", return_value=AsyncMock()),
        patch.object(
            recording,
            "poll_egress_completion",
            new=AsyncMock(return_value=egress_info),
        ),
        patch.object(recording, "_create_s3_client") as client,
        patch.object(
            recording,
            "upload_stream_to_s3",
            side_effect=lambda _body, key: f"https://media/{key}",
        ),
        patch.object(recording, "_s3_stream_iter") as source,
        patch.object(
            subprocess, "Popen", side_effect=AssertionError("ffmpeg must not start")
        ),
        patch.object(recording, "delete_egress_recording") as delete,
    ):
        client.return_value.get_object.return_value = {"Body": MagicMock()}
        if not real_split:
            patch_stream = patch.object(
                recording, "stream_egress_to_s3", side_effect=stream
            )
            with patch_stream:
                result = asyncio.run(service.extract_and_persist_recordings("call-1"))
        else:
            result = asyncio.run(service.extract_and_persist_recordings("call-1"))
    return result, call, delete, source


def test_missing_ffmpeg_keeps_the_raw_recording_and_names_the_error(no_ffmpeg):
    from ee.voice.services.livekit.service import PROVIDER_KEY

    result, call, delete, source = _extract(
        {PROVIDER_KEY: {"egress_id": "EG_1"}}, real_split=True
    )

    # The stereo copy needs no ffmpeg and is kept; the three cuts are not.
    assert result.stereo_recording_url.endswith("_stereo_recording.mp3")
    assert result.recording_url is None
    assert result.assistant_recording_url is None
    assert result.customer_recording_url is None
    source.assert_not_called()
    # The raw egress file is the only full recording left: not deleted.
    delete.assert_not_called()

    assert result.error["code"] == "ffmpeg_unavailable"
    assert result.error["tracks"] == ["assistant", "customer", "mono"]
    assert result.error["message"].startswith("ffmpeg is not installed")
    assert "call recording channel split" in result.error["message"]
    stored = call.provider_call_data[PROVIDER_KEY]
    assert stored["recording_error"] == result.error
    assert stored["recording"] == {"stereo": result.stereo_recording_url}
    # The activity saves what this method stored, not its older copy.
    assert result.provider_call_data is call.provider_call_data


def test_any_failed_track_keeps_the_raw_recording_without_its_error_text():
    from ee.voice.services.livekit.service import PROVIDER_KEY

    def stream(_config, _object_key, s3_key, ffargs):
        if "_customer_" in s3_key:
            raise RuntimeError("upload to s3://internal-bucket/x failed")
        return f"https://media/{s3_key}"

    result, call, delete, _ = _extract(
        {PROVIDER_KEY: {"egress_id": "EG_1"}}, stream=stream
    )

    delete.assert_not_called()
    assert result.customer_recording_url is None
    assert result.assistant_recording_url.endswith("_assistant_recording.mp3")
    assert result.error == {
        "code": "recording_split_failed",
        "message": "Some call recording tracks could not be created. The raw "
        "recording is kept.",
        "tracks": ["customer"],
    }
    assert "internal-bucket" not in str(call.provider_call_data)


def test_all_tracks_made_deletes_the_raw_recording_and_clears_an_old_error():
    from ee.voice.services.livekit.service import PROVIDER_KEY

    result, call, delete, _ = _extract(
        {
            PROVIDER_KEY: {
                "egress_id": "EG_1",
                "recording_error": {"code": "ffmpeg_unavailable"},
            }
        },
        stream=lambda _c, _o, s3_key, _f: f"https://media/{s3_key}",
    )

    delete.assert_called_once()
    assert result.error is None
    assert "recording_error" not in call.provider_call_data[PROVIDER_KEY]
    assert set(call.provider_call_data[PROVIDER_KEY]["recording"]) == {
        "stereo",
        "combined",
        "assistant",
        "customer",
    }


def test_empty_track_url_keeps_raw_recording():
    from ee.voice.services.livekit.service import PROVIDER_KEY

    def stream(_config, _key, destination, _args):
        return "" if "_customer_" in destination else f"https://media/{destination}"

    result, _, delete, _ = _extract(
        {PROVIDER_KEY: {"egress_id": "EG_1"}}, stream=stream
    )
    delete.assert_not_called()
    assert result.error["tracks"] == ["customer"]


def test_interrupted_input_is_not_reported_as_a_successful_split():
    from io import BytesIO

    from ee.voice.services.livekit import recording

    def interrupted():
        yield b"valid prefix"
        raise OSError("interrupted source")

    process = MagicMock(
        returncode=0, stdin=BytesIO(), stderr=BytesIO(), stdout=BytesIO(b"prefix")
    )
    with (
        patch.object(recording, "require_ffmpeg"),
        patch.object(recording, "_s3_stream_iter", return_value=interrupted()),
        patch.object(subprocess, "Popen", return_value=process),
        patch.object(
            recording, "upload_stream_to_s3", return_value="https://media/partial"
        ),
        pytest.raises(RuntimeError, match="Incomplete egress recording input"),
    ):
        recording.stream_egress_to_s3(MagicMock(), "source", "dest", ["-ac", "1"])
