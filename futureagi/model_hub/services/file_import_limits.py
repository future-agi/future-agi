"""Bounds for a single atomic file import (not a limit on dataset growth).

One publication writes <=100,000 Rows, <=1,000 Columns and <=1,000,000
Cells, with <=64 MiB of normalized UTF-8 values. SQL INSERT batches and live
Cell instances are capped at 1,000. Parsing, type inference and normalization
happen before the transaction. Oversized uploads fail without partial tables;
callers can split a file. These are application payload bounds, not a promise
about PostgreSQL WAL/index overhead or DataFrame resident memory.
"""

MAX_IMPORT_ROWS = 100_000
MAX_IMPORT_COLUMNS = 1_000
MAX_IMPORT_CELLS = 1_000_000
MAX_IMPORT_VALUE_BYTES = 64 * 1024 * 1024
MAX_IMPORT_FILE_BYTES = 25 * 1024 * 1024


def check_import_shape(data):
    rows, columns = data.shape
    if (
        rows > MAX_IMPORT_ROWS
        or columns > MAX_IMPORT_COLUMNS
        or rows * columns > MAX_IMPORT_CELLS
    ):
        raise ValueError(
            "File import exceeds 100,000 rows, 1,000 columns or 1,000,000 cells. "
            "Split the file into smaller imports."
        )
    if not data.columns.is_unique:
        raise ValueError("Column names must be unique after trimming whitespace.")


def prepare_import_values(data, columns, media_types, normalize):
    """Normalize once before acquiring the publication transaction/row lock."""
    total_bytes = 0
    prepared = {}
    for column in columns:
        values = {}
        media = column.data_type in media_types
        for index, raw in data[column.name].items():
            value = str(raw) if media else normalize(raw)
            total_bytes += len(value.encode("utf-8")) if value is not None else 0
            if total_bytes > MAX_IMPORT_VALUE_BYTES:
                raise ValueError(
                    "File import exceeds 64 MiB of cell values. Split the file into smaller imports."
                )
            values[index] = value
        prepared[column.name] = values
    return prepared
