import io

import pytest

from model_hub.utils.file_reader import FileProcessor


@pytest.mark.unit
class TestReadCsvSmartQuotes:
    """Tests for CSV parsing with smart/curly quotes (TH-3546)."""

    def _make_file(self, content: str) -> io.BytesIO:
        """Create a file-like object from string content."""
        f = io.BytesIO(content.encode("utf-8"))
        f.name = "test.csv"
        return f

    def test_csv_with_straight_quotes(self):
        """Standard CSV with straight quotes should parse correctly."""
        csv = (
            "user_message,bot_response\n"
            'Hello,"I hear you, friend."\n'
            'How are you?,"I am fine, thanks."\n'
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["user_message", "bot_response"]
        assert len(df) == 2
        assert df.iloc[0]["bot_response"] == "I hear you, friend."

    def test_csv_with_smart_quotes(self):
        """CSV with smart/curly quotes should parse correctly (TH-3546)."""
        csv = (
            "user_message,bot_response\n"
            "Hello,\u201cI hear you, friend.\u201d\n"
            "How are you?,\u201cI am fine, thanks.\u201d\n"
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["user_message", "bot_response"]
        assert len(df) == 2
        # Smart quotes should be normalised to straight quotes in content
        assert df.iloc[0]["bot_response"] == "I hear you, friend."

    def test_csv_with_low9_quotes(self):
        """CSV with low-9 double quotes (\u201e) should parse correctly."""
        csv = "col_a,col_b\n" "foo,\u201ebar, baz\u201d\n"
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["col_a", "col_b"]
        assert df.iloc[0]["col_b"] == "bar, baz"

    def test_csv_without_commas_in_values(self):
        """CSV without commas in values should parse correctly regardless of quotes."""
        csv = "user_message,bot_response\n" "Hello,World\n" "Foo,Bar\n"
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["user_message", "bot_response"]
        assert len(df) == 2

    def test_csv_mixed_quoted_unquoted(self):
        """CSV with mix of quoted and unquoted fields should work."""
        csv = (
            "user_message,bot_response\n"
            "Hello,No commas here\n"
            'Question?,"Answer with, commas"\n'
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert len(df) == 2
        assert df.iloc[0]["bot_response"] == "No commas here"
        assert df.iloc[1]["bot_response"] == "Answer with, commas"

    def test_csv_smart_quotes_multi_column(self):
        """CSV with smart quotes across multiple columns."""
        csv = "a,b,c\n" "\u201cfoo, bar\u201d,\u201cbaz, qux\u201d,plain\n"
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["a", "b", "c"]
        assert df.iloc[0]["a"] == "foo, bar"
        assert df.iloc[0]["b"] == "baz, qux"
        assert df.iloc[0]["c"] == "plain"

    def test_csv_underscore_headers_with_smart_quotes(self):
        """Underscore headers + smart quotes should not confuse Sniffer (TH-3546).

        The Sniffer can misdetect the delimiter (e.g. 'o') when smart quotes
        break normal quoting recognition and underscored header names skew
        character frequency patterns.
        """
        csv = (
            "user_message,bot_response\n"
            "I've been feeling stressed at work lately. How do I manage it?,"
            "\u201cI hear you, Suhani. Want to share a bit more about what's been going on?\u201d\n"
            "What is anxiety?,"
            "Anxiety is a feeling of worry or fear. It can come up when we face stress.\n"
            "What are some breathing exercises I can do when I feel overwhelmed?,"
            "\u201cI can't share specific exercises, but I can support you.\u201d\n"
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["user_message", "bot_response"]
        assert len(df) == 3
        assert "Suhani" in df.iloc[0]["bot_response"]

    def test_csv_underscore_headers_without_smart_quotes(self):
        """Underscore headers with straight quotes should work normally."""
        csv = (
            "user_message,bot_response\n"
            'Hello world?,"I hear you, friend. How are you doing?"\n'
            "Simple question,Simple answer with no commas\n"
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["user_message", "bot_response"]
        assert len(df) == 2
        assert df.iloc[0]["bot_response"] == "I hear you, friend. How are you doing?"

    def test_csv_underscore_headers_straight_quotes_realistic(self):
        """Realistic CSV matching the ticket data but with straight quotes.

        Ensures underscore headers (user_message, bot_response) with properly
        quoted comma-containing values parse into the correct two columns.
        """
        csv = (
            "user_message,bot_response\n"
            "I've been feeling stressed at work lately. How do I manage it?,"
            '"I hear you, Suhani. Want to share a bit more about what\'s been going on?"\n'
            "What is anxiety?,"
            "Anxiety is a feeling of worry or fear. It can come up when we face stress.\n"
            "What are some breathing exercises I can do when I feel overwhelmed?,"
            '"I can\'t share specific exercises, but I can support you in finding a calming way to breathe."\n'
            "How does cognitive behavioral therapy work?,"
            '"I\'m not a therapist, but I can offer some gentle support. CBT focuses on how our thoughts affect our feelings and actions."\n'
            "I've been feeling a bit low lately. Any tips to boost my mood?,"
            '"It sounds like you\'ve been going through a tough time. One small thing we could try is making a gratitude list."\n'
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["user_message", "bot_response"]
        assert len(df) == 5
        assert "Suhani" in df.iloc[0]["bot_response"]
        # Unquoted row without commas should also parse fine
        assert df.iloc[1]["bot_response"].startswith("Anxiety is a feeling")

    def test_csv_many_underscore_headers_straight_quotes(self):
        """Multiple underscore headers with straight-quoted values."""
        csv = (
            "first_name,last_name,user_message,bot_response,created_at\n"
            'John,Doe,Hello,"I hear you, friend.",2024-01-01\n'
            'Jane,Smith,Hi,"Sure, I can help.",2024-01-02\n'
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == [
            "first_name",
            "last_name",
            "user_message",
            "bot_response",
            "created_at",
        ]
        assert len(df) == 2
        assert df.iloc[0]["bot_response"] == "I hear you, friend."
        assert df.iloc[1]["bot_response"] == "Sure, I can help."

    def test_csv_many_underscore_headers_with_smart_quotes(self):
        """Multiple underscore headers with smart-quoted values."""
        csv = (
            "first_name,last_name,user_message,bot_response\n"
            "John,Doe,Hi there,"
            "\u201cHello, John. How can I help you today?\u201d\n"
            "Jane,Smith,Question?,"
            "\u201cSure, I can help with that.\u201d\n"
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == [
            "first_name",
            "last_name",
            "user_message",
            "bot_response",
        ]
        assert len(df) == 2
        assert df.iloc[0]["first_name"] == "John"
        assert df.iloc[0]["bot_response"] == "Hello, John. How can I help you today?"


@pytest.mark.unit
class TestReadCsvCurlyQuotesAsContent:
    """Curly quotes as literal *content* inside properly-quoted fields.

    The smart-quote normalization (TH-3546) used to run unconditionally
    before parsing. That rescues files whose field *wrappers* are curly
    quotes, but corrupts files where curly quotes are ordinary text inside
    straight-quoted fields: the rewrite injects unescaped '"' mid-field,
    desyncing the reader's quoting state until every delimiter yields
    inconsistent column counts and the upload fails. The parser now tries
    the file as-is first and only falls back to the normalized text, keeping
    whichever candidate parses widest.
    """

    def _make_file(self, content: str) -> io.BytesIO:
        f = io.BytesIO(content.encode("utf-8"))
        f.name = "test.csv"
        return f

    def test_curly_quotes_inside_straight_quoted_fields(self):
        """The regression: curly quotes as content must not break parsing."""
        csv = (
            "id,transcript\n"
            '1,"The agent said \u201cplease hold, sir\u201d and hung up."\n'
            '2,"She replied \u201cno, thanks\u201d twice."\n'
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["id", "transcript"]
        assert len(df) == 2
        # Content is preserved verbatim — the normalization candidate must
        # not win and rewrite the curly quotes.
        assert df.iloc[0]["transcript"] == (
            "The agent said \u201cplease hold, sir\u201d and hung up."
        )

    def test_quoted_json_cells_with_colons_newlines_and_curly_quotes(self):
        """Mimics the failing production dataset: JSON blobs in quoted cells.

        Covers three old failure modes at once: the unrestricted Sniffer
        picking ':' from the JSON as delimiter, embedded newlines inside
        quoted fields, and curly quotes as literal content.
        """
        json_cell = (
            '"{""role"": ""system"", ""content"": ""Say \u201chi\u201d to the\n'
            'customer, then stop.""}"'
        )
        csv = (
            "id,assistant,score\n"
            f"a1,{json_cell},0.9\n"
            f"a2,{json_cell},0.7\n"
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["id", "assistant", "score"]
        assert len(df) == 2
        assert df.iloc[0]["assistant"].startswith('{"role": "system"')
        assert "\u201chi\u201d" in df.iloc[0]["assistant"]

    def test_widest_consistent_parse_wins_over_single_column(self):
        """A delimiter absent from the file always yields a 'consistent'
        1-column parse; the true delimiter's wider parse must win."""
        csv = (
            "a;b;c\n"
            "1;2;3\n"
            "4;5;6\n"
        )
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["a", "b", "c"]
        assert len(df) == 2

    def test_single_column_csv_still_parses(self):
        """Genuine single-column files remain accepted."""
        csv = "only_column\nvalue one\nvalue two\n"
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["only_column"]
        assert len(df) == 2
