"""Regression tests for provider-neutral deterministic recording rehosting."""

from unittest.mock import Mock, patch

from simulate.temporal.utils.async_storage import convert_audio_url_to_s3_sync


def _response(audio_bytes: bytes) -> Mock:
    response = Mock()
    response.iter_content.return_value = [audio_bytes]
    return response


def test_repeated_rehost_reuses_detected_format_object_and_stored_size():
    """A later poll reuses the durable WAV object without another download."""
    stored: dict[str, int] = {}
    uploads: list[str] = []
    storage_client = Mock()

    def stat_object(_bucket: str, object_key: str) -> Mock:
        if object_key not in stored:
            raise Exception("not found")
        return Mock(size=stored[object_key])

    def upload(audio_data: dict, *, object_key: str) -> str:
        uploads.append(object_key)
        stored[object_key] = len(audio_data["bytes"])
        return f"https://fi-customer-data.s3.amazonaws.com/{object_key}"

    storage_client.stat_object.side_effect = stat_object
    audio_bytes = b"wav-recording"
    with patch(
        "simulate.temporal.utils.async_storage._is_fagi_storage_url",
        return_value=False,
    ), patch(
        "tfc.utils.storage_client.get_storage_client", return_value=storage_client
    ), patch(
        "tfc.utils.storage_client.get_object_url",
        side_effect=lambda _bucket, key: f"https://fi-customer-data.s3.amazonaws.com/{key}",
    ), patch(
        "simulate.temporal.utils.async_storage.requests.get",
        return_value=_response(audio_bytes),
    ) as get, patch(
        "simulate.temporal.utils.async_storage._detected_audio_extension",
        return_value="wav",
    ), patch("tfc.utils.storage.upload_audio_to_s3", side_effect=upload):
        first_url, first_bytes = convert_audio_url_to_s3_sync(
            "call-123",
            "https://provider.example/recording",
            "mono_combined",
            provider="vapi",
            project_id="project-1",
        )
        second_url, second_bytes = convert_audio_url_to_s3_sync(
            "call-123",
            "https://provider.example/rotated-signed-url",
            "mono_combined",
            provider="vapi",
            project_id="project-1",
        )

    expected_key = "call-recordings/project-1/vapi/call-123/mono_combined.wav"
    expected_url = f"https://fi-customer-data.s3.amazonaws.com/{expected_key}"
    assert (first_url, first_bytes) == (expected_url, len(audio_bytes))
    assert (second_url, second_bytes) == (expected_url, len(audio_bytes))
    assert uploads == [expected_key]
    get.assert_called_once()


def test_rehost_storage_key_isolated_by_provider():
    """The same provider call ID cannot collide between Vapi and Retell."""
    uploads: list[str] = []
    storage_client = Mock()
    storage_client.stat_object.side_effect = Exception("not found")

    def upload(_audio_data: dict, *, object_key: str) -> str:
        uploads.append(object_key)
        return f"https://fi-customer-data.s3.amazonaws.com/{object_key}"

    with patch(
        "simulate.temporal.utils.async_storage._is_fagi_storage_url",
        return_value=False,
    ), patch(
        "tfc.utils.storage_client.get_storage_client", return_value=storage_client
    ), patch(
        "simulate.temporal.utils.async_storage.requests.get",
        return_value=_response(b"recording"),
    ), patch(
        "simulate.temporal.utils.async_storage._detected_audio_extension",
        return_value="mp3",
    ), patch("tfc.utils.storage.upload_audio_to_s3", side_effect=upload):
        convert_audio_url_to_s3_sync(
            "shared-call",
            "https://provider-recordings.s3.amazonaws.com/recording",
            "mono_combined",
            provider="vapi",
            project_id="project-1",
        )
        convert_audio_url_to_s3_sync(
            "shared-call",
            "https://retell.example/recording",
            "mono_combined",
            provider="retell",
            project_id="project-1",
        )

    assert uploads == [
        "call-recordings/project-1/vapi/shared-call/mono_combined.mp3",
        "call-recordings/project-1/retell/shared-call/mono_combined.mp3",
    ]
