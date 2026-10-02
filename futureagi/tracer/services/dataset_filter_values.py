"""Dataset filter values read from PostgreSQL.

PostgreSQL holds the dataset and answers one column from its index; the
ClickHouse mirror is ordered by cell id and trails every write by a CDC batch.
Evaluation choice cells are decoded by ``dataset_choice_values``.
"""

import json
import uuid
from collections import Counter
from typing import TYPE_CHECKING

from django.db import InterfaceError, OperationalError, connection, transaction

from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.dataset_choice_values import (
    CHOICE_DOCUMENT_SQL,
    LITERAL_CANDIDATE_SQL,
    literal_choice,
)
from tracer.services.postgres_read_policy import (
    ApplicationPostgresReadError,
    application_postgres_reads,
)

if TYPE_CHECKING:
    from accounts.models.workspace import Workspace


class DatasetValuesTooBroad(ValueError):
    """More values match than the caller may return exactly."""


class DatasetValuesTooLarge(RuntimeError):
    """The matching values exceed the read's result-byte budget."""


# The read failures a picker answers 503 for: the request wall, a PostgreSQL
# statement timeout or lost connection (also under the read policy's own SET
# statements), and an answer over the byte budget. Anything else is a defect
# in the read and must reach Sentry.
UNAVAILABLE_READ_ERRORS = (
    ReadDeadlineExceeded,
    OperationalError,
    InterfaceError,
    ApplicationPostgresReadError,
    DatasetValuesTooLarge,
)


def _read(deadline, wall_ms, read):
    """Run ``read(fetch)`` in one read-only snapshot, each statement timed.

    Every statement gets the remaining request wall as its PostgreSQL
    ``statement_timeout``. It reads the primary, so a value written a moment
    ago is suggested at once.
    """

    def remaining_ms():
        return deadline.remaining_ms(wall_ms)

    with application_postgres_reads(
        connection=connection,
        atomic=transaction.atomic,
        check_request=remaining_ms,
        statement_timeout_ms=remaining_ms,
        read_only=True,
        repeatable_read=True,
    ):
        with connection.cursor() as cursor:

            def fetch(sql, params):
                cursor.execute(sql, params)
                columns = [col[0] for col in cursor.description]
                return [dict(zip(columns, row, strict=True)) for row in cursor]

            return read(fetch)


def _fetch_bounded(fetch, select, params, *, size, order, max_bytes):
    """Fetch ``select`` in ``order``, refusing an answer over ``max_bytes``.

    PostgreSQL has no result-size cap. The statement stops one row past the
    budget, so an oversized answer is refused without being transferred.
    """
    rows = fetch(
        "SELECT * FROM ("
        f"SELECT *, sum({size}) OVER (ORDER BY {order}) AS result_bytes "
        f"FROM ({select}) AS inventory"
        f") AS bounded WHERE result_bytes - ({size}) <= %(max_result_bytes)s "
        f"ORDER BY {order}",
        {**params, "max_result_bytes": max_bytes},
    )
    if any(row["result_bytes"] > max_bytes for row in rows):
        raise DatasetValuesTooLarge("result_bytes")
    return rows


def _distinct_values(fetch, select, params, *, max_values, max_bytes):
    """Fetch the ``val`` rows of ``select``, refusing more than ``max_values``."""
    rows = _fetch_bounded(
        fetch,
        f"{select}ORDER BY val LIMIT %(result_limit)s",
        {**params, "result_limit": max_values + 1},
        size="octet_length(val)",
        order="val",
        max_bytes=max_bytes,
    )
    if len(rows) > max_values:
        raise DatasetValuesTooBroad()
    return rows


# Dataset widget dimensions whose vocabulary PostgreSQL answers from the
# dataset and column tables. The ClickHouse mirror is ordered by cell id, so a
# workspace's column names read the whole cell table, and it trails every
# write (on dev it also misses whole columns and datasets).
_DATASET_WORKSPACE_ROWS = (
    "FROM model_hub_dataset AS d "
    "WHERE d.workspace_id = %(workspace_id)s "
    "AND d.organization_id = %(organization_id)s "
    "AND d.deleted = false "
)
# The widgets read cells, so a column is suggested only while it is live and
# holds a live cell, as when the names came from the cells. The probe stops at
# the column's first live cell on the column index; adding the cell's dataset
# makes the planner intersect the dataset index (dev: 3.5 s instead of 25 ms).
_DATASET_LIVE_COLUMN_ROWS = (
    "FROM model_hub_column AS col "
    f"WHERE col.dataset_id = ANY(ARRAY(SELECT d.id {_DATASET_WORKSPACE_ROWS})) "
    "AND col.deleted = false "
    "AND EXISTS (SELECT 1 FROM model_hub_cell AS c "
    "WHERE c.column_id = col.id AND c.deleted = false) "
)
_DATASET_METADATA_VALUES = {
    "dataset": ("d.name", _DATASET_WORKSPACE_ROWS),
    "eval_template": ("col.name", _DATASET_LIVE_COLUMN_ROWS),
    "column_name": ("col.name", _DATASET_LIVE_COLUMN_ROWS),
    "column_source": ("col.source", _DATASET_LIVE_COLUMN_ROWS),
}
DATASET_METADATA_METRICS = frozenset(_DATASET_METADATA_VALUES)


def read_dataset_metadata_values(
    workspace: "Workspace",
    metric_name: str,
    *,
    search: str,
    max_values: int,
    max_bytes: int,
    deadline: ReadDeadline,
    wall_ms: int,
) -> list[str]:
    """Return a workspace's distinct dataset or column names, in byte order.

    ``metric_name`` is one of ``DATASET_METADATA_METRICS``. More matching
    values than ``max_values`` raise ``DatasetValuesTooBroad``.
    """
    expression, rows = _DATASET_METADATA_VALUES[metric_name]
    params = {
        "workspace_id": workspace.id,
        "organization_id": workspace.organization_id,
        "search": search,
    }
    return [
        row["val"]
        for row in _read(
            deadline,
            wall_ms,
            lambda fetch: _distinct_values(
                fetch,
                f'SELECT DISTINCT {expression} COLLATE "C" AS val {rows}'
                f"AND {expression} <> '' "
                "AND (%(search)s = '' OR "
                f"strpos(lower({expression}), lower(%(search)s)) > 0) ",
                params,
                max_values=max_values,
                max_bytes=max_bytes,
            ),
        )
    ]


_CELL_ROWS = (
    "FROM model_hub_cell "
    "WHERE dataset_id = %(dataset_id)s "
    "AND column_id = %(column_id)s "
    "AND deleted = false "
    "AND value <> '' "
)


def _cell_params(dataset_id, column_id, search):
    return {
        "dataset_id": uuid.UUID(str(dataset_id)),
        "column_id": uuid.UUID(str(column_id)),
        "search": search,
    }


def read_column_values(
    dataset_id: uuid.UUID | str,
    column_id: uuid.UUID | str,
    *,
    search: str,
    max_values: int,
    max_bytes: int,
    deadline: ReadDeadline,
    wall_ms: int,
) -> list[str]:
    """Return one column's distinct non-empty values containing ``search``.

    More matching values than ``max_values`` raise ``DatasetValuesTooBroad``,
    never a sample. The caller validates that the column is the user's.
    """
    return [
        row["val"]
        for row in _read(
            deadline,
            wall_ms,
            lambda fetch: _distinct_values(
                fetch,
                f"SELECT DISTINCT value AS val {_CELL_ROWS}"
                "AND (%(search)s = '' OR strpos(lower(value), lower(%(search)s)) > 0) ",
                _cell_params(dataset_id, column_id, search),
                max_values=max_values,
                max_bytes=max_bytes,
            ),
        )
    ]


# Choice labels are decoded in Python, so an eval-choice search cannot be
# answered by matching the stored text. It can still be *bounded* by it. A
# decoded label differs from its storage only at a backslash escape, and an
# ASCII-only case-insensitive match agrees with Python's casefold only while
# both sides stay ASCII, so keeping every row that satisfies any of those three
# arms can never drop a row the decoded filter would have kept. Without it a
# narrow search still reads the whole inventory and a column above the cap
# answers 422 no matter what the user types, which the error's own advice
# cannot resolve.
_CHOICE_SEARCH_SQL = (
    # The "C" collation lowercases ASCII letters only.
    'AND (strpos(lower(value COLLATE "C"), '
    'lower(%(choice_search)s COLLATE "C")) > 0 '
    # chr(92) is a backslash: escaped storage may decode to a label whose
    # characters are not literally present.
    "OR strpos(value, chr(92)) > 0 "
    # A non-ASCII cell may casefold differently than it lowercases; never let
    # this arm decide such a row.
    "OR octet_length(value) <> char_length(value)) "
)


def read_choice_column_values(
    dataset_id: uuid.UUID | str,
    column_id: uuid.UUID | str,
    *,
    search: str,
    max_values: int,
    max_bytes: int,
    deadline: ReadDeadline,
    wall_ms: int,
) -> list[tuple[str, int]]:
    """Return each distinct stored text of an eval-choice column with its modes.

    Mode bit 1: some cell reads the text as a container of labels. Bit 2: some
    cell's own metadata names the text itself as the choice. ``search`` only
    bounds the read; the caller filters the decoded labels. More stored texts
    than ``max_values`` raise ``DatasetValuesTooBroad``.
    """
    params = _cell_params(dataset_id, column_id, search)
    choice_search = search.strip()
    search_clause = ""
    if choice_search and choice_search.isascii():
        search_clause = _CHOICE_SEARCH_SQL
        params["choice_search"] = choice_search
    rows_sql = f"{_CELL_ROWS}{search_clause}"

    def read(fetch):
        values = _distinct_values(
            fetch,
            f"SELECT value AS val, count(*) AS cells {rows_sql}GROUP BY value ",
            params,
            max_values=max_values,
            max_bytes=max_bytes,
        )
        literal = _literal_cells(
            fetch, rows_sql, params, [row["val"] for row in values], max_bytes
        )
        return [
            (
                row["val"],
                (2 if literal[row["val"]] else 0)
                | (1 if literal[row["val"]] < row["cells"] else 0),
            )
            for row in values
        ]

    return _read(deadline, wall_ms, read)


def _literal_cells(fetch, rows_sql, params, values, max_bytes):
    """Count, per stored text, the cells whose metadata names it the choice.

    Only bracketed storage reads differently as a literal (a scalar is its own
    label either way). SQL ships only the few cells whose metadata may name
    their text (``LITERAL_CANDIDATE_SQL``), and ``literal_choice`` decides.
    """
    bracketed = [value for value in values if "[" in value or "{" in value]
    counts = Counter()
    if not bracketed:
        return counts
    for cell in _fetch_bounded(
        fetch,
        "SELECT id, val, value_infos FROM ("
        "SELECT id, value AS val, value_infos::text AS value_infos, "
        "%(ascii_forms)s::jsonb ->> value AS ascii_form, "
        f"{CHOICE_DOCUMENT_SQL} AS document {rows_sql}"
        # OFFSET 0 keeps PostgreSQL from inlining ``document`` into each arm
        # of the predicate, which would unwrap every cell's metadata per arm.
        "AND value = ANY(%(literal_values)s::text[]) OFFSET 0"
        f") AS cells WHERE {LITERAL_CANDIDATE_SQL}",
        {
            **params,
            "literal_values": bracketed,
            "ascii_forms": json.dumps(
                {value: json.dumps(value) for value in bracketed}
            ),
        },
        size="octet_length(val) + octet_length(value_infos)",
        order="id",
        max_bytes=max_bytes,
    ):
        if literal_choice(cell["val"], cell["value_infos"]):
            counts[cell["val"]] += 1
    return counts
