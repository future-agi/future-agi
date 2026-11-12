"""Reading pasted or uploaded transcripts, and naming the line that fails."""

import json

import pytest

from simulate.utils.transcript_parse import (
    ParsedTurn,
    TranscriptError,
    parse_transcript,
)


def _turns(text, form):
    return [(turn.role, turn.text) for turn in parse_transcript(text, form).turns]


def _fails(text, form):
    with pytest.raises(TranscriptError) as caught:
        parse_transcript(text, form)
    return str(caught.value)


class TestJson:
    def test_reads_a_list_of_messages_and_leaves_out_system_and_tool_ones(self):
        text = json.dumps(
            [
                {"role": "system", "content": "You are a refund agent."},
                {"role": "user", "content": "Refund order 42"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
                {"role": "tool", "content": '{"ok": true}'},
                {"role": "assistant", "content": "Done."},
            ]
        )
        parsed = parse_transcript(text, "json")
        assert parsed.turns == (
            ParsedTurn("user", "Refund order 42"),
            ParsedTurn("agent", "Done."),
        )
        assert parsed.left_out == 3

    def test_reads_an_object_with_messages_and_content_parts(self):
        text = json.dumps(
            {
                "id": "x",
                "messages": [
                    {
                        "role": "human",
                        "content": [
                            {"type": "text", "text": "Hi"},
                            {"type": "image_url", "image_url": {"url": "data:"}},
                        ],
                    },
                    {"role": "AI", "text": "Hello"},
                ],
            }
        )
        assert _turns(text, "json") == [("user", "Hi"), ("agent", "Hello")]

    def test_bad_json_names_its_line(self):
        assert _fails('[\n  {"role": "user",\n  "content": }\n]', "json").startswith(
            "Line 3: Not valid JSON"
        )

    def test_a_bad_message_names_the_line_it_starts_on(self):
        text = (
            "[\n"
            '  {"role": "user", "content": "Hi"},\n'
            '  {"role": "agent", "content": "Hello"},\n'
            '  {"role": "manager", "content": "Hm"}\n'
            "]"
        )
        assert (
            _fails(text, "json")
            == "Line 4: Unknown speaker 'manager': use User or Agent."
        )

    def test_a_message_without_content_names_its_line(self):
        text = (
            '{"messages": [\n {"role": "user", "content": "Hi"},\n {"role": "user"}\n]}'
        )
        assert _fails(text, "json") == "Line 3: A message has no content."

    def test_json_nested_too_deeply_is_refused_not_crashed(self):
        assert _fails("[" * 100_000 + "]" * 100_000, "json") == (
            "Not valid JSON: it is nested too deeply."
        )

    def test_an_escaped_nul_is_dropped(self):
        text = json.dumps([{"role": "user", "content": "hi\u0000 there"}])
        assert "\\u0000" in text
        assert _turns(text, "json") == [("user", "hi there")]

    def test_not_a_list_of_messages(self):
        assert _fails('{"turns": []}', "json").startswith(
            "Line 1: Expected a list of messages"
        )


class TestText:
    def test_reads_speakers_and_joins_continuation_lines(self):
        text = (
            "User: My order arrived broken.\n"
            "It is order 42.\n"
            "\n"
            "Agent: Sorry to hear that. See https://help.example.com/returns\n"
            "System: tool call refund(42)\n"
            "  refund ok\n"
            "customer: Thanks\n"
        )
        parsed = parse_transcript(text, "text")
        assert [(t.role, t.text) for t in parsed.turns] == [
            ("user", "My order arrived broken.\nIt is order 42."),
            ("agent", "Sorry to hear that. See https://help.example.com/returns"),
            ("user", "Thanks"),
        ]
        assert parsed.left_out == 1

    def test_a_line_starting_with_a_link_or_a_time_is_not_a_speaker(self):
        text = "User: see\nhttps://example.com/x\nAgent: at\n10:30 then"
        assert _turns(text, "text") == [
            ("user", "see\nhttps://example.com/x"),
            ("agent", "at\n10:30 then"),
        ]

    def test_an_unknown_speaker_names_its_line(self):
        text = "User: hi\nAgent: hello\nManager: let me see"
        assert (
            _fails(text, "text")
            == "Line 3: Unknown speaker 'Manager': use User or Agent."
        )

    def test_text_before_any_speaker_names_its_line(self):
        assert _fails("\nhello there\nUser: hi", "text") == (
            "Line 2: Expected a speaker, such as 'User:' or 'Agent:', at the start "
            "of the line."
        )

    def test_crlf_and_a_byte_order_mark_are_read(self):
        assert _turns("\ufeffUser: hi\r\nAgent: hello\r\n", "text") == [
            ("user", "hi"),
            ("agent", "hello"),
        ]


class TestCsv:
    def test_reads_rows_with_a_quoted_multiline_cell(self):
        text = (
            "\ufeffSpeaker,Message,at\r\n"
            'customer,"Line one\r\nline two",1\r\n'
            "system,prompt,2\r\n"
            ",,\r\n"
            "assistant,Done,3\r\n"
        )
        parsed = parse_transcript(text, "csv")
        assert [(t.role, t.text) for t in parsed.turns] == [
            ("user", "Line one\r\nline two"),
            ("agent", "Done"),
        ]
        assert parsed.left_out == 1

    def test_a_header_without_role_or_text_names_line_1(self):
        assert _fails("who,said\nuser,hi", "csv").startswith(
            "Line 1: The first row must name a role column"
        )

    def test_a_bad_row_names_its_first_line(self):
        text = 'role,text\nuser,"hi\nthere"\nagent,hello\nboss,no\n'
        assert (
            _fails(text, "csv") == "Line 5: Unknown speaker 'boss': use User or Agent."
        )

    def test_a_short_row_names_its_line(self):
        assert _fails("role,text\nuser,hi\nagent\n", "csv") == (
            "Line 3: The row has fewer cells than the header."
        )

    def test_a_row_without_a_speaker_names_its_line(self):
        assert _fails("role,text\n,hi\n", "csv") == "Line 2: No speaker is given."

    def test_a_long_unknown_speaker_is_cut_in_the_refusal(self):
        role = "x" * 31 + " my account number is 123"
        assert _fails(f"role,text\n{role},hi\n", "csv") == (
            f"Line 2: Unknown speaker '{'x' * 30}…': use User or Agent."
        )


@pytest.mark.parametrize("form", ["json", "text", "csv"])
def test_empty_input_is_refused(form):
    assert _fails("  \n ", form) == "The transcript is empty."


def test_a_transcript_with_only_system_messages_is_refused():
    assert _fails("System: prompt\n", "text") == (
        "No message from the user or the agent was found."
    )
