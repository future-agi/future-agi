"""Unit tests for LangfuseTransformer."""

import json
import uuid

import pytest

from integrations.transformers.langfuse_transformer import (
    LangfuseTransformer,
    _guess_provider,
)


@pytest.fixture
def transformer():
    return LangfuseTransformer()


PROJECT_ID = str(uuid.uuid4())
TRACE_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# transform_trace
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransformTrace:
    def test_basic_mapping(self, transformer, raw_langfuse_trace):
        result = transformer.transform_trace(raw_langfuse_trace, PROJECT_ID)

        assert result["external_id"] == "lf-trace-001"
        assert result["project_id"] == PROJECT_ID
        assert result["name"] == "my-chat-chain"
        assert result["user_id"] == "user-42"
        assert result["session_id"] == "sess-abc"
        assert result["input"] == {"prompt": "Hello"}
        assert result["output"] == {"response": "Hi there"}

    def test_adds_langfuse_tag(self, transformer, raw_langfuse_trace):
        result = transformer.transform_trace(raw_langfuse_trace, PROJECT_ID)
        assert "langfuse" in result["tags"]

    def test_does_not_duplicate_langfuse_tag(self, transformer):
        trace = {"id": "t1", "tags": ["langfuse", "other"]}
        result = transformer.transform_trace(trace, PROJECT_ID)
        assert result["tags"].count("langfuse") == 1

    def test_metadata_includes_integration_source(
        self, transformer, raw_langfuse_trace
    ):
        result = transformer.transform_trace(raw_langfuse_trace, PROJECT_ID)
        assert result["metadata"]["integration_source"] == "langfuse"

    def test_non_dict_metadata_wrapped(self, transformer):
        trace = {"id": "t1", "metadata": "just-a-string"}
        result = transformer.transform_trace(trace, PROJECT_ID)
        assert result["metadata"]["integration_source"] == "langfuse"
        assert result["metadata"]["original_metadata"] == "just-a-string"

    def test_empty_name_falls_back_to_root_observation(
        self, transformer, raw_langfuse_trace_no_name
    ):
        result = transformer.transform_trace(raw_langfuse_trace_no_name, PROJECT_ID)
        assert result["name"] == "root-gen"

    def test_empty_name_falls_back_to_earliest_observation(self, transformer):
        trace = {
            "id": "t1",
            "name": "",
            "observations": [
                {
                    "id": "o1",
                    "parentObservationId": "parent-outside",
                    "name": "second",
                    "startTime": "2024-01-15T10:00:01Z",
                },
                {
                    "id": "o2",
                    "parentObservationId": "parent-outside",
                    "name": "first",
                    "startTime": "2024-01-15T10:00:00Z",
                },
            ],
        }
        result = transformer.transform_trace(trace, PROJECT_ID)
        # Both have parents not in the set, so root fallback fails; uses earliest
        assert result["name"] == "first"

    def test_empty_observations_uses_empty_name(self, transformer):
        trace = {"id": "t1", "name": "", "observations": []}
        result = transformer.transform_trace(trace, PROJECT_ID)
        assert result["name"] == ""

    def test_missing_id_raises(self, transformer):
        with pytest.raises(ValueError, match="id"):
            transformer.transform_trace({"name": "no-id"}, PROJECT_ID)

    def test_none_tags_handled(self, transformer):
        """tags=None should not crash; 'langfuse' added."""
        trace = {"id": "t1", "tags": None}
        result = transformer.transform_trace(trace, PROJECT_ID)
        assert result["tags"] == ["langfuse"]

    def test_metadata_merge_preserves_existing_keys(self, transformer):
        """Existing dict metadata keys are preserved alongside integration_source."""
        trace = {"id": "t1", "metadata": {"env": "production", "version": "2.0"}}
        result = transformer.transform_trace(trace, PROJECT_ID)
        assert result["metadata"]["env"] == "production"
        assert result["metadata"]["version"] == "2.0"
        assert result["metadata"]["integration_source"] == "langfuse"


# ---------------------------------------------------------------------------
# transform_observations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransformObservations:
    def test_generation_type(self, transformer, raw_langfuse_trace):
        obs_list = transformer.transform_observations(
            raw_langfuse_trace, TRACE_ID, PROJECT_ID
        )
        gen = next(o for o in obs_list if o["id"] == "obs-gen-001")
        assert gen["observation_type"] == "llm"

    def test_span_type(self, transformer, raw_langfuse_trace):
        obs_list = transformer.transform_observations(
            raw_langfuse_trace, TRACE_ID, PROJECT_ID
        )
        span = next(o for o in obs_list if o["id"] == "obs-span-001")
        assert span["observation_type"] == "chain"

    def test_event_type(self, transformer, raw_langfuse_trace):
        obs_list = transformer.transform_observations(
            raw_langfuse_trace, TRACE_ID, PROJECT_ID
        )
        event = next(o for o in obs_list if o["id"] == "obs-event-001")
        assert event["observation_type"] == "unknown"

    def test_missing_id_skipped(self, transformer):
        trace = {"observations": [{"type": "SPAN", "name": "no-id"}]}
        result = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert len(result) == 0

    def test_token_counts_input_output(self, transformer, raw_langfuse_trace):
        obs_list = transformer.transform_observations(
            raw_langfuse_trace, TRACE_ID, PROJECT_ID
        )
        gen = next(o for o in obs_list if o["id"] == "obs-gen-001")
        assert gen["prompt_tokens"] == 15
        assert gen["completion_tokens"] == 5
        assert gen["total_tokens"] == 20

    def test_token_counts_legacy_keys(self, transformer, raw_langfuse_trace_no_name):
        obs_list = transformer.transform_observations(
            raw_langfuse_trace_no_name, TRACE_ID, PROJECT_ID
        )
        gen = obs_list[0]
        assert gen["prompt_tokens"] == 10
        assert gen["completion_tokens"] == 5
        assert gen["total_tokens"] == 15

    def test_token_counts_default_zero(self, transformer):
        trace = {
            "observations": [{"id": "o1", "type": "GENERATION", "usageDetails": {}}]
        }
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list[0]["prompt_tokens"] == 0
        assert obs_list[0]["completion_tokens"] == 0
        assert obs_list[0]["total_tokens"] == 0

    def test_total_tokens_computed_when_missing(self, transformer):
        trace = {
            "observations": [
                {
                    "id": "o1",
                    "type": "GENERATION",
                    "usageDetails": {"input": 10, "output": 5},
                }
            ]
        }
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list[0]["total_tokens"] == 15

    def test_latency_converted_to_ms(self, transformer, raw_langfuse_trace):
        obs_list = transformer.transform_observations(
            raw_langfuse_trace, TRACE_ID, PROJECT_ID
        )
        gen = next(o for o in obs_list if o["id"] == "obs-gen-001")
        assert gen["latency_ms"] == 1500

    def test_cost_from_calculated_total(self, transformer, raw_langfuse_trace):
        obs_list = transformer.transform_observations(
            raw_langfuse_trace, TRACE_ID, PROJECT_ID
        )
        gen = next(o for o in obs_list if o["id"] == "obs-gen-001")
        assert gen["cost"] == 0.002

    def test_cost_from_cost_details_fallback(self, transformer):
        trace = {
            "observations": [
                {
                    "id": "o1",
                    "type": "GENERATION",
                    "calculatedTotalCost": None,
                    "costDetails": {"total": 0.005},
                }
            ]
        }
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list[0]["cost"] == 0.005

    def test_cost_explicit_zero_no_fallback(self, transformer):
        """calculatedTotalCost=0 is kept as 0, not falling through to costDetails."""
        trace = {
            "observations": [
                {
                    "id": "o1",
                    "type": "GENERATION",
                    "calculatedTotalCost": 0,
                    "costDetails": {"total": 9.99},
                }
            ]
        }
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        # calculatedTotalCost=0 is not None, so costDetails is NOT consulted.
        # But `cost = cost or 0` turns 0 into 0 (int 0 is falsy → stays 0).
        assert obs_list[0]["cost"] == 0

    def test_parent_span_id(self, transformer, raw_langfuse_trace):
        obs_list = transformer.transform_observations(
            raw_langfuse_trace, TRACE_ID, PROJECT_ID
        )
        span = next(o for o in obs_list if o["id"] == "obs-span-001")
        assert span["parent_span_id"] == "obs-gen-001"

    def test_status_error(self, transformer):
        trace = {"observations": [{"id": "o1", "type": "SPAN", "level": "ERROR"}]}
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list[0]["status"] == "ERROR"

    def test_status_default(self, transformer, raw_langfuse_trace):
        obs_list = transformer.transform_observations(
            raw_langfuse_trace, TRACE_ID, PROJECT_ID
        )
        gen = next(o for o in obs_list if o["id"] == "obs-gen-001")
        assert gen["status"] == "OK"

    def test_status_none(self, transformer):
        trace = {"observations": [{"id": "o1", "type": "SPAN"}]}
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        # Observations without an explicit level default to OK
        assert obs_list[0]["status"] == "OK"

    def test_unknown_observation_type(self, transformer):
        """Observation with unrecognized type maps to 'unknown'."""
        trace = {"observations": [{"id": "o1", "type": "CUSTOM_TYPE"}]}
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list[0]["observation_type"] == "unknown"

    def test_none_type_maps_to_unknown(self, transformer):
        """Observation with no type field maps to 'unknown'."""
        trace = {"observations": [{"id": "o1"}]}
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list[0]["observation_type"] == "unknown"

    def test_negative_latency_passes_through(self, transformer):
        """Negative latency from API is converted (not clamped to zero)."""
        trace = {"observations": [{"id": "o1", "type": "SPAN", "latency": -1.5}]}
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        # latency_seconds = -1.5 is truthy, so int(-1.5 * 1000) = -1500
        assert obs_list[0]["latency_ms"] == -1500

    def test_zero_latency(self, transformer):
        """Latency of exactly 0 → 0 ms."""
        trace = {"observations": [{"id": "o1", "type": "SPAN", "latency": 0}]}
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list[0]["latency_ms"] == 0

    def test_none_cost_no_cost_details(self, transformer):
        """Observation with None calculatedTotalCost and no costDetails → cost = 0."""
        trace = {
            "observations": [
                {"id": "o1", "type": "GENERATION", "calculatedTotalCost": None}
            ]
        }
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list[0]["cost"] == 0

    def test_no_usage_details_key(self, transformer):
        """Observation without usageDetails key at all → 0 tokens."""
        trace = {"observations": [{"id": "o1", "type": "GENERATION"}]}
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list[0]["prompt_tokens"] == 0
        assert obs_list[0]["completion_tokens"] == 0
        assert obs_list[0]["total_tokens"] == 0

    def test_empty_observations_list(self, transformer):
        """No observations → empty list."""
        trace = {"observations": []}
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list == []

    def test_no_observations_key(self, transformer):
        """Missing observations key → empty list."""
        trace = {}
        obs_list = transformer.transform_observations(trace, TRACE_ID, PROJECT_ID)
        assert obs_list == []


# ---------------------------------------------------------------------------
# transform_scores
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTransformScores:
    def test_numeric_score(self, transformer, raw_langfuse_trace):
        scores = transformer.transform_scores(raw_langfuse_trace, TRACE_ID)
        s = next(s for s in scores if s["eval_type_id"] == "helpfulness")
        assert s["output_float"] == 0.9
        assert s["observation_id"] == "obs-gen-001"
        assert s["eval_explanation"] == "Very helpful response"
        assert s["output_bool"] is None

    def test_boolean_score(self, transformer, raw_langfuse_trace):
        scores = transformer.transform_scores(raw_langfuse_trace, TRACE_ID)
        s = next(s for s in scores if s["eval_type_id"] == "is_appropriate")
        assert s["output_bool"] is True
        assert s["observation_id"] is None

    def test_empty_scores(self, transformer, raw_langfuse_trace_no_name):
        scores = transformer.transform_scores(raw_langfuse_trace_no_name, TRACE_ID)
        assert scores == []

    def test_score_string_value(self, transformer):
        trace = {
            "scores": [
                {
                    "id": "s1",
                    "name": "quality",
                    "value": 3,
                    "stringValue": "good",
                    "dataType": "NUMERIC",
                }
            ]
        }
        scores = transformer.transform_scores(trace, TRACE_ID)
        assert scores[0]["output_str"] == "good"

    def test_score_no_id_generates_uuid(self, transformer):
        """Score missing 'id' field gets an auto-generated UUID."""
        trace = {
            "scores": [
                {
                    "name": "quality",
                    "value": 0.8,
                    "dataType": "NUMERIC",
                }
            ]
        }
        scores = transformer.transform_scores(trace, TRACE_ID)
        assert len(scores) == 1
        # The langfuse_score_id should be a valid UUID string
        uuid.UUID(scores[0]["langfuse_score_id"])

    def test_score_missing_name(self, transformer):
        """Score with missing name → empty string."""
        trace = {
            "scores": [
                {
                    "id": "s1",
                    "value": 0.5,
                    "dataType": "NUMERIC",
                }
            ]
        }
        scores = transformer.transform_scores(trace, TRACE_ID)
        assert scores[0]["eval_type_id"] == ""

    def test_no_scores_key(self, transformer):
        """Trace dict with no 'scores' key → empty list."""
        scores = transformer.transform_scores({}, TRACE_ID)
        assert scores == []

    def test_boolean_score_value_zero_is_false(self, transformer):
        """Boolean score with value 0 → output_bool is False."""
        trace = {
            "scores": [
                {
                    "id": "s1",
                    "name": "pass",
                    "value": 0,
                    "dataType": "BOOLEAN",
                }
            ]
        }
        scores = transformer.transform_scores(trace, TRACE_ID)
        assert scores[0]["output_bool"] is False


# ---------------------------------------------------------------------------
# _build_eval_attributes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildEvalAttributes:
    def test_llm_model_and_tokens(self, transformer, raw_langfuse_trace):
        obs = raw_langfuse_trace["observations"][0]  # GENERATION
        attrs = transformer._build_eval_attributes(obs, "llm", 15, 5, 20)
        assert attrs["gen_ai.span.kind"] == "LLM"
        assert attrs["llm.model_name"] == "gpt-4"
        assert attrs["llm.token_count.prompt"] == 15
        assert attrs["llm.token_count.completion"] == 5
        assert attrs["llm.token_count.total"] == 20

    def test_provider_openai(self, transformer, raw_langfuse_trace):
        obs = raw_langfuse_trace["observations"][0]
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert attrs["llm.provider"] == "openai"
        assert attrs["llm.system"] == "openai"

    def test_provider_anthropic(self, transformer):
        obs = {"type": "GENERATION", "model": "claude-3-opus-20240229"}
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert attrs["llm.provider"] == "anthropic"

    def test_provider_unknown_no_key(self, transformer):
        obs = {"type": "GENERATION", "model": "custom-model-v1"}
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert "llm.provider" not in attrs

    def test_input_messages_from_list(self, transformer, raw_langfuse_trace):
        obs = raw_langfuse_trace["observations"][0]
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert attrs["llm.input_messages.0.message.role"] == "system"
        assert attrs["llm.input_messages.0.message.content"] == "You are helpful."
        assert attrs["llm.input_messages.1.message.role"] == "user"
        assert attrs["llm.input_messages.1.message.content"] == "Hello"

    def test_output_from_string(self, transformer, raw_langfuse_trace):
        obs = raw_langfuse_trace["observations"][0]  # output is "Hi there!"
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert attrs["llm.output_messages.0.message.role"] == "assistant"
        assert attrs["llm.output_messages.0.message.content"] == "Hi there!"
        assert attrs["response"] == "Hi there!"

    def test_output_from_list(self, transformer):
        obs = {
            "type": "GENERATION",
            "output": [{"role": "assistant", "content": "reply"}],
        }
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert attrs["llm.output_messages.0.message.role"] == "assistant"
        assert attrs["llm.output_messages.0.message.content"] == "reply"

    def test_output_from_dict(self, transformer):
        obs = {
            "type": "GENERATION",
            "output": {"content": "dict-reply"},
        }
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert attrs["llm.output_messages.0.message.content"] == "dict-reply"
        assert attrs["response"] == "dict-reply"

    def test_tool_calls_in_input(self, transformer):
        obs = {
            "type": "GENERATION",
            "input": [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "function": {
                                "name": "search",
                                "arguments": '{"q": "test"}',
                            },
                        }
                    ],
                }
            ],
        }
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert (
            attrs["llm.input_messages.0.message.tool_calls.0.tool_call.id"]
            == "call_123"
        )
        assert (
            attrs["llm.input_messages.0.message.tool_calls.0.tool_call.function.name"]
            == "search"
        )

    def test_query_from_last_user_message(self, transformer, raw_langfuse_trace):
        obs = raw_langfuse_trace["observations"][0]
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert attrs["query"] == "Hello"

    def test_query_absent_when_no_user_messages(self, transformer):
        """If input has no user role messages, 'query' key should not be set."""
        obs = {
            "type": "GENERATION",
            "input": [
                {"role": "system", "content": "You are helpful."},
                {"role": "assistant", "content": "Hi there!"},
            ],
        }
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert "query" not in attrs

    def test_no_model_omits_llm_model_name(self, transformer):
        """LLM observation with no model → llm.model_name not set."""
        obs = {"type": "GENERATION", "model": None}
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert "llm.model_name" not in attrs
        assert "llm.provider" not in attrs

    def test_session_and_user_ids(self, transformer, raw_langfuse_trace):
        obs = raw_langfuse_trace["observations"][0]
        attrs = transformer._build_eval_attributes(
            obs,
            "llm",
            0,
            0,
            0,
            trace_session_id="sess-abc",
            trace_user_id="user-42",
        )
        assert attrs["session.id"] == "sess-abc"
        assert attrs["user.id"] == "user-42"

    def test_chain_span_has_no_llm_attrs(self, transformer, raw_langfuse_trace):
        obs = raw_langfuse_trace["observations"][1]  # SPAN
        attrs = transformer._build_eval_attributes(obs, "chain", 0, 0, 0)
        assert attrs["gen_ai.span.kind"] == "CHAIN"
        assert "llm.model_name" not in attrs
        assert "llm.token_count.prompt" not in attrs

    def test_invocation_parameters(self, transformer, raw_langfuse_trace):
        obs = raw_langfuse_trace["observations"][0]
        attrs = transformer._build_eval_attributes(obs, "llm", 0, 0, 0)
        assert attrs["llm.invocation_parameters"]["temperature"] == 0.7
        assert attrs["llm.invocation_parameters"]["max_tokens"] == 1024


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHelperFunctions:
    def test_parse_timestamp_iso_z(self, transformer):
        dt = transformer._parse_timestamp("2024-01-15T10:00:00.000Z")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_parse_timestamp_offset(self, transformer):
        dt = transformer._parse_timestamp("2024-01-15T10:00:00+00:00")
        assert dt is not None

    def test_parse_timestamp_none(self, transformer):
        assert transformer._parse_timestamp(None) is None

    def test_parse_timestamp_invalid(self, transformer):
        assert transformer._parse_timestamp("not-a-date") is None

    def test_map_level_error(self, transformer):
        assert transformer._map_level_to_status("ERROR") == "ERROR"

    def test_map_level_ok_variants(self, transformer):
        for level in ("DEFAULT", "DEBUG", "WARNING"):
            assert transformer._map_level_to_status(level) == "OK"

    def test_map_level_none(self, transformer):
        # None / missing level now defaults to OK (successful completion)
        assert transformer._map_level_to_status(None) == "OK"

    def test_map_level_unknown_value(self, transformer):
        """Unrecognized level string maps to UNSET."""
        assert transformer._map_level_to_status("INFO") == "UNSET"

    def test_parse_timestamp_with_milliseconds(self, transformer):
        """Timestamp with fractional seconds parses correctly."""
        dt = transformer._parse_timestamp("2024-06-15T12:30:45.123456+00:00")
        assert dt is not None
        assert dt.microsecond == 123456

    def test_parse_timestamp_empty_string(self, transformer):
        """Empty string returns None."""
        assert transformer._parse_timestamp("") is None


# ---------------------------------------------------------------------------
# _guess_provider (module-level function)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGuessProvider:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("gpt-4", "openai"),
            ("gpt-4o-mini", "openai"),
            ("o1-preview", "openai"),
            ("chatgpt-4o-latest", "openai"),
            ("claude-3-opus-20240229", "anthropic"),
            ("claude-3.5-sonnet", "anthropic"),
            ("gemini-2.0-flash", "google"),
            ("command-r-plus", "cohere"),
            ("mistral-large", "mistralai"),
            ("mixtral-8x7b", "mistralai"),
            ("llama-3.1-70b", "meta"),
            ("deepseek-chat", "deepseek"),
            ("custom-model-v1", ""),
        ],
    )
    def test_guess_provider(self, model, expected):
        assert _guess_provider(model) == expected
