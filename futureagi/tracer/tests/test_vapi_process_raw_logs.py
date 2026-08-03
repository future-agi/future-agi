"""
Tests for the span_attributes overlay in ObservabilityService.process_raw_logs.

Moved from test_observability_recordings_rehost.py (which was deleted in
Phase 2 of the inline Vapi recording rehost work — the backfill Temporal
task no longer exists, but these overlay tests remain valid).

Run with: pytest tracer/tests/test_vapi_process_raw_logs.py -v
"""

from tracer.models.observability_provider import ProviderChoices
from tracer.services.observability_providers import ObservabilityService


class TestProcessRawLogsOverlay:
    """Tests for the span_attributes overlay in ObservabilityService.process_raw_logs."""

    def test_vapi_overlay_prefers_flat_s3_alias(self):
        """The flat S3 alias (recording_url) is preferred over raw_log provider URLs."""
        raw_log = {
            "id": "vapi-call-1",
            "recordingUrl": "https://storage.vapi.ai/combined.mp3",
            "artifact": {"stereoRecordingUrl": "https://storage.vapi.ai/stereo.mp3"},
            "messages": [],
        }
        span_attributes = {
            "recording_url": "https://fagi.s3.amazonaws.com/x/combined.mp3",
            "stereo_recording_url": "https://fagi.s3.amazonaws.com/x/stereo.mp3",
        }

        result = ObservabilityService.process_raw_logs(
            raw_log, ProviderChoices.VAPI, span_attributes=span_attributes
        )

        assert result["recording_url"] == span_attributes["recording_url"]
        assert result["stereo_recording_url"] == span_attributes[
            "stereo_recording_url"
        ]

    def test_vapi_overlay_new_shape_before_legacy(self):
        """New shape (artifact.recording.mono.combinedUrl) beats legacy recordingUrl."""
        raw_log = {
            "id": "vapi-call-1",
            "recordingUrl": "https://storage.vapi.ai/legacy.mp3",
            "artifact": {
                "recording": {"mono": {"combinedUrl": "https://storage.vapi.ai/new-shape.mp3"}}
            },
            "messages": [],
        }

        result = ObservabilityService.process_raw_logs(
            raw_log, ProviderChoices.VAPI
        )

        assert result["recording_url"] == "https://storage.vapi.ai/new-shape.mp3"

    def test_no_overlay_keeps_provider_urls(self):
        """Without span_attributes, the field-chain fallback returns the raw_log value."""
        raw_log = {
            "id": "vapi-call-1",
            "recordingUrl": "https://storage.vapi.ai/combined.mp3",
            "artifact": {"stereoRecordingUrl": "https://storage.vapi.ai/stereo.mp3"},
            "messages": [],
        }

        result = ObservabilityService.process_raw_logs(
            raw_log, ProviderChoices.VAPI
        )

        assert result["recording_url"] == "https://storage.vapi.ai/combined.mp3"
        assert (
            result["stereo_recording_url"] == "https://storage.vapi.ai/stereo.mp3"
        )

    def test_retell_overlay_prefers_durable_span_urls(self):
        """ClickHouse span attributes override Retell's expiring provider URLs."""
        raw_log = {
            "call_id": "retell-call-1",
            "recording_url": "https://retell.example/raw-mono.wav",
            "recording_multi_channel_url": "https://retell.example/raw-stereo.wav",
            "call_cost": {"product_costs": []},
        }
        span_attributes = {
            "conversation.recording.mono.combined": (
                "https://fi-customer-data.s3.amazonaws.com/rehosted/mono.wav"
            ),
            "conversation.recording.stereo": (
                "https://fi-customer-data.s3.amazonaws.com/rehosted/stereo.wav"
            ),
        }

        result = ObservabilityService.process_raw_logs(
            raw_log, ProviderChoices.RETELL, span_attributes=span_attributes
        )

        assert result["recording_url"] == span_attributes[
            "conversation.recording.mono.combined"
        ]
        assert result["stereo_recording_url"] == span_attributes[
            "conversation.recording.stereo"
        ]


class TestVapiDisplayFieldsParity:
    """The normalizer's _extract_display_fields (ingest-time materialization)
    must match _process_vapi_logs (read-path derivation) so the v2 typed-attr
    read path stays in parity with legacy. Guards the intentional duplication."""

    @staticmethod
    def _sample():
        return {
            "id": "vapi-1",
            "type": "inboundPhoneCall",
            "status": "ended",
            "startedAt": "2026-06-19T12:00:00.000Z",
            "endedAt": "2026-06-19T12:01:00.000Z",
            "createdAt": "2026-06-19T11:59:00.000Z",
            "customer": {"number": "+15551230000"},
            "phoneNumber": {"number": "+15559990000"},
            "assistantId": "asst_9",
            "summary": "Booked a demo",
            "overallScore": 7,
            "cost": 0.25,
            # Assistant phone comes from variableValues.phoneNumber (string only).
            "variableValues": {"phoneNumber": "+18005551234"},
            "messages": [
                {"role": "assistant", "message": "Hi", "duration": 2000,
                 "secondsFromStart": 0, "time": 1700000000000},
                {"role": "user", "message": "Hello", "duration": 1000,
                 "secondsFromStart": 2, "time": 1700000002000},
                {"role": "bot", "message": "Bye", "duration": 1000,
                 "secondsFromStart": 3, "time": 1700000003000},
            ],
        }

    def test_display_fields_match_process_raw_logs(self):
        from tracer.utils.otel import CallAttributes
        from tracer.utils.vapi import (
            _extract_common_call_fields,
            _extract_conversation,
            _extract_metadata,
            _extract_recording_urls,
        )

        # Distributed across the topical extract helpers (not one function).
        attrs: dict = {}
        sample = self._sample()
        _extract_common_call_fields(sample, attrs)
        _extract_metadata(sample, attrs)
        _extract_conversation(sample, attrs)
        _extract_recording_urls(sample, attrs)
        # Separate copy — process_raw_logs mutates messages in place.
        processed = ObservabilityService.process_raw_logs(
            self._sample(), ProviderChoices.VAPI
        )

        assert attrs[CallAttributes.CUSTOMER_NAME] == processed["customer_name"]
        assert attrs[CallAttributes.STATUS_DISPLAY] == processed["status"]
        assert attrs[CallAttributes.CALL_TYPE] == processed["call_type"]
        assert attrs[CallAttributes.SUMMARY] == processed["call_summary"]
        assert attrs[CallAttributes.OVERALL_SCORE] == processed["overall_score"]
        assert attrs[CallAttributes.ASSISTANT_ID] == processed["assistant_id"]
        # assistant_phone intentionally diverges from legacy: from
        # variableValues.phoneNumber (string), not the empty top-level field.
        assert attrs[CallAttributes.ASSISTANT_PHONE_NUMBER] == "+18005551234"
        assert attrs[CallAttributes.COST_CENTS] == processed["cost_cents"]
        assert attrs[CallAttributes.MESSAGE_COUNT] == processed["message_count"]
        assert (
            attrs[CallAttributes.TRANSCRIPT_AVAILABLE]
            == processed["transcript_available"]
        )
        assert attrs[CallAttributes.STARTED_AT] == processed["started_at"]
        assert attrs[CallAttributes.CREATED_AT] == processed["created_at"]
        # Talk-ratio breakdown parity.
        tr = processed["talk_ratio"]
        assert attrs[CallAttributes.TALK_SECONDS_USER] == tr["user"]
        assert attrs[CallAttributes.TALK_SECONDS_BOT] == tr["bot"]
        assert attrs[CallAttributes.TALK_PCT_USER] == tr["user_pct"]
        assert attrs[CallAttributes.TALK_PCT_BOT] == tr["bot_pct"]


class TestRetellDisplayFieldsParity:
    """retell.py normalizer display fields must match _process_retell_logs."""

    @staticmethod
    def _sample():
        return {
            "call_id": "call_1",
            "agent_id": "agent_9",
            "agent_name": "Healthcare Check-In",
            "call_status": "ended",
            "direction": "inbound",
            "to_number": "+12345162722",
            "from_number": "+18568806998",
            "start_timestamp": 1763541469420,
            "end_timestamp": 1763541606176,
            "recording_url": "https://x/recording.wav",
            "recording_multi_channel_url": "https://x/multi.wav",
            "call_cost": {
                "combined_cost": 29.68,
                "product_costs": [{"product": "tts", "unit_price": 0.1, "cost": 1.0}],
            },
            "call_analysis": {"call_summary": "Cindy confirmed the appointment."},
            "disconnection_reason": "user_hangup",
            "transcript_with_tool_calls": [
                {"role": "agent", "content": "Hi", "words": [{"start": 2.0, "end": 5.0}]},
                {"role": "user", "content": "Hello", "words": [{"start": 6.0, "end": 8.0}]},
            ],
        }

    def test_display_fields_match_process_raw_logs(self):
        from tracer.utils import retell as R
        from tracer.utils.otel import CallAttributes

        attrs: dict = {}
        s = self._sample()
        R._process_transcript(s, attrs)
        R._extract_recording_urls(s, attrs)
        R._extract_metadata(s, attrs)
        R._extract_common_call_fields(s, attrs)
        processed = ObservabilityService.process_raw_logs(
            self._sample(), ProviderChoices.RETELL
        )

        assert attrs[CallAttributes.CUSTOMER_NAME] == processed["customer_name"]
        assert attrs[CallAttributes.STATUS_DISPLAY] == processed["status"]
        assert attrs[CallAttributes.CALL_TYPE] == processed["call_type"]
        assert attrs[CallAttributes.SUMMARY] == processed["call_summary"]
        assert attrs[CallAttributes.ASSISTANT_ID] == processed["assistant_id"]
        assert (
            attrs[CallAttributes.ASSISTANT_PHONE_NUMBER]
            == processed["assistant_phone_number"]
        )
        assert attrs[CallAttributes.COST_CENTS] == processed["cost_cents"]
        assert attrs[CallAttributes.MESSAGE_COUNT] == processed["message_count"]
        assert (
            attrs[CallAttributes.TRANSCRIPT_AVAILABLE]
            == processed["transcript_available"]
        )
        assert attrs[CallAttributes.STARTED_AT] == processed["started_at"]
        assert attrs[CallAttributes.ENDED_AT] == processed["ended_at"]
        assert attrs[CallAttributes.RECORDING_AVAILABLE] == processed[
            "recording_available"
        ]
        tr = processed["talk_ratio"]
        assert attrs[CallAttributes.TALK_SECONDS_USER] == tr["user"]
        assert attrs[CallAttributes.TALK_SECONDS_BOT] == tr["bot"]


class TestSparseProviderDisplayFields:
    """eleven_labs / bland / twilio normalizers materialize the list-display
    fields (these providers set fewer fields than vapi/retell)."""

    def test_eleven_labs(self):
        from tracer.utils.eleven_labs import normalize_eleven_labs_data
        from tracer.utils.otel import CallAttributes

        attrs = normalize_eleven_labs_data(
            {
                "conversation_id": "conv_1",
                "agent_id": "agent_x",
                "status": "done",
                "metadata": {"start_time_unix_secs": 1763541469, "cost": 42},
                "transcript": [
                    {"role": "agent", "message": "Hi"},
                    {"role": "user", "message": "Hello"},
                ],
            }
        )["span_attributes"]
        assert attrs[CallAttributes.STATUS_DISPLAY] == "completed"
        assert attrs[CallAttributes.COST_CENTS] == 42
        assert attrs[CallAttributes.ASSISTANT_ID] == "agent_x"
        assert attrs[CallAttributes.MESSAGE_COUNT] == 2
        assert attrs[CallAttributes.TRANSCRIPT_AVAILABLE] is True
        assert attrs[CallAttributes.RECORDING_AVAILABLE] is False
        assert attrs[CallAttributes.STARTED_AT]

    def test_bland(self):
        from tracer.utils.bland import normalize_bland_data
        from tracer.utils.otel import CallAttributes

        attrs = normalize_bland_data(
            {
                "call_id": "c1",
                "to": "+1",
                "status": "completed",
                "started_at": "2026-07-20T20:00:00Z",
                "created_at": "2026-07-20T19:59:00Z",
                "call_length": 2.5,
                "price": 0.1,
                "recording_url": "https://x/rec.wav",
                "summary": "Done",
                "transcripts": [
                    {"user": "assistant", "text": "Hi"},
                    {"user": "user", "text": "Hey"},
                ],
            }
        )["span_attributes"]
        assert attrs[CallAttributes.STATUS_DISPLAY] == "completed"
        assert attrs[CallAttributes.COST_CENTS] == 0.1 * 100
        assert attrs[CallAttributes.SUMMARY] == "Done"
        assert attrs[CallAttributes.RECORDING_AVAILABLE] is True
        assert attrs[CallAttributes.MESSAGE_COUNT] == 2
        assert attrs[CallAttributes.TRANSCRIPT_AVAILABLE] is True

    def test_twilio(self):
        from tracer.utils.otel import CallAttributes
        from tracer.utils.twilio_calls import normalize_twilio_data

        attrs = normalize_twilio_data(
            {
                "sid": "CA1",
                "to": "+1",
                "status": "completed",
                "direction": "inbound",
                "start_time": "Tue, 20 Jul 2026 20:00:00 +0000",
                "duration": "137",
                "price": "-0.0085",
            }
        )["span_attributes"]
        assert attrs[CallAttributes.STATUS_DISPLAY] == "completed"
        assert attrs[CallAttributes.CALL_TYPE] == "inbound"
        # Twilio reports price as a NEGATIVE dollar charge (e.g. "-0.0085");
        # cost_cents is the magnitude converted to cents (abs × 100 = 0.85),
        # mirroring _process_twilio_raw. Compared via the formula to dodge
        # float-representation error.
        assert attrs[CallAttributes.COST_CENTS] == abs(-0.0085) * 100
        assert attrs[CallAttributes.RECORDING_AVAILABLE] is False
        assert attrs[CallAttributes.MESSAGE_COUNT] == 0
        assert attrs[CallAttributes.TRANSCRIPT_AVAILABLE] is False
        assert attrs[CallAttributes.STARTED_AT]


class TestNormalizerDisplayRobustness:
    """The display-field additions must never raise when raw_log fields are
    missing, None, or the wrong type (provider API shape drift)."""

    def test_vapi_helpers_never_raise(self):
        from tracer.utils import vapi as V
        from tracer.utils.otel import CallAttributes

        # Wrong types on the fields our additions read; existing-code fields kept
        # well-formed (dicts/lists) so we isolate our additions.
        bad = {
            "customer": "not-a-dict",
            "messages": [None, "x", {"role": "user", "duration": "abc"}],
            "artifact": "not-a-dict",
            "variableValues": "not-a-dict",
            "cost": "not-a-number",
            "phoneNumber": "not-a-dict",
            "type": 123,
            "status": None,
        }
        attrs: dict = {}
        V._extract_common_call_fields(bad, attrs)
        V._extract_metadata(bad, attrs)
        V._extract_conversation(bad, attrs)
        V._extract_recording_urls(bad, attrs)
        assert CallAttributes.CUSTOMER_NAME not in attrs  # {} has no number
        assert CallAttributes.COST_CENTS not in attrs  # str cost skipped
        assert CallAttributes.ASSISTANT_PHONE_NUMBER not in attrs  # str vv skipped

    def test_retell_helpers_never_raise(self):
        from tracer.utils import retell as R
        from tracer.utils.otel import CallAttributes

        bads = [
            {  # call_cost / call_analysis as non-dict; str timestamps; junk rows
                "call_cost": "not-a-dict",
                "call_analysis": "not-a-dict",
                "start_timestamp": "not-a-number",
                "end_timestamp": None,
                "transcript_with_tool_calls": [None, "x", {"role": "user"}],
                "direction": None,
                "call_status": None,
            },
            {},  # everything absent — the del/pop path must be safe
            {  # call_cost dict WITHOUT product_costs; string word timings
                "call_cost": {"combined_cost": 1},
                "transcript_with_tool_calls": [
                    {"role": "user", "words": [{"start": "0", "end": "5"}]}
                ],
            },
        ]
        for bad in bads:
            attrs: dict = {}
            R._extract_common_call_fields(bad, attrs)
            R._extract_metadata(bad, attrs)
            R._process_transcript(bad, attrs)
            R._extract_recording_urls(bad, attrs)
            # Non-dict call_analysis / str timestamp → those keys must be skipped.
            assert CallAttributes.STARTED_AT not in attrs or isinstance(
                attrs.get(CallAttributes.STARTED_AT), str
            )

    def test_sparse_normalizers_never_raise(self):
        from tracer.utils.bland import normalize_bland_data
        from tracer.utils.eleven_labs import normalize_eleven_labs_data
        from tracer.utils.twilio_calls import normalize_twilio_data

        for fn in (
            normalize_eleven_labs_data,
            normalize_bland_data,
            normalize_twilio_data,
        ):
            fn({})
            fn({"status": None, "price": "abc", "metadata": None, "direction": None})
            # metadata as a non-dict must not crash the get-chains
            fn({"metadata": "oops", "status": 123, "price": {}})
            fn({"metadata": {"charging": "oops"}})
