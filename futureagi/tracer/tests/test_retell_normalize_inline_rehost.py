"""Regression tests for Retell's inline recording rehost normalization."""

from unittest.mock import patch

from tracer.utils.retell import normalize_retell_data

RETELL_MONO_URL = "https://retell-cdn.example.test/call-123-mono.wav"
RETELL_STEREO_URL = "https://retell-cdn.example.test/call-123-stereo.wav"
DURABLE_MONO_URL = (
    "https://fi-customer-data.s3.amazonaws.com/call-recordings/project-1/"
    "retell/call-123/mono_combined.wav"
)
DURABLE_STEREO_URL = (
    "https://fi-customer-data.s3.amazonaws.com/call-recordings/project-1/"
    "retell/call-123/stereo.wav"
)


def _retell_log(**overrides):
    log = {
        "call_id": "call-123",
        "call_status": "ended",
        "recording_url": RETELL_MONO_URL,
        "recording_multi_channel_url": RETELL_STEREO_URL,
        "call_cost": {"combined_cost": 0.01, "product_costs": []},
    }
    log.update(overrides)
    return log


def test_normalize_retell_data_maps_call_analytics_without_mutating_raw_log():
    log = _retell_log(
        start_timestamp=0,
        end_timestamp=5000,
        latency={"e2e": {"values": [100, 300]}},
        llm_token_usage={"values": [10, 20]},
        transcript_with_tool_calls=[
            {
                "role": "user",
                "content": "one two",
                "words": [{"start": 0, "end": 1}, {"start": 1, "end": 2}],
            },
            {
                "role": "agent",
                "content": "three four",
                "words": [{"start": 2.2, "end": 3.2}, {"start": 3.2, "end": 4.2}],
            },
        ],
    )

    result = normalize_retell_data(log)
    attrs = result["span_attributes"]

    assert result["total_tokens"] == 30
    assert attrs["avg_agent_latency_ms"] == 200
    assert attrs["call.user_wpm"] == 60
    assert attrs["call.bot_wpm"] == 60
    assert attrs["call.talk_ratio"] == 1
    assert attrs["user_interruption_count"] == 0
    assert attrs["ai_interruption_count"] == 0
    assert log["llm_token_usage"] == {"values": [10, 20]}


def test_normalize_retell_data_uses_only_provider_duration_ms():
    with_provider_duration = normalize_retell_data(
        _retell_log(
            duration_ms=7000,
            start_timestamp=0,
            end_timestamp=5000,
        )
    )
    without_provider_duration = normalize_retell_data(
        _retell_log(
            start_timestamp=0,
            end_timestamp=5000,
        )
    )

    assert with_provider_duration["span_attributes"]["call.duration"] == 7
    assert without_provider_duration["span_attributes"]["call.duration"] is None


def test_normalize_retell_data_participant_phone_by_direction():
    inbound = normalize_retell_data(
        _retell_log(
            direction="inbound",
            from_number="+111",
            to_number="+222",
        )
    )
    inbound_missing_from = normalize_retell_data(
        _retell_log(
            direction="inbound",
            from_number=None,
            to_number="+222",
        )
    )
    outbound = normalize_retell_data(
        _retell_log(
            direction="outbound",
            from_number="+111",
            to_number="+222",
        )
    )
    unknown = normalize_retell_data(
        _retell_log(
            direction="",
            from_number=None,
            to_number="+222",
        )
    )

    assert inbound["span_attributes"]["call.participant_phone_number"] == "+111"
    assert (
        inbound_missing_from["span_attributes"]["call.participant_phone_number"] is None
    )
    assert outbound["span_attributes"]["call.participant_phone_number"] == "+222"
    assert unknown["span_attributes"]["call.participant_phone_number"] == "+222"


def test_normalize_retell_data_counts_agent_interruptions_from_word_overlap():
    log = _retell_log(
        transcript_with_tool_calls=[
            {
                "role": "user",
                "content": "one two",
                "words": [{"start": 0, "end": 1}, {"start": 1, "end": 2}],
            },
            {
                "role": "agent",
                "content": "three four",
                "words": [{"start": 1.8, "end": 2.8}, {"start": 2.8, "end": 3.8}],
            },
        ]
    )

    attrs = normalize_retell_data(log)["span_attributes"]

    assert attrs["user_interruption_count"] == 0
    assert attrs["ai_interruption_count"] == 1


def test_normalize_retell_data_preserves_flattened_token_value_attributes():
    log = _retell_log(llm_token_usage={"values": [10, 20]})

    attrs = normalize_retell_data(log)["span_attributes"]

    assert attrs["llm_token_usage.0"] == 10
    assert attrs["llm_token_usage.1"] == 20


def test_normalize_retell_data_uses_none_for_unavailable_analytics():
    result = normalize_retell_data(_retell_log())
    attrs = result["span_attributes"]

    assert result["prompt_tokens"] is None
    assert result["completion_tokens"] is None
    assert result["total_tokens"] is None
    assert attrs["call.user_wpm"] is None
    assert attrs["call.bot_wpm"] is None
    assert attrs["call.talk_ratio"] is None


def test_rehosts_both_retell_recordings_with_provider_and_project_scope():
    def convert(*, audio_url, **kwargs):
        return {
            RETELL_MONO_URL: (DURABLE_MONO_URL, 100),
            RETELL_STEREO_URL: (DURABLE_STEREO_URL, 200),
        }[audio_url]

    with patch(
        "tracer.utils.retell.convert_audio_url_to_s3_sync", side_effect=convert
    ) as mock_convert:
        result = normalize_retell_data(_retell_log(), project_id="project-1")

    attrs = result["span_attributes"]
    assert attrs["conversation.recording.mono.combined"] == DURABLE_MONO_URL
    assert attrs["conversation.recording.stereo"] == DURABLE_STEREO_URL
    assert result["rehost_uploads"] == {"mono_combined": 100, "stereo": 200}
    assert result["rehost_bytes_uploaded"] == 300

    assert mock_convert.call_count == 2
    assert {call.kwargs["url_type"] for call in mock_convert.call_args_list} == {
        "mono_combined",
        "stereo",
    }
    for call in mock_convert.call_args_list:
        assert call.kwargs["call_id"] == "call-123"
        assert call.kwargs["provider"] == "retell"
        assert call.kwargs["project_id"] == "project-1"
        assert call.kwargs["artifact_type"] == call.kwargs["url_type"]


def test_partial_rehost_failure_preserves_source_and_continues_other_artifact():
    def convert(*, audio_url, **kwargs):
        if audio_url == RETELL_MONO_URL:
            raise RuntimeError("download failed")
        return DURABLE_STEREO_URL, 200

    with patch("tracer.utils.retell.convert_audio_url_to_s3_sync", side_effect=convert):
        result = normalize_retell_data(_retell_log(), project_id="project-1")

    attrs = result["span_attributes"]
    assert attrs["conversation.recording.mono.combined"] == RETELL_MONO_URL
    assert attrs["conversation.recording.stereo"] == DURABLE_STEREO_URL
    assert result["rehost_uploads"] == {"stereo": 200}


def test_no_project_or_recording_urls_performs_no_conversion():
    with patch("tracer.utils.retell.convert_audio_url_to_s3_sync") as mock_convert:
        no_project = normalize_retell_data(_retell_log())
        no_urls = normalize_retell_data(
            _retell_log(recording_url=None, recording_multi_channel_url=None),
            project_id="project-1",
        )

    mock_convert.assert_not_called()
    assert no_project["rehost_uploads"] == {}
    assert no_urls["rehost_uploads"] == {}


def test_already_owned_recording_url_is_skipped():
    log = _retell_log(
        recording_url=DURABLE_MONO_URL,
        recording_multi_channel_url=None,
    )
    with patch("tracer.utils.retell.convert_audio_url_to_s3_sync") as mock_convert:
        result = normalize_retell_data(log, project_id="project-1")

    mock_convert.assert_not_called()
    assert (
        result["span_attributes"]["conversation.recording.mono.combined"]
        == DURABLE_MONO_URL
    )
    assert result["rehost_uploads"] == {}


def test_normalize_retell_data_latency_ms_absent_from_top_level():
    """latency_ms must not appear in the top-level normalized result
    while avg_agent_latency_ms remains in span_attributes."""
    log = _retell_log(
        latency={"e2e": {"values": [100, 300]}},
        transcript_with_tool_calls=[],
    )
    result = normalize_retell_data(log)
    assert "latency_ms" not in result
    assert result["span_attributes"]["avg_agent_latency_ms"] == 200


def test_normalize_retell_data_wpm_excludes_sub_100ms_segments():
    """Segments shorter than 100ms must not contribute to WPM."""
    log = _retell_log(
        transcript_with_tool_calls=[
            {
                "role": "agent",
                "content": "fast",
                "words": [{"start": 0, "end": 0.05}],
            },
            {
                "role": "agent",
                "content": "normal speech",
                "words": [
                    {"start": 1, "end": 1.5},
                    {"start": 1.5, "end": 2.0},
                ],
            },
        ],
    )
    attrs = normalize_retell_data(log)["span_attributes"]
    # 0.05s segment excluded; 1.0s segment with 2 words = 120 WPM
    assert attrs["call.bot_wpm"] == 120
    assert attrs["call.user_wpm"] is None


def test_normalize_retell_data_wpm_capped_at_300():
    """WPM must be capped at 300 per role."""
    log = _retell_log(
        transcript_with_tool_calls=[
            {
                "role": "user",
                "content": "extremely rapid speech here many words",
                "words": [
                    {"start": 0, "end": 0.1},
                    {"start": 0.1, "end": 0.2},
                    {"start": 0.2, "end": 0.3},
                    {"start": 0.3, "end": 0.4},
                    {"start": 0.4, "end": 0.5},
                ],
            },
        ],
    )
    attrs = normalize_retell_data(log)["span_attributes"]
    # 5 words in 0.5s = 600 WPM, capped to 300
    assert attrs["call.user_wpm"] == 300


def test_normalize_retell_data_small_overlap_counts_as_interruption():
    """Any positive cross-role overlap must count as interruption,
    even when the overlap is under 100ms."""
    log = _retell_log(
        transcript_with_tool_calls=[
            {
                "role": "user",
                "content": "hello",
                "words": [{"start": 0, "end": 1}],
            },
            {
                "role": "agent",
                "content": "hi there",
                "words": [{"start": 0.95, "end": 1.5}, {"start": 1.5, "end": 2.0}],
            },
        ],
    )
    attrs = normalize_retell_data(log)["span_attributes"]
    # overlap = 1.0 - 0.95 = 0.05 > 0
    assert attrs["ai_interruption_count"] == 1


def test_normalize_retell_data_out_of_order_transcripts_sorted():
    """Segments must be sorted by start before overlap and WPM computation."""
    log = _retell_log(
        transcript_with_tool_calls=[
            {
                "role": "agent",
                "content": "later start",
                "words": [{"start": 2, "end": 3}],
            },
            {
                "role": "user",
                "content": "earlier start",
                "words": [{"start": 0, "end": 2.5}],
            },
        ],
    )
    attrs = normalize_retell_data(log)["span_attributes"]
    # After sort: user (0-2.5), agent (2-3) → overlap = 2.5 - 2 = 0.5
    assert attrs["ai_interruption_count"] == 1
    assert attrs["call.talk_ratio"] is not None
