"""Seed-record files attached to an environment.

A seed-records file (role `seed_records` in `knowledge/roles.json`) holds rows the world starts with, as CSV or as a
JSON list of objects. How one file's rows refer to another's is not declared anywhere yet, so files are shown as they
are, never joined by guessing from field names.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping

SEED_FORMATS = (".csv", ".json")


def seed_records_problem(path: str, body: bytes) -> str | None:
    """Why a file cannot be read as seed records, naming the line where it can; None when it reads as rows."""
    if not path.lower().endswith(SEED_FORMATS):
        return f"seed records are {' or '.join(one.lstrip('.').upper() for one in SEED_FORMATS)}"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return "the file is not UTF-8 text"
    if path.lower().endswith(".csv"):
        reader = csv.reader(io.StringIO(text), strict=True)
        while True:
            line = reader.line_num + 1
            try:
                next(reader)
            except StopIteration:
                return None
            except csv.Error as exc:
                if "unexpected end of data" in str(exc):
                    return f"line {line}: a quoted field is never closed"
                return f"line {line}: {exc}"
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return f"line {exc.lineno}: not valid JSON ({exc.msg})"
    if not isinstance(value, list) or not all(
        isinstance(one, Mapping) for one in value
    ):
        return "seed records in JSON are a list of objects, one per row"
    return None
