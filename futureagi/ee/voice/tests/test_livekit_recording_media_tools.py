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


def _extract(
    provider_call_data,
    *,
    stream=None,
    real_split=False,
    delete_effect=None,
    response_id="EG_1",
    response_room="call_call-1",
    on_save=None,
):
    """Run extract_and_persist_recordings on a stubbed call and egress.

    real_split: the ffmpeg tracks go through the real stream_egress_to_s3
    (its ffmpeg check), with only the S3 copy stubbed for the stereo track."""
    import asyncio
    from unittest.mock import AsyncMock

    from livekit.protocol.egress import EgressStatus

    from ee.voice.services.livekit import recording

    service = _livekit_service()
    call = MagicMock(service_provider_call_id="call_call-1")
    call.provider_call_data = provider_call_data
    call.asave = AsyncMock()
    egress_info = MagicMock(egress_id=response_id, room_name=response_room)
    egress_info.status = EgressStatus.EGRESS_COMPLETE
    egress_info.file_results = [MagicMock(filename="raw/room.ogg")]

    api = AsyncMock()
    api.egress.list_egress.return_value = MagicMock(items=[egress_info])

    async def update_fields(**changes):
        for field, value in changes.items():
            setattr(call, field, value)
        if on_save:
            on_save(**changes)
        return 1

    with (
        patch(
            "simulate.models.test_execution.CallExecution.objects.filter"
        ) as filtered,
        patch(
            "simulate.models.test_execution.CallExecution.objects.aget",
            new=AsyncMock(return_value=call),
        ),
        patch.object(service, "_create_api", return_value=api),
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
        patch.object(
            client.return_value, "delete_object", side_effect=delete_effect
        ) as delete,
    ):
        filtered.return_value.aupdate = AsyncMock(side_effect=update_fields)
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
        "message": "Some call recording tracks could not be created.",
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


def test_retry_completes_with_complementary_track_successes():
    from ee.voice.services.livekit.service import PROVIDER_KEY

    def partial(_config, _key, destination, _args):
        return None if "_assistant_" in destination else f"https://first/{destination}"

    first, _, delete, _ = _extract(
        {PROVIDER_KEY: {"egress_id": "EG_1"}}, stream=partial
    )
    delete.assert_not_called()
    assert first.error["tracks"] == ["assistant"]

    def remaining(_config, _key, destination, _args):
        assert "_assistant_" in destination
        return f"https://second/{destination}"

    second, call, delete, _ = _extract(first.provider_call_data, stream=remaining)
    assert second.error is None
    assert second.customer_recording_url == first.customer_recording_url
    assert second.recording_url == first.recording_url
    assert second.stereo_recording_url == first.stereo_recording_url
    assert second.assistant_recording_url.startswith("https://second/")
    assert "recording_error" not in call.provider_call_data[PROVIDER_KEY]
    delete.assert_called_once()


def _output_url(_config, _source, destination, _args):
    return f"https://media/{destination}"


def _assert_returned_tracks(result, saved):
    assert saved == {
        "combined": result.recording_url,
        "stereo": result.stereo_recording_url,
        "assistant": result.assistant_recording_url,
        "customer": result.customer_recording_url,
    }
    assert all(saved.values())


def test_retry_after_raw_deletion_returns_persisted_tracks():
    from ee.voice.services.livekit.service import PROVIDER_KEY

    # S3 DeleteObject returns success even when the key no longer exists.
    objects = {"raw/room.ogg": b"audio"}

    def delete_object(*, Bucket, Key):
        objects.pop(Key, None)
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    first, _, _, _ = _extract(
        {PROVIDER_KEY: {"egress_id": "EG_1"}},
        stream=_output_url,
        delete_effect=delete_object,
    )
    assert not objects
    missing_raw = MagicMock(side_effect=FileNotFoundError)
    second, _, delete, _ = _extract(
        first.provider_call_data, stream=missing_raw, delete_effect=delete_object
    )
    missing_raw.assert_not_called()
    delete.assert_called_once_with(Bucket="livekit-egress", Key="raw/room.ogg")
    assert second == first and second.error is None


def test_complete_map_saved_before_cleanup_survives_worker_stop():
    import asyncio
    from copy import deepcopy

    from ee.voice.services.livekit.service import PROVIDER_KEY

    data = {PROVIDER_KEY: {"egress_id": "EG_1", "recording_files": ["other/raw.ogg"]}}
    persisted = {}

    def stop_after_save(**changes):
        persisted.update(deepcopy(changes["provider_call_data"]))
        raise asyncio.CancelledError

    before_crash = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        _extract(
            data,
            stream=_output_url,
            on_save=stop_after_save,
            delete_effect=before_crash,
        )
    before_crash.assert_not_called()
    no_stream = MagicMock()
    replay, _, delete, _ = _extract(persisted, stream=no_stream)
    no_stream.assert_not_called()
    delete.assert_called_once_with(Bucket="livekit-egress", Key="raw/room.ogg")
    _assert_returned_tracks(replay, persisted[PROVIDER_KEY]["recording"])
    assert replay.error is None


def test_transient_cleanup_failure_then_replay_preserves_all_urls():
    from ee.voice.services.livekit.service import PROVIDER_KEY

    first, call, _, _ = _extract(
        {PROVIDER_KEY: {"egress_id": "EG_1"}},
        stream=_output_url,
        delete_effect=TimeoutError,
    )
    saved = dict(call.provider_call_data[PROVIDER_KEY]["recording"])
    _assert_returned_tracks(first, saved)
    assert (
        first.error["code"] == "recording_cleanup_failed"
        and first.error["tracks"] == []
    )
    assert call.provider_call_data[PROVIDER_KEY]["recording_error"] == first.error
    no_stream = MagicMock()
    second, call, delete, _ = _extract(first.provider_call_data, stream=no_stream)
    no_stream.assert_not_called()
    delete.assert_called_once_with(Bucket="livekit-egress", Key="raw/room.ogg")
    _assert_returned_tracks(second, saved)
    assert (
        second.error is None
        and "recording_error" not in call.provider_call_data[PROVIDER_KEY]
    )
    call.asave.assert_not_awaited()


@pytest.mark.parametrize(
    ("response_id", "response_room"), [("EG_2", "call_call-1"), ("EG_1", "call_other")]
)
def test_cleanup_replay_rejects_foreign_egress_result(response_id, response_room):
    from ee.voice.services.livekit.service import PROVIDER_KEY

    first, _, _, _ = _extract({PROVIDER_KEY: {"egress_id": "EG_1"}}, stream=_output_url)
    no_stream = MagicMock()
    result, _, delete, _ = _extract(
        first.provider_call_data,
        stream=no_stream,
        response_id=response_id,
        response_room=response_room,
    )
    delete.assert_not_called()
    no_stream.assert_not_called()
    _assert_returned_tracks(result, first.provider_call_data[PROVIDER_KEY]["recording"])
    assert result.error["code"] == "recording_cleanup_failed"


@pytest.mark.parametrize("changed", ["egress_id", "call_id"])
def test_retry_does_not_reuse_another_recording_source(changed):
    from ee.voice.services.livekit.service import PROVIDER_KEY

    first, _, _, _ = _extract(
        {PROVIDER_KEY: {"egress_id": "EG_1"}},
        stream=lambda _c, _k, dest, _a: f"https://old/{dest}",
    )
    data = first.provider_call_data
    if changed == "egress_id":
        data[PROVIDER_KEY]["egress_id"] = "EG_2"
    else:
        data[PROVIDER_KEY]["recording_source"]["call_id"] = "other-call"
    retry, call, delete, _ = _extract(data, stream=MagicMock(side_effect=OSError))
    delete.assert_not_called()
    assert retry.error["tracks"] == ["assistant", "customer", "mono", "stereo"]
    assert call.provider_call_data[PROVIDER_KEY]["recording"] == {}
    assert "https://old/" not in str(call.provider_call_data)
    assert call.recording_url is None and call.stereo_recording_url is None
    assert not call.recording_available


def test_unattributed_legacy_links_are_preserved_but_not_used_for_a_new_failure():
    from ee.voice.services.livekit.service import PROVIDER_KEY

    saved = {"combined": "https://media/legacy"}
    result, _, delete, _ = _extract(
        {PROVIDER_KEY: {"egress_id": "EG_1", "recording": saved}},
        stream=MagicMock(side_effect=FileNotFoundError),
    )
    delete.assert_not_called()
    assert result.recording_url is None
    assert result.error["tracks"] == ["assistant", "customer", "mono", "stereo"]
    assert result.provider_call_data[PROVIDER_KEY]["unattributed_recording"] == saved


@pytest.mark.parametrize("incoming_egress", [None, "EG_2"])
def test_feeder_retry_preserves_completed_preupgrade_call(incoming_egress):
    import asyncio
    from unittest.mock import AsyncMock

    from ee.voice.services.livekit.service import PROVIDER_KEY
    from simulate.models.test_execution import CallExecution, CallTranscript
    from simulate.serializers.test_execution import CallExecutionDetailSerializer

    # Before recording_source, all four keys included the call and timestamp,
    # not the egress. The worker had already removed the raw object on success.
    prefix = "https://media/call-recordings/call-1/20260101T000000"
    saved = {
        "combined": f"{prefix}_recording.mp3",
        **{
            k: f"{prefix}_{k}_recording.mp3"
            for k in ("stereo", "assistant", "customer")
        },
    }
    call = MagicMock(
        asave=AsyncMock(),
        recording_available=True,
        recording_url=saved["combined"],
        stereo_recording_url=saved["stereo"],
        simulation_call_type="voice",
        call_metadata={"call_direction": "inbound"},
        provider_call_data={
            PROVIDER_KEY: {
                "egress_id": "EG_1",
                "recording": saved.copy(),
                "assistant_recording_url": saved["assistant"],
                "customer_recording_url": saved["customer"],
            }
        },
    )
    service = _livekit_service()
    serializer = CallExecutionDetailSerializer()
    before = serializer.get_recordings(call)
    assert set(saved.values()) == {before[k] for k in saved}
    feeder_data = {"room_name": "call_call-1"}
    if incoming_egress:
        feeder_data["egress_id"] = incoming_egress
    with (
        patch.object(CallExecution.objects, "aget", new=AsyncMock(return_value=call)),
        patch.object(CallTranscript.objects, "filter") as transcripts,
        patch.object(service, "_create_api") as api,
        patch("ee.voice.services.livekit.recording.delete_egress_recording") as delete,
    ):
        transcripts.return_value.acount = AsyncMock(return_value=0)
        asyncio.run(
            service.fetch_and_store_call_data(
                "call-1",
                "call_call-1",
                "completed",
                provider_data=feeder_data,
            )
        )
        call.asave.reset_mock()
        result = asyncio.run(service.extract_and_persist_recordings("call-1"))
        api.assert_not_called()
        delete.assert_not_called()
        call.asave.assert_not_awaited()

    assert result.error["code"] == "recording_source_unverified"
    assert result.error["tracks"] == []
    assert result.provider_call_data is None
    assert not any(
        (
            result.recording_url,
            result.stereo_recording_url,
            result.assistant_recording_url,
            result.customer_recording_url,
        )
    )
    bucket = call.provider_call_data[PROVIDER_KEY]
    assert bucket["egress_id"] == (incoming_egress or "EG_1")
    assert "recording_source" not in bucket and "recording_error" not in bucket
    assert call.recording_available and serializer.get_recordings(call) == before
