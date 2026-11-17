"""Read JSON and JSONL imports with the Upload dialog's record rules.

The dialog previews a file before anything is created
(frontend/src/sections/develop/AddDatasetDrawer/importFile.js parseImport),
and the import must store exactly what it showed:

- JSON: an array of records, an object whose ``rows`` is an array, or one
  object. Items that are not objects are ignored.
- JSONL: one object per non-blank line. A line that is not a JSON object is
  skipped, as the preview says.
- Columns: every key, in the order the preview lists them.
- Values keep the type the file gives them. Strings are never read as numbers
  or dates, numbers and booleans keep their JSON spelling, nested objects and
  lists stay JSON, and null is an empty cell.
- Numbers: the dialog reads them with JSON.parse. It shows every whole number
  (an integer literal) within Number.MAX_SAFE_INTEGER exactly and cannot show
  larger ones exactly (9007199254740992 and 9007199254740993 both show as
  9007199254740992, 10**21 as 1e+21). A stored record holding a whole number
  outside that range, at any depth, refuses the file, so the route answers
  before anything is created; quoted, the number imports as text. A number
  with a fraction or exponent is the same double on both sides: a cell stores
  the dialog's spelling (js_number_text), a nested one the same JSON number.
"""

import json
import math
import os
import re
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd

from model_hub.models.choices import DataTypeChoices, determine_data_type

_EXTENSIONS = {".json": False, ".jsonl": True}
_LINE_BREAK = re.compile(r"\r?\n")
# String.prototype.trim: ECMAScript WhiteSpace and LineTerminator characters.
_JS_SPACE = "".join(
    map(
        chr,
        (
            *(0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680),
            *range(0x2000, 0x200B),
            *(0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF),
        ),
    )
)
_ARRAY_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_MAX_ARRAY_INDEX = 2**32 - 2
# A column of JSON strings stays text even when every string looks numeric.
_FROM_STRINGS_AS_TEXT = {
    DataTypeChoices.BOOLEAN.value,
    DataTypeChoices.INTEGER.value,
    DataTypeChoices.FLOAT.value,
}
NO_DATA = "The file contains no data"
# Number.MAX_SAFE_INTEGER. Every JSON integer literal of at most 15 characters
# is within it; one with more digits than it has is beyond it.
MAX_SAFE_INTEGER = 2**53 - 1
_SAFE_LITERAL_LENGTH = 15
_MAX_SAFE_DIGITS = len(str(MAX_SAFE_INTEGER))
_SHOWN_LITERAL_LENGTH = 24
UNSAFE_INTEGER = (
    "{where}The number {number} can't be imported exactly. Whole numbers must "
    f"be between -{MAX_SAFE_INTEGER} and {MAX_SAFE_INTEGER}; put this one in "
    "quotes to import it as text."
)


class JsonImportError(ValueError):
    """The file cannot be imported; the message is safe to show."""


class _UnsafeInteger:
    """A whole number literal the dialog cannot show exactly."""

    __slots__ = ("literal",)

    def __init__(self, literal):
        self.literal = literal


@dataclass(frozen=True)
class JsonImport:
    frame: pd.DataFrame
    data_types: tuple[str, ...]
    skipped_lines: tuple[int, ...]


def json_lines_mode(filename):
    """False for .json, True for .jsonl, None for any other file."""
    return _EXTENSIONS.get(os.path.splitext(str(filename or "").lower())[1])


def _refuse_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def _loads(text):
    """(value, whether it holds an _UnsafeInteger) of one JSON text.

    Integers beyond MAX_SAFE_INTEGER become _UnsafeInteger, so they are never
    converted (no 4300-digit limit) and only refuse the file when stored.
    """
    unsafe = False

    def parse_int(literal):
        nonlocal unsafe
        if len(literal) <= _SAFE_LITERAL_LENGTH:
            return int(literal)
        if len(literal.lstrip("-")) <= _MAX_SAFE_DIGITS:
            value = int(literal)
            if abs(value) <= MAX_SAFE_INTEGER:
                return value
        unsafe = True
        return _UnsafeInteger(literal)

    value = json.loads(text, parse_constant=_refuse_constant, parse_int=parse_int)
    return value, unsafe


def _refuse_unsafe_integers(record, line):
    """Refuse a stored record holding an _UnsafeInteger, the first in file order."""
    pending = [record]
    while pending:
        value = pending.pop()
        if isinstance(value, _UnsafeInteger):
            literal = value.literal
            if len(literal) > _SHOWN_LITERAL_LENGTH:
                literal = f"{literal[:20]}…"
            raise JsonImportError(
                UNSAFE_INTEGER.format(
                    where=f"Line {line}: " if line else "", number=literal
                )
            )
        if isinstance(value, dict):
            pending.extend(reversed(list(value.values())))
        elif isinstance(value, list):
            pending.extend(reversed(value))


def _records(text, lines):
    if not lines:
        try:
            value, unsafe = _loads(text)
        except ValueError as exc:
            raise JsonImportError(f"Invalid JSON format: {exc}") from exc
        if isinstance(value, list):
            items = value
        elif isinstance(value, dict) and isinstance(value.get("rows"), list):
            items = value["rows"]
        else:
            items = [value]
        records = [item for item in items if isinstance(item, dict)]
        if unsafe:
            for record in records:
                _refuse_unsafe_integers(record, None)
        return records, ()
    records = []
    skipped = []
    for number, line in enumerate(_LINE_BREAK.split(text), start=1):
        if not line.strip(_JS_SPACE):
            continue
        try:
            value, unsafe = _loads(line)
        except ValueError:
            value, unsafe = None, False
        if isinstance(value, dict):
            if unsafe:
                _refuse_unsafe_integers(value, number)
            records.append(value)
        else:
            skipped.append(number)
    return records, tuple(skipped)


def _is_array_index(key):
    return bool(_ARRAY_INDEX.match(key)) and int(key) <= _MAX_ARRAY_INDEX


def _keys(record):
    """Object.keys order: array-index keys ascending, then insertion order."""
    indexes = sorted((key for key in record if _is_array_index(key)), key=int)
    return indexes + [key for key in record if not _is_array_index(key)]


def _columns(records):
    seen = {}
    for record in records:
        for key in _keys(record):
            seen.setdefault(key, None)
    return list(seen)


def js_number_text(value):
    """A float as JavaScript's String(number) spells it."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    _, digit_tuple, exponent = Decimal(repr(abs(value))).as_tuple()
    digits = "".join(map(str, digit_tuple)).rstrip("0")
    exponent += len(digit_tuple) - len(digits)
    k = len(digits)
    n = exponent + k
    if k <= n <= 21:
        text = digits + "0" * (n - k)
    elif 0 < n <= 21:
        text = f"{digits[:n]}.{digits[n:]}"
    elif -6 < n <= 0:
        text = "0." + "0" * -n + digits
    else:
        power = f"e{'+' if n - 1 >= 0 else '-'}{abs(n - 1)}"
        text = digits + power if k == 1 else f"{digits[0]}.{digits[1:]}{power}"
    return sign + text


def _cell(value):
    if value is None:
        return None
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return js_number_text(value)
    if isinstance(value, str):
        # The CSV importer stores trimmed text and blank text as empty.
        return value.strip() or None
    return value


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _data_type(values):
    present = [value for value in values if value is not None]
    if not present:
        return DataTypeChoices.TEXT.value
    if all(isinstance(value, bool) for value in present):
        return DataTypeChoices.BOOLEAN.value
    if all(_is_number(value) for value in present):
        if all(isinstance(value, int) for value in present):
            return DataTypeChoices.INTEGER.value
        return DataTypeChoices.FLOAT.value
    if all(isinstance(value, (dict, list)) for value in present):
        return determine_data_type(pd.Series(present, dtype=object))
    if all(isinstance(value, str) for value in present):
        texts = [text for text in (value.strip() for value in present) if text]
        if not texts:
            return DataTypeChoices.TEXT.value
        inferred = determine_data_type(pd.Series(texts, dtype=object))
        if inferred in _FROM_STRINGS_AS_TEXT:
            return DataTypeChoices.TEXT.value
        return inferred
    return DataTypeChoices.TEXT.value


def read_json_import(file_obj, lines):
    """Records of a JSON (lines=False) or JSONL (lines=True) upload."""
    file_obj.seek(0)
    raw = file_obj.read()
    # Blob.text() in the dialog: UTF-8, a leading BOM dropped, bad bytes
    # replaced rather than refused.
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
    records, skipped = _records(text, lines)
    columns = _columns(records)
    if not records or not columns:
        raise JsonImportError(NO_DATA)
    raw_columns = [[record.get(column) for record in records] for column in columns]
    frame = pd.DataFrame(
        {
            position: pd.Series([_cell(value) for value in values], dtype=object)
            for position, values in enumerate(raw_columns)
        }
    )
    frame.columns = columns
    return JsonImport(
        frame=frame,
        data_types=tuple(_data_type(values) for values in raw_columns),
        skipped_lines=skipped,
    )
