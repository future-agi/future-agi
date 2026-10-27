import io

import pytest

from model_hub.utils.file_reader import FileProcessor


def _make_csv_file(content: str) -> io.BytesIO:
    """Create a file-like object from string content."""
    f = io.BytesIO(content.encode("utf-8"))
    f.name = "test.csv"
    return f


# Test cases for the smart-quote parametrized suite below.
# Each case: (csv_content, expected_columns, expected_row_count,
#             cell_checks: list of (row_idx, column, expected, mode))
# mode: "eq" — equality; "in" — substring; "starts" — startswith
_SMART_QUOTE_CASES = [
    (
        "straight_quotes",
        "user_message,bot_response\n"
        'Hello,"I hear you, friend."\n'
        'How are you?,"I am fine, thanks."\n',
        ["user_message", "bot_response"],
        2,
        [(0, "bot_response", "I hear you, friend.", "eq")],
    ),
    (
        "smart_quotes",
        "user_message,bot_response\n"
        "Hello,\u201cI hear you, friend.\u201d\n"
        "How are you?,\u201cI am fine, thanks.\u201d\n",
        ["user_message", "bot_response"],
        2,
        # Smart quotes normalised to straight quotes in content
        [(0, "bot_response", "I hear you, friend.", "eq")],
    ),
    (
        "low9_quotes",
        "col_a,col_b\nfoo,\u201ebar, baz\u201d\n",
        ["col_a", "col_b"],
        1,
        [(0, "col_b", "bar, baz", "eq")],
    ),
    (
        "no_commas_in_values",
        "user_message,bot_response\nHello,World\nFoo,Bar\n",
        ["user_message", "bot_response"],
        2,
        [],
    ),
    (
        "mixed_quoted_unquoted",
        "user_message,bot_response\n"
        "Hello,No commas here\n"
        'Question?,"Answer with, commas"\n',
        ["user_message", "bot_response"],
        2,
        [
            (0, "bot_response", "No commas here", "eq"),
            (1, "bot_response", "Answer with, commas", "eq"),
        ],
    ),
    (
        "smart_quotes_multi_column",
        "a,b,c\n\u201cfoo, bar\u201d,\u201cbaz, qux\u201d,plain\n",
        ["a", "b", "c"],
        1,
        [
            (0, "a", "foo, bar", "eq"),
            (0, "b", "baz, qux", "eq"),
            (0, "c", "plain", "eq"),
        ],
    ),
    (
        # Underscore headers + smart quotes must not confuse the Sniffer
        # (TH-3546): character-frequency heuristics can otherwise pick 'o' as
        # delimiter when smart quotes break normal quoting recognition.
        "underscore_headers_with_smart_quotes",
        "user_message,bot_response\n"
        "I've been feeling stressed at work lately. How do I manage it?,"
        "\u201cI hear you, Suhani. Want to share a bit more about what's been going on?\u201d\n"
        "What is anxiety?,"
        "Anxiety is a feeling of worry or fear. It can come up when we face stress.\n"
        "What are some breathing exercises I can do when I feel overwhelmed?,"
        "\u201cI can't share specific exercises, but I can support you.\u201d\n",
        ["user_message", "bot_response"],
        3,
        [(0, "bot_response", "Suhani", "in")],
    ),
    (
        "underscore_headers_without_smart_quotes",
        "user_message,bot_response\n"
        'Hello world?,"I hear you, friend. How are you doing?"\n'
        "Simple question,Simple answer with no commas\n",
        ["user_message", "bot_response"],
        2,
        [(0, "bot_response", "I hear you, friend. How are you doing?", "eq")],
    ),
    (
        # Realistic ticket-shaped data with straight quotes \u2014 ensures
        # underscore headers + comma-containing quoted values yield exactly
        # two columns.
        "underscore_headers_straight_quotes_realistic",
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
        '"It sounds like you\'ve been going through a tough time. One small thing we could try is making a gratitude list."\n',
        ["user_message", "bot_response"],
        5,
        [
            (0, "bot_response", "Suhani", "in"),
            (1, "bot_response", "Anxiety is a feeling", "starts"),
        ],
    ),
    (
        "many_underscore_headers_straight_quotes",
        "first_name,last_name,user_message,bot_response,created_at\n"
        'John,Doe,Hello,"I hear you, friend.",2024-01-01\n'
        'Jane,Smith,Hi,"Sure, I can help.",2024-01-02\n',
        ["first_name", "last_name", "user_message", "bot_response", "created_at"],
        2,
        [
            (0, "bot_response", "I hear you, friend.", "eq"),
            (1, "bot_response", "Sure, I can help.", "eq"),
        ],
    ),
    (
        "many_underscore_headers_with_smart_quotes",
        "first_name,last_name,user_message,bot_response\n"
        "John,Doe,Hi there,"
        "\u201cHello, John. How can I help you today?\u201d\n"
        "Jane,Smith,Question?,"
        "\u201cSure, I can help with that.\u201d\n",
        ["first_name", "last_name", "user_message", "bot_response"],
        2,
        [
            (0, "first_name", "John", "eq"),
            (0, "bot_response", "Hello, John. How can I help you today?", "eq"),
        ],
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "csv,expected_columns,expected_rows,cell_checks",
    [case[1:] for case in _SMART_QUOTE_CASES],
    ids=[case[0] for case in _SMART_QUOTE_CASES],
)
def test_read_csv_smart_quotes(csv, expected_columns, expected_rows, cell_checks):
    """CSV parser handles straight/smart/low-9 quotes across header shapes (TH-3546)."""
    df, err = FileProcessor.process_file(_make_csv_file(csv))
    assert err is None
    assert list(df.columns) == expected_columns
    assert len(df) == expected_rows
    for row_idx, column, expected, mode in cell_checks:
        actual = df.iloc[row_idx][column]
        if mode == "in":
            assert expected in actual, f"expected {expected!r} in {actual!r}"
        elif mode == "starts":
            assert actual.startswith(expected), (
                f"expected {actual!r} to start with {expected!r}"
            )
        else:  # "eq"
            assert actual == expected


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


@pytest.mark.unit
class TestReadCsvDelimiterSelection:
    """Delimiter-selection guarantees of the widest-consistent-parse rule."""

    def _make_file(self, content: str) -> io.BytesIO:
        f = io.BytesIO(content.encode("utf-8"))
        f.name = "test.csv"
        return f

    def test_malformed_multicolumn_csv_errors_instead_of_one_column(self):
        """A file with ragged rows must raise, not silently import each raw
        line as a single-column DataFrame (every absent delimiter yields a
        trivially 'consistent' 1-column parse)."""
        csv = "id,name,age\n1,a,b,c\n2,d\n"
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert df is None
        assert "inconsistent column counts" in err

    def test_exotic_colon_delimiter_still_detected(self):
        """Genuine ':'-delimited files must keep working: the restricted
        sniff misses ':', so the unrestricted sniff supplies it as an extra
        candidate and its 3-column parse beats the 1-column comma parse."""
        csv = "a:b:c\n1:2:3\n4:5:6\n"
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["a", "b", "c"]
        assert len(df) == 2

    def test_header_only_file_raises_specific_error(self):
        """A file with just a header row keeps its dedicated error message."""
        csv = "col_a,col_b,col_c\n"
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert df is None
        assert "only a header row" in err

    def test_single_column_with_unquoted_comma_in_header(self):
        """Dev parity: a single-column file whose header contains an unquoted
        comma must parse as one column, not be rejected — only the header
        splits; every data row is a single cell."""
        csv = "first,second\nhello\nworld\n"
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["first,second"]
        assert len(df) == 2
        assert df.iloc[0, 0] == "hello"

    def test_single_column_with_unquoted_semicolon_in_header(self):
        """Same parity guarantee for a stray semicolon in the header."""
        csv = "a;b\nhello\nworld\n"
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert err is None
        assert list(df.columns) == ["a;b"]
        assert len(df) == 2

    def test_delimiter_in_some_data_rows_still_rejected(self):
        """The malformed-file guard must survive the parity refinement: when
        the delimiter appears in the *data* rows with ragged counts, the file
        errors instead of importing as one column."""
        csv = "id,name\n1,a\nlonely\n2,b\n"
        df, err = FileProcessor.process_file(self._make_file(csv))
        assert df is None
        assert "inconsistent column counts" in err
