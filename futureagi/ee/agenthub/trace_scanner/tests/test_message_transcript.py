"""The judge must see the conversation as ordered turns, not one flattened blob.

Per-message attributes reach the payload as a flat key/value map. Without
reconstruction the judge gets `input.value` — the whole serialized message list —
and cannot tell which agent reply answered which user question.

Two classes of content were invisible before this, measured on the 2026-08-18
prod corpus (6,266 real spans):

  * structured multi-part content (`contents.N.message_content.text`) on 869
    spans — the actual message text on multimodal messages, not metadata. Those
    messages carry no flat `.content`, so they were **entirely** unreadable.
  * per-message `tool_calls` (name + arguments) on 52/41 spans, and the legacy
    `function_call_*` pair.

Together 834/6,266 spans (13.3%) carried at least one attribute that the old
`.role|.content` filter dropped before the judge ever ran.

These tests pin what the transcript must contain and the order it must preserve,
not the exact separators — rendering stays free to change.
"""

from ee.agenthub.trace_scanner.compress import (
    _build_message_transcript,
    _exception_text,
)


class TestTranscriptReconstruction:
    def test_flat_role_and_content_render_as_a_turn(self):
        out = _build_message_transcript(
            {
                "gen_ai.input.messages.0.message.role": "user",
                "gen_ai.input.messages.0.message.content": "where is my refund",
            }
        )
        assert "user:" in out
        assert "where is my refund" in out

    def test_structured_multipart_content_is_not_lost(self):
        """The 869-span case: text lives under contents.N, there is no .content."""
        out = _build_message_transcript(
            {
                "gen_ai.input.messages.0.message.role": "user",
                "gen_ai.input.messages.0.message.contents.0.message_content.text": "describe this",
                "gen_ai.input.messages.0.message.contents.0.message_content.type": "text",
                "gen_ai.input.messages.0.message.contents.1.message_content.text": "and this",
            }
        )
        assert "describe this" in out, f"multimodal message text was dropped: {out!r}"
        assert "and this" in out
        assert out.index("describe this") < out.index("and this"), "content parts reordered"

    def test_tool_calls_stay_attached_to_the_message_that_made_them(self):
        out = _build_message_transcript(
            {
                "gen_ai.output.messages.0.message.role": "assistant",
                "gen_ai.output.messages.0.message.tool_calls.0.tool_call.function.name": "search_pokedex",
                "gen_ai.output.messages.0.message.tool_calls.0.tool_call.function.arguments": '{"q":"krabby"}',
            }
        )
        assert "search_pokedex" in out, f"tool call name was dropped: {out!r}"
        assert '{"q":"krabby"}' in out, "tool call arguments were dropped"
        assert out.startswith("assistant:"), "tool call detached from its message"

    def test_legacy_function_call_is_read(self):
        out = _build_message_transcript(
            {
                "gen_ai.output.messages.0.message.role": "assistant",
                "gen_ai.output.messages.0.message.function_call_name": "get_weather",
                "gen_ai.output.messages.0.message.function_call_arguments_json": '{"city":"pune"}',
            }
        )
        assert "get_weather" in out
        assert '{"city":"pune"}' in out

    def test_messages_order_numerically_not_lexically(self):
        """Message 2 precedes message 10 — string sorting would invert them."""
        out = _build_message_transcript(
            {
                "gen_ai.input.messages.2.message.role": "user",
                "gen_ai.input.messages.2.message.content": "SECOND",
                "gen_ai.input.messages.10.message.role": "user",
                "gen_ai.input.messages.10.message.content": "TENTH",
            }
        )
        assert out.index("SECOND") < out.index("TENTH"), f"turns out of order: {out!r}"

    def test_input_turns_render_before_output_turns(self):
        out = _build_message_transcript(
            {
                "gen_ai.output.messages.0.message.role": "assistant",
                "gen_ai.output.messages.0.message.content": "THE-REPLY",
                "gen_ai.input.messages.0.message.role": "user",
                "gen_ai.input.messages.0.message.content": "THE-REQUEST",
            }
        )
        assert out.index("THE-REQUEST") < out.index("THE-REPLY"), (
            f"the reply rendered before the request: {out!r}"
        )

    def test_openinference_namespace_is_read_when_genai_is_absent(self):
        """Both ingest namespaces are live in prod; neither may be ignored."""
        out = _build_message_transcript(
            {
                "llm.input_messages.0.message.role": "user",
                "llm.input_messages.0.message.content": "openinference path",
            }
        )
        assert "openinference path" in out

    def test_a_span_with_no_messages_yields_nothing(self):
        assert _build_message_transcript({"input.value": "not a message attr"}) == ""


class TestExceptionText:
    def test_type_and_message_are_surfaced(self):
        """The judge saw status_code=Error but never why. 142/6,266 spans carry this."""
        out = _exception_text(
            {
                "exception.type": "TimeoutError",
                "exception.message": "upstream did not respond in 30s",
            }
        )
        assert "TimeoutError" in out
        assert "upstream did not respond in 30s" in out

    def test_long_stacktrace_is_truncated(self):
        out = _exception_text({"exception.stacktrace": "x" * 900})
        assert len(out) < 900, "an unbounded stacktrace would crowd out the trace itself"
        assert out.endswith("…")

    def test_a_span_without_an_exception_yields_nothing(self):
        assert _exception_text({"status_code": "Error"}) == ""
