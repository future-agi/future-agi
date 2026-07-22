"""Pure-function data-shape coverage for the voice-provider consumers.

The CH 25.3 typed-JSON array-leaf stringification bug (see DECISIONS #028)
surfaced because `_process_vapi_logs` and `_process_retell_logs` reach
deep into nested arrays for arithmetic — duration/1000, talk-ratio sums,
timedelta(seconds=...), etc. Schema 013 fixes the storage. These tests
fix the *contract*: regardless of which numeric type the JSON parse yields
(int, float, or — in legacy reads — str), the consumer must either compute
the correct number or fail loudly with a typed error, not silently corrupt.

We test the consumers directly (no Django, no CH) by feeding them the
exact `raw_log` dict shape they would receive after the read path
deserializes `attributes_extra`. Each test asserts:
  1. no `TypeError: unsupported operand type(s) for ...`
  2. arithmetic outputs match a hand-computed expected value
  3. structural fields (transcript ordering, message_count, etc.) match
"""
from __future__ import annotations

import datetime as dt
from datetime import timezone

import pytest

# Acceptable failure modes for the TypeFragility contract: any of these
# means the consumer crashed LOUDLY on a str-typed numeric (not silent
# corruption). pydantic.ValidationError covers the post-arithmetic path
# (e.g. `cost * 100` produces a repeated string that pydantic refuses).
try:
    from pydantic import ValidationError as _PydanticValidationError
except ImportError:  # pragma: no cover
    _PydanticValidationError = ValueError  # fallback so the tuple is well-formed

_LOUD_FAILURE_EXCEPTIONS = (TypeError, ValueError, _PydanticValidationError)


pytestmark = pytest.mark.unit


def _import_provider_module():
    """Import lazily — the module has heavy module-level Django imports
    (model_hub, secret access). Import once per test session via the
    function-scoped fixture; pytest collection cost is negligible.
    """
    from tracer.services import observability_providers
    return observability_providers


# ─── Vapi fixtures ───────────────────────────────────────────────────────────

def _vapi_basic_call(**overrides):
    """The shape `_process_vapi_logs` expects, drawn from real production
    payloads (see fixtures in shape_audit catalogue and an actual Vapi
    webhook capture). Mirror the keys the consumer reads. Numbers are
    INTs at the leaves of `messages` (the crashing shape) and FLOATs
    at `secondsFromStart` (also crashing under str-coercion).
    """
    base = {
        "id": "call_test_123",
        "type": "outboundPhoneCall",
        "status": "ended",
        "startedAt": "2026-05-25T10:00:00+00:00",
        "endedAt": "2026-05-25T10:01:30+00:00",
        "createdAt": "2026-05-25T09:59:55+00:00",
        "summary": "Test call",
        "endedReason": "customer-ended-call",
        "recordingUrl": "https://example.com/rec.wav",
        "cost": 0.0234,
        "assistantId": "asst_x1",
        "phoneNumber": {"number": "+15555550100"},
        "customer": {"number": "+15555550200"},
        "callMetadata": {"source": "campaign-A"},
        "artifact": {"stereoRecordingUrl": "https://example.com/rec_stereo.wav"},
        "costBreakdown": {
            "stt": 0.0050,
            "llm": 0.0125,
            "tts": 0.0040,
            "vapi": 0.0010,
            "transport": 0.0009,
            "total": 0.0234,
        },
        "messages": [
            # First message is intentionally NOT user/bot — gets skipped in transcripts.
            {
                "role": "system",
                "message": "You are a helpful assistant.",
                "time": 0,
                "secondsFromStart": 0.0,
                "duration": 0,
            },
            {
                "role": "user",
                "message": "Hello, can you help me?",
                "time": 1773816575961,   # ms epoch — large int that previously stringified
                "secondsFromStart": 1.5,  # float
                "duration": 932,          # int(ms) — the canonical crashing value
            },
            {
                "role": "bot",
                "message": "Of course! What can I do for you?",
                "time": 1773816577893,
                "secondsFromStart": 3.443,
                "duration": 1810,
            },
            {
                "role": "user",
                "message": "Book me a flight.",
                "time": 1773816580500,
                "secondsFromStart": 6.050,
                "duration": 1200,
            },
        ],
    }
    base.update(overrides)
    return base


# ─── Vapi consumer tests ─────────────────────────────────────────────────────

class TestProcessVapiLogs:
    """The function that crashed in production with `str / int` —
    these tests pin every code path that does arithmetic on a nested-array leaf.
    """

    def test_normal_call_no_type_errors(self):
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_vapi_logs(_vapi_basic_call())
        assert result["call_id"] == "call_test_123"
        assert result["status"] == "completed"
        assert result["duration_seconds"] == 90    # 10:01:30 - 10:00:00
        assert result["cost_cents"] == pytest.approx(2.34)

    def test_transcript_excludes_first_message_and_skips_system_role(self):
        """`_process_vapi_logs` deliberately skips index 0 and only emits
        user/bot rows. Regression: this contract held under PG (raw dict),
        must hold under the new CH path too."""
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_vapi_logs(_vapi_basic_call())
        # 4 messages in fixture; first skipped (i==0 guard); system role skipped anyway.
        # Remaining: user, bot, user — 3 transcripts.
        transcripts = result["transcript"]
        assert len(transcripts) == 3
        assert [t["role"] for t in transcripts] == ["user", "bot", "user"]
        # Each transcript duration was computed from `duration / 1000` then round(_,2).
        # First user msg: 932/1000 = 0.932 → 0.93
        assert transcripts[0]["duration"] == pytest.approx(0.93)
        assert transcripts[1]["duration"] == pytest.approx(1.81)
        assert transcripts[2]["duration"] == pytest.approx(1.2)

    def test_talk_ratio_computed_with_correct_arithmetic(self):
        """The line `user_talk_seconds += dur` is the regression site —
        if `dur` came back as a string from CH, this would crash. After
        schema 013 + this test, both code paths are pinned."""
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_vapi_logs(_vapi_basic_call())
        # User: 932 + 1200 = 2132 ms = 2.132 s
        # Bot:  1810 ms = 1.81 s
        ratio = result["talk_ratio"]
        assert ratio is not None
        # round(2.132, 1) == 2.1; round(1.81, 1) == 1.8
        assert ratio["user"] == pytest.approx(2.1)
        assert ratio["bot"] == pytest.approx(1.8)
        # Percentages must sum within rounding to 100
        assert ratio["user_pct"] + ratio["bot_pct"] in (99, 100, 101)

    def test_empty_messages_returns_no_transcript_no_ratio(self):
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_vapi_logs(
            _vapi_basic_call(messages=[])
        )
        assert result["transcript"] == []
        assert result["transcript_available"] is False
        assert result["talk_ratio"] is None
        assert result["message_count"] == 0

    def test_missing_cost_breakdown_returns_none(self):
        mod = _import_provider_module()
        log = _vapi_basic_call()
        del log["costBreakdown"]
        result = mod.ObservabilityService._process_vapi_logs(log)
        assert result["cost_breakdown"] is None

    def test_zero_duration_messages_dont_crash_div_by_zero(self):
        """Boundary: total_talk == 0 → talk_ratio is None (avoid div/0)."""
        mod = _import_provider_module()
        log = _vapi_basic_call(messages=[
            {"role": "user", "message": "Hi",
             "time": 1, "secondsFromStart": 0.0, "duration": 0},
            {"role": "bot", "message": "Hi",
             "time": 2, "secondsFromStart": 1.0, "duration": 0},
        ])
        result = mod.ObservabilityService._process_vapi_logs(log)
        assert result["talk_ratio"] is None  # total_talk == 0 → None

    def test_inbound_call_type_classified_correctly(self):
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_vapi_logs(
            _vapi_basic_call(type="inboundPhoneCall")
        )
        assert result["call_type"] == "inbound"

    def test_started_at_missing_falls_back_to_created_at(self):
        """Real-data variant: queued/scheduled calls lack startedAt; consumer
        must use createdAt as effective_start to still compute duration."""
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_vapi_logs(
            _vapi_basic_call(startedAt=None)
        )
        # endedAt - createdAt = 10:01:30 - 09:59:55 = 95 sec
        assert result["duration_seconds"] == 95


# ─── Retell fixtures ─────────────────────────────────────────────────────────

def _retell_basic_call(**overrides):
    """Retell shape — the consumer reads `transcript_with_tool_calls[i].words[j].{start,end}`
    where the leaves are float seconds. This was the second-most-common
    crashing shape in the audit (D-028)."""
    base = {
        "call_id": "ret_call_456",
        "direction": "outbound",
        "agent_id": "agent_xyz",
        "call_status": "ended",
        # ms-epoch ints
        "start_timestamp": 1773816575000,
        "end_timestamp":   1773816665000,
        "to_number": "+15555550100",
        "from_number": "+15555550000",
        "recording_url": "https://retell/rec.wav",
        "recording_multi_channel_url": "https://retell/stereo.wav",
        "disconnection_reason": "user_hangup",
        "agent_name": "Sales Agent",
        "metadata": {"campaign": "Q2"},
        "call_analysis": {"call_summary": "Successful booking"},
        "call_cost": {
            "stt_cost": 1.5,    # cents
            "llm_cost": 3.0,
            "tts_cost": 1.2,
            "combined_cost": 5.7,
        },
        "transcript_with_tool_calls": [
            {
                "role": "agent",
                "content": "Hello, this is Sales Agent.",
                "words": [
                    {"word": "Hello,",  "start": 0.5,  "end": 0.8},
                    {"word": "this",    "start": 0.85, "end": 1.0},
                    {"word": "is",      "start": 1.05, "end": 1.15},
                    {"word": "Sales",   "start": 1.20, "end": 1.50},
                    {"word": "Agent.",  "start": 1.55, "end": 1.95},
                ],
                "metadata": {"channel": 0},
            },
            {
                "role": "user",
                "content": "Hi.",
                "words": [
                    {"word": "Hi.", "start": 2.10, "end": 2.40},
                ],
                "metadata": {"channel": 1},
            },
            # Tool-call message, role neither user nor agent — must NOT be in transcripts.
            {
                "role": "tool_call_invocation",
                "content": None,
                "words": [],
                "metadata": {"tool_call_id": "tc_1"},
            },
        ],
    }
    base.update(overrides)
    return base


# ─── Retell consumer tests ───────────────────────────────────────────────────

class TestProcessRetellLogs:
    def test_normal_call_no_type_errors(self):
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_retell_logs(_retell_basic_call())
        assert result["call_id"] == "ret_call_456"
        assert result["status"] == "completed"
        assert result["duration_seconds"] == 90  # (end - start)/1000
        assert result["cost_cents"] == pytest.approx(5.7)

    def test_messages_use_word_start_end_for_timing(self):
        """The crash site: words[0].start and words[-1].end are pulled
        into `timedelta(seconds=...)`. Must remain numeric."""
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_retell_logs(_retell_basic_call())
        msgs = result["messages"]
        # 3 input transcripts → 3 output messages (tool-call kept in messages but
        # NOT in processed_transcripts; see test below).
        assert len(msgs) == 3
        agent_msg, user_msg, tool_msg = msgs
        # Agent first word .start=0.5, last word .end=1.95 → duration=1.45 (round to 2)
        assert agent_msg["seconds_from_start"] == pytest.approx(0.5)
        assert agent_msg["end_time"] == pytest.approx(1.95)
        assert agent_msg["duration"] == pytest.approx(1.45)
        # User single word: start=2.10, end=2.40 → duration=0.30
        assert user_msg["duration"] == pytest.approx(0.30)
        # Tool call has no words → all timing None
        assert tool_msg["duration"] is None
        assert tool_msg["seconds_from_start"] is None
        assert tool_msg["end_time"] is None

    def test_transcripts_exclude_tool_calls(self):
        """Only role in {user, agent} make it into `transcript`."""
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_retell_logs(_retell_basic_call())
        transcripts = result["transcript"]
        assert len(transcripts) == 2
        assert [t["role"] for t in transcripts] == ["agent", "user"]

    def test_talk_ratio_computed_with_correct_arithmetic(self):
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_retell_logs(_retell_basic_call())
        ratio = result["talk_ratio"]
        assert ratio is not None
        # agent (=bot bucket): 1.45 → round(_, 1)=1.4 OR 1.5 (round to even)
        # user: 0.30 → round(_, 1)=0.3
        assert ratio["bot"] in (1.4, 1.5)
        assert ratio["user"] == pytest.approx(0.3)
        assert ratio["user_pct"] + ratio["bot_pct"] in (99, 100, 101)

    def test_empty_transcripts_returns_no_messages_no_ratio(self):
        mod = _import_provider_module()
        result = mod.ObservabilityService._process_retell_logs(
            _retell_basic_call(transcript_with_tool_calls=[])
        )
        assert result["messages"] == []
        assert result["transcript_available"] is False
        assert result["talk_ratio"] is None
        assert result["message_count"] == 0

    def test_missing_call_cost_returns_none(self):
        mod = _import_provider_module()
        log = _retell_basic_call()
        del log["call_cost"]
        result = mod.ObservabilityService._process_retell_logs(log)
        assert result["cost_breakdown"] is None
        assert result["cost_cents"] is None

    def test_missing_timestamps_returns_no_duration(self):
        """Real-world shape: a Retell call without start_timestamp is one
        that never connected (cancelled / queue-timeout). Such calls also
        lack transcripts. Test the realistic combination — the consumer
        formats time fields by `started_at + word_offset`, so transcripts
        without started_at is a synthetic case the API never sees."""
        mod = _import_provider_module()
        log = _retell_basic_call(
            start_timestamp=None,
            end_timestamp=None,
            transcript_with_tool_calls=[],
        )
        result = mod.ObservabilityService._process_retell_logs(log)
        assert result["duration_seconds"] is None
        assert result["started_at"] is None
        assert result["ended_at"] is None
        assert result["messages"] == []
        assert result["transcript_available"] is False

    def test_retell_with_transcript_but_missing_started_at_gracefully_degrades(self):
        mod = _import_provider_module()
        log = _retell_basic_call(start_timestamp=None, end_timestamp=None)
        result = mod.ObservabilityService._process_retell_logs(log)
        assert result["started_at"] is None
        assert result["messages"], "transcripts should still be extracted"


# ─── Cross-provider type-fragility regression ────────────────────────────────

class TestTypeFragilityRegression:
    """If anyone reintroduces the typed-JSON array-leaf stringification
    (or otherwise hands the consumers str-typed numerics), these tests
    expose the failure as a clean TypeError — not a silent NaN or corrupt
    aggregate. This pins the contract on the consumer side, complementing
    schema 013 (which pins it on the storage side).

    Coverage rationale (per codex P2 finding 2026-05-25):
    Every numeric leaf that the consumer reaches via arithmetic, indexing,
    or `timedelta(seconds=...)` is a regression surface. We parametrize over
    every such leaf so adding a new numeric field to the fixture forces a
    matching fragility test, not just one canonical "the original bug" case.
    """

    # Format: (description, mutator_callable_taking_log)
    # Each mutator turns ONE numeric leaf into a string and returns the
    # mutated log. Doing it via callable rather than a JSON path keeps
    # the test readable and avoids a homemade JSONPointer.
    _VAPI_STR_LEAF_MUTATORS = [
        # `duration` → divided by 1000; the original incident.
        ("messages[1].duration", lambda lg: (lg["messages"][1].__setitem__("duration", "932"), lg)[1]),
        # `secondsFromStart` → used in `timedelta(seconds=start_time + duration)`.
        ("messages[1].secondsFromStart", lambda lg: (lg["messages"][1].__setitem__("secondsFromStart", "1.5"), lg)[1]),
        # `time` (ms-epoch) → passed to `datetime.fromtimestamp(time / 1000)`.
        ("messages[1].time", lambda lg: (lg["messages"][1].__setitem__("time", "1773816575961"), lg)[1]),
        # Top-level `cost` → consumer does `cost * 100 if cost else None`.
        # Not a nested-array leaf (so the CH typed-JSON bug doesn't affect it
        # today), but the class doc claims "every numeric leaf the consumer
        # touches" — so we cover it (codex P3 finding 2026-05-26).
        ("cost (top-level)", lambda lg: (lg.__setitem__("cost", "0.0234"), lg)[1]),
    ]

    @pytest.mark.parametrize("leaf_name,mutate", _VAPI_STR_LEAF_MUTATORS,
                             ids=[name for name, _ in _VAPI_STR_LEAF_MUTATORS])
    def test_vapi_numeric_leaf_as_string_raises_typeerror(self, leaf_name, mutate):
        mod = _import_provider_module()
        log = mutate(_vapi_basic_call())
        with pytest.raises(_LOUD_FAILURE_EXCEPTIONS):
            mod.ObservabilityService._process_vapi_logs(log)

    _RETELL_STR_LEAF_MUTATORS = [
        # `words[0].start` → passed to `timedelta(seconds=seconds_from_start)`.
        ("words[0].start",
         lambda lg: (lg["transcript_with_tool_calls"][0]["words"][0].__setitem__("start", "0.5"), lg)[1]),
        # `words[-1].end` → passed to `timedelta(seconds=end_time)` AND
        # `round(end_time, 2)` in the message dict. Distinct codepath from
        # the start leaf; needs its own assertion.
        ("words[-1].end",
         lambda lg: (lg["transcript_with_tool_calls"][0]["words"][-1].__setitem__("end", "1.95"), lg)[1]),
        # `start_timestamp` → divided by 1000 at the top of the function.
        ("start_timestamp",
         lambda lg: (lg.__setitem__("start_timestamp", "1773816575000"), lg)[1]),
        # `end_timestamp` → divided by 1000, then `int(ended_at - started_at)`.
        # If only start was mutated this would never crash on end-side arithmetic.
        ("end_timestamp",
         lambda lg: (lg.__setitem__("end_timestamp", "1773816665000"), lg)[1]),
    ]

    @pytest.mark.parametrize("leaf_name,mutate", _RETELL_STR_LEAF_MUTATORS,
                             ids=[name for name, _ in _RETELL_STR_LEAF_MUTATORS])
    def test_retell_numeric_leaf_as_string_raises_typeerror(self, leaf_name, mutate):
        mod = _import_provider_module()
        log = mutate(_retell_basic_call())
        with pytest.raises(_LOUD_FAILURE_EXCEPTIONS):
            mod.ObservabilityService._process_retell_logs(log)
