"""The JSON and JSONL reader follows the Upload dialog's parser (importFile.js)."""

from io import BytesIO

import pytest

from model_hub.services.file_import_json import (
    JsonImportError,
    js_number_text,
    json_lines_mode,
    read_json_import,
)

# String(value) in JavaScript for each float.
JS_NUMBERS = [
    (12.5, "12.5"),
    (40.0, "40"),
    (0.75, "0.75"),
    (-3.25, "-3.25"),
    (-0.0, "0"),
    (1e21, "1e+21"),
    (1e20, "100000000000000000000"),
    (1e16, "10000000000000000"),
    (1e-7, "1e-7"),
    (1.5e-7, "1.5e-7"),
    (0.000001, "0.000001"),
    (2.5e-10, "2.5e-10"),
    (5e-324, "5e-324"),
    (1.7976931348623157e308, "1.7976931348623157e+308"),
    (0.1 + 0.2, "0.30000000000000004"),
    (float("inf"), "Infinity"),
]


@pytest.mark.parametrize(("value", "text"), JS_NUMBERS)
def test_float_text_matches_javascript_string(value, text):
    assert js_number_text(value) == text


@pytest.mark.parametrize(
    ("name", "mode"),
    [("a.json", False), ("A.JSONL", True), ("a.csv", None), ("json", None)],
)
def test_only_json_and_jsonl_names_use_the_reader(name, mode):
    assert json_lines_mode(name) is mode


def _read(content, lines=False):
    return read_json_import(BytesIO(content.encode("utf-8")), lines)


def test_columns_follow_object_key_order_and_first_appearance():
    imported = _read(
        '{"rows": [{"b": 1, "2": "x", "a": true, "10": null}, {"z": [1, 2]}, 3]}'
    )

    assert imported.frame.columns.tolist() == ["2", "10", "b", "a", "z"]
    assert imported.frame.values.tolist() == [
        ["x", None, "1", "true", None],
        [None, None, None, None, [1, 2]],
    ]
    assert imported.data_types[1:4] == ("text", "integer", "boolean")


def test_jsonl_skips_lines_that_are_not_objects_and_ignores_blank_lines():
    imported = _read(
        '\ufeff{"a": 1}\n\n \u00a0\nnot json\n[1]\n{"a": NaN}\r\n{"a": " x "}\n', True
    )

    assert imported.frame.values.tolist() == [["1"], ["x"]]
    assert imported.data_types == ("text",)
    assert imported.skipped_lines == (4, 5, 6)


def test_string_columns_never_become_numbers_or_booleans():
    imported = _read(
        '[{"code": "007", "flag": "1", "ratio": "0.5", "at": "2026-05-01"},'
        ' {"code": "010", "flag": "0", "ratio": "2", "at": "2026-05-02"}]'
    )

    assert imported.frame.values.tolist() == [
        ["007", "1", "0.5", "2026-05-01"],
        ["010", "0", "2", "2026-05-02"],
    ]
    assert imported.data_types == ("text", "text", "text", "datetime")


def test_number_columns_keep_json_spelling():
    imported = _read('[{"n": 1, "f": 2.0}, {"n": null, "f": 0.5}]')

    assert imported.frame.values.tolist() == [["1", "2"], [None, "0.5"]]
    assert imported.data_types == ("integer", "float")


@pytest.mark.parametrize(
    ("content", "lines"),
    [
        ("[]", False),
        ("[{}]", False),
        ('"text"', False),
        ('[{"a": Infinity}]', False),
        ("{", False),
        ("[1]\n\n", True),
    ],
)
def test_files_without_readable_records_are_refused(content, lines):
    with pytest.raises(JsonImportError):
        _read(content, lines)


# The dialog reads numbers with JSON.parse: every whole number up to
# Number.MAX_SAFE_INTEGER (9007199254740991) is previewed exactly, larger ones
# are not (9007199254740992 and 9007199254740993 both show as
# 9007199254740992, 10**21 as 1e+21). A stored record holding one is refused,
# never stored differently from the preview.
ROUNDED_BY_PREVIEW = [
    ('{"id": 9007199254740993}', False, "9007199254740993", None),
    ('[{"id": 1}, {"id": 9007199254740992}]', False, "9007199254740992", None),
    ('[{"id": -9007199254740992}]', False, "-9007199254740992", None),
    (
        '{"rows": [{"n": 1000000000000000000000}]}',
        False,
        "1000000000000000000000",
        None,
    ),
    (
        '[{"meta": {"ids": [7, 18446744073709551615]}}]',
        False,
        "18446744073709551615",
        None,
    ),
    ('{"a": 1}\n{"a": 2, "id": 9007199254740993}\n', True, "9007199254740993", 2),
]


@pytest.mark.parametrize(
    ("content", "lines", "number", "line"),
    ROUNDED_BY_PREVIEW,
    ids=["single-object", "array", "negative", "rows-wrapper", "nested", "jsonl"],
)
def test_whole_numbers_the_preview_rounds_are_refused(content, lines, number, line):
    with pytest.raises(JsonImportError) as refused:
        _read(content, lines)

    where = f"Line {line}: " if line else ""
    assert str(refused.value) == (
        f"{where}The number {number} can't be imported exactly. Whole numbers "
        "must be between -9007199254740991 and 9007199254740991; put this one "
        "in quotes to import it as text."
    )


@pytest.mark.parametrize(
    ("literal", "lines", "line"),
    [("9" * 5000, False, None), ("-" + "9" * 5000, True, 2)],
    ids=["json", "jsonl"],
)
def test_very_long_whole_numbers_are_refused_with_a_short_message(literal, lines, line):
    # Beyond Python's 4300-digit int conversion limit, which would otherwise
    # skip the JSONL line the preview shows.
    content = f'{{"a": 1}}\n{{"n": {literal}}}\n' if lines else f'[{{"n": {literal}}}]'

    with pytest.raises(JsonImportError) as refused:
        _read(content, lines)

    where = f"Line {line}: " if line else ""
    message = str(refused.value)
    assert message.startswith(f"{where}The number {literal[:20]}… can't be")
    assert len(message) < 240


def test_safe_whole_numbers_and_values_the_preview_drops_still_import():
    # The wrapper's other keys, non-object items and an overwritten duplicate
    # key are not stored, so their numbers do not matter.
    imported = _read(
        '{"rows": [{"max": 9007199254740991, "min": -9007199254740991,'
        ' "big": 1e21, "dup": 9007199254740993, "dup": 5, "ids": [9007199254740991]},'
        ' 9007199254740993], "total": 9007199254740993}'
    )

    assert imported.frame.columns.tolist() == ["max", "min", "big", "dup", "ids"]
    assert imported.frame.values.tolist() == [
        ["9007199254740991", "-9007199254740991", "1e+21", "5", [9007199254740991]]
    ]
    assert imported.data_types == ("integer", "integer", "float", "integer", "array")


def test_jsonl_lines_the_preview_skips_are_not_refused():
    imported = _read('{"a": 1}\n[9007199254740993]\n{"a": 9007199254740993,\n', True)

    assert imported.frame.values.tolist() == [["1"]]
    assert imported.skipped_lines == (2, 3)
