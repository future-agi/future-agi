"""Revision-bound continuation cursors for exact dataset-table reads.

Each page still runs in its own repeatable-read transaction. A read-only,
fixed-cardinality lifecycle fingerprint provides the cross-request fence:
Dataset/Row/Column/Cell mutations advance either a lifecycle clock, row count,
or transaction id and invalidate the continuation. No table or trigger is
installed on the source database.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from hashlib import sha256

from django.conf import settings
from django.core import signing
from django.db import connection
from rest_framework.utils.encoders import JSONEncoder

from model_hub.services.dataset_read_limits import DATASET_READ_LIMITS

CURSOR_VERSION = 3
CURSOR_SALT = "model_hub.dataset-table-snapshot.v3"
DATASET_TABLE_SERVER_WALL_SECONDS = DATASET_READ_LIMITS.server_wall_seconds
DATASET_TABLE_STATEMENT_TIMEOUT_MS = int(DATASET_TABLE_SERVER_WALL_SECONDS * 1_000)
DATASET_TABLE_EXACT_MAX_COLUMNS = DATASET_READ_LIMITS.exact_max_columns
DATASET_TABLE_EXACT_MAX_CELLS = DATASET_READ_LIMITS.exact_max_cells
DATASET_TABLE_EXACT_MAX_CELL_VALUE_BYTES = (
    DATASET_READ_LIMITS.exact_max_cell_value_bytes
)
DATASET_TABLE_EXACT_MAX_CELL_VARIABLE_BYTES = (
    DATASET_READ_LIMITS.exact_max_cell_variable_bytes
)
DATASET_TABLE_EXACT_MAX_SCHEMA_BYTES = DATASET_READ_LIMITS.exact_max_schema_bytes
DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES = (
    DATASET_READ_LIMITS.exact_max_serialized_bytes
)


class DatasetTableCursorError(ValueError):
    """Sanitized cursor error safe to expose at the API boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class DatasetTableSnapshotChanged(DatasetTableCursorError):
    """The source dataset no longer matches the cursor's MVCC revision."""


class DatasetTableSnapshotUnavailable(RuntimeError):
    """The database cannot provide the exact snapshot contract."""


class DatasetTableReadDeadlineExceeded(TimeoutError):
    """The bounded exact-read action exhausted its shared server wall."""


class DatasetTableExactLimitExceeded(RuntimeError):
    """The dataset cannot fit in one bounded exact page contract."""

    code = "dataset_exact_limit_exceeded"


@dataclass(frozen=True)
class DatasetTableReadDeadline:
    """One monotonic wall shared by every SQL and Python stage in a page read."""

    expires_at: float

    @classmethod
    def start(cls) -> DatasetTableReadDeadline:
        return cls(time.monotonic() + DATASET_TABLE_SERVER_WALL_SECONDS)

    def remaining_ms(self) -> int:
        remaining = int((self.expires_at - time.monotonic()) * 1_000)
        if remaining <= 0:
            raise DatasetTableReadDeadlineExceeded(
                "The exact dataset read deadline was exceeded."
            )
        return remaining

    def checkpoint(self) -> None:
        self.remaining_ms()

    def set_statement_timeout(self) -> None:
        """Cap the next PostgreSQL statement to this action's remaining wall."""

        if connection.vendor != "postgresql":
            raise DatasetTableSnapshotUnavailable(
                "Exact dataset pagination requires PostgreSQL MVCC snapshots."
            )
        timeout_ms = self.remaining_ms()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [str(timeout_ms)],
            )


@dataclass(frozen=True)
class DatasetTableRevision:
    revision: str
    active_rows: int


@dataclass(frozen=True)
class DatasetTableCursor:
    revision: DatasetTableRevision
    page_index: int
    page_size: int
    seen_rows: int
    last_order: int
    last_id: str


def _cursor_max_age_seconds() -> int:
    return DATASET_READ_LIMITS.cursor_max_age_seconds


def assert_dataset_table_shape_within_limits(
    *,
    dataset_id: str,
    deadline: DatasetTableReadDeadline,
) -> None:
    """Reject wide/large schema state before Django materializes JSON fields."""

    deadline.set_statement_timeout()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              octet_length(COALESCE(dataset.column_order::text, ''))
                + octet_length(COALESCE(dataset.column_config::text, ''))
                + octet_length(COALESCE(dataset.dataset_config::text, ''))
                + octet_length(COALESCE(dataset.synthetic_dataset_config::text, ''))
                AS dataset_schema_bytes,
              column_state.column_count,
              column_state.column_bytes
            FROM model_hub_dataset AS dataset
            CROSS JOIN LATERAL (
              SELECT
                COUNT(*)::bigint AS column_count,
                COALESCE(SUM(
                  octet_length(COALESCE(dataset_column.name, ''))
                    + octet_length(COALESCE(dataset_column.source_id, ''))
                    + octet_length(COALESCE(dataset_column.metadata::text, ''))
                ), 0)::bigint AS column_bytes
              FROM model_hub_column AS dataset_column
              WHERE dataset_column.dataset_id = dataset.id
                AND NOT dataset_column.deleted
            ) AS column_state
            WHERE dataset.id = %s
            """,
            [str(dataset_id)],
        )
        state = cursor.fetchone()
    if not state:
        raise DatasetTableSnapshotUnavailable(
            "The dataset exact-read shape could not be verified."
        )
    dataset_bytes, column_count, column_bytes = (int(value or 0) for value in state)
    if column_count > DATASET_TABLE_EXACT_MAX_COLUMNS:
        raise DatasetTableExactLimitExceeded(
            f"Exact dataset import supports at most "
            f"{DATASET_TABLE_EXACT_MAX_COLUMNS} columns; this dataset has "
            f"{column_count}."
        )
    if dataset_bytes + column_bytes > DATASET_TABLE_EXACT_MAX_SCHEMA_BYTES:
        raise DatasetTableExactLimitExceeded(
            "Dataset schema metadata is too large for a bounded exact import."
        )


def assert_dataset_table_cells_within_limits(
    *,
    row_ids: list[str],
    column_ids: list[str],
    deadline: DatasetTableReadDeadline,
) -> None:
    """Bound cell cardinality and variable bytes before ORM materialization."""

    if not row_ids or not column_ids:
        return
    deadline.set_statement_timeout()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              COUNT(*)::bigint AS cell_count,
              COALESCE(MAX(octet_length(COALESCE(cell.value, ''))), 0)::bigint
                AS max_value_bytes,
              COALESCE(SUM(
                octet_length(COALESCE(cell.value, ''))
                  + octet_length(COALESCE(cell.value_infos::text, ''))
                  + octet_length(COALESCE(cell.feedback_info::text, ''))
              ), 0)::bigint AS variable_bytes
            FROM model_hub_cell AS cell
            WHERE NOT cell.deleted
              AND cell.row_id = ANY(%s::uuid[])
              AND cell.column_id = ANY(%s::uuid[])
            """,
            [[str(value) for value in row_ids], [str(value) for value in column_ids]],
        )
        cell_count, max_value_bytes, variable_bytes = (
            int(value or 0) for value in cursor.fetchone()
        )
    if cell_count > DATASET_TABLE_EXACT_MAX_CELLS:
        raise DatasetTableExactLimitExceeded(
            f"An exact dataset page supports at most "
            f"{DATASET_TABLE_EXACT_MAX_CELLS} cells; this page has {cell_count}."
        )
    if max_value_bytes > DATASET_TABLE_EXACT_MAX_CELL_VALUE_BYTES:
        raise DatasetTableExactLimitExceeded(
            f"A dataset cell value exceeds the "
            f"{DATASET_TABLE_EXACT_MAX_CELL_VALUE_BYTES // 1024} KiB exact-import limit."
        )
    if variable_bytes > DATASET_TABLE_EXACT_MAX_CELL_VARIABLE_BYTES:
        raise DatasetTableExactLimitExceeded(
            "Dataset cell values are too large for one bounded exact page."
        )


def assert_dataset_table_response_within_limits(response_data: dict) -> None:
    """Enforce the final wire-size ceiling before DRF renders the response."""

    encoded = JSONEncoder(separators=(",", ":"), ensure_ascii=False).encode(
        {"status": True, "result": response_data}
    )
    if len(encoded.encode("utf-8")) > DATASET_TABLE_EXACT_MAX_SERIALIZED_BYTES:
        raise DatasetTableExactLimitExceeded(
            "The exact dataset page exceeds the configured response limit."
        )


def begin_repeatable_read_snapshot(
    deadline: DatasetTableReadDeadline | None = None,
) -> str:
    """Freeze this request's reads and return its PostgreSQL snapshot token."""

    if connection.vendor != "postgresql":
        raise DatasetTableSnapshotUnavailable(
            "Exact dataset pagination requires PostgreSQL MVCC snapshots."
        )
    deadline = deadline or DatasetTableReadDeadline.start()
    with connection.cursor() as cursor:
        # The caller enters ``transaction.atomic`` before invoking this helper,
        # and invokes it before its first application query.
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        deadline.set_statement_timeout()
        cursor.execute("SELECT txid_current_snapshot()::text")
        row = cursor.fetchone()
    if not row or not row[0]:
        raise DatasetTableSnapshotUnavailable(
            "The dataset snapshot could not be established."
        )
    return str(row[0])


def _read_revision_state(
    dataset_id: str,
    *,
    deadline: DatasetTableReadDeadline | None = None,
) -> DatasetTableRevision:
    """Read one fixed-cardinality lifecycle fingerprint for this dataset."""

    if deadline is not None:
        deadline.set_statement_timeout()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH xid_clock AS MATERIALIZED (
              SELECT txid_current()::bigint AS current_txid
            ), requested AS (
              SELECT %s::uuid AS dataset_id
            ), dataset_state AS (
              SELECT
                dataset.updated_at::text AS updated_at,
                dataset.deleted,
                COALESCE(dataset.deleted_at, dataset.updated_at)::text AS deleted_at,
                xid_clock.current_txid - age(dataset.xmin) AS transaction_id
              FROM model_hub_dataset AS dataset
              JOIN requested ON requested.dataset_id = dataset.id
              CROSS JOIN xid_clock
            ), row_state AS (
              SELECT
                COUNT(*)::bigint AS total_count,
                COUNT(*) FILTER (WHERE NOT dataset_row.deleted)::bigint
                  AS active_count,
                COALESCE(
                  MAX(GREATEST(
                    dataset_row.updated_at,
                    COALESCE(dataset_row.deleted_at, dataset_row.updated_at)
                  ))::text,
                  ''
                ) AS lifecycle_clock,
                COALESCE(
                  MAX(xid_clock.current_txid - age(dataset_row.xmin)),
                  0
                )::bigint
                  AS transaction_id
              FROM model_hub_row AS dataset_row
              JOIN requested ON requested.dataset_id = dataset_row.dataset_id
              CROSS JOIN xid_clock
            ), column_state AS (
              SELECT
                COUNT(*)::bigint AS total_count,
                COUNT(*) FILTER (WHERE NOT dataset_column.deleted)::bigint
                  AS active_count,
                COALESCE(
                  MAX(GREATEST(
                    dataset_column.updated_at,
                    COALESCE(dataset_column.deleted_at, dataset_column.updated_at)
                  ))::text,
                  ''
                ) AS lifecycle_clock,
                COALESCE(
                  MAX(xid_clock.current_txid - age(dataset_column.xmin)),
                  0
                )::bigint
                  AS transaction_id
              FROM model_hub_column AS dataset_column
              JOIN requested ON requested.dataset_id = dataset_column.dataset_id
              CROSS JOIN xid_clock
            ), cell_state AS (
              SELECT
                COUNT(*)::bigint AS total_count,
                COUNT(*) FILTER (WHERE NOT dataset_cell.deleted)::bigint
                  AS active_count,
                COALESCE(
                  MAX(GREATEST(
                    dataset_cell.updated_at,
                    COALESCE(dataset_cell.deleted_at, dataset_cell.updated_at)
                  ))::text,
                  ''
                ) AS lifecycle_clock,
                COALESCE(
                  MAX(xid_clock.current_txid - age(dataset_cell.xmin)),
                  0
                )::bigint
                  AS transaction_id
              FROM model_hub_cell AS dataset_cell
              JOIN requested ON requested.dataset_id = dataset_cell.dataset_id
              CROSS JOIN xid_clock
            )
            SELECT
              dataset_state.updated_at,
              dataset_state.deleted,
              dataset_state.deleted_at,
              dataset_state.transaction_id,
              row_state.total_count,
              row_state.active_count,
              row_state.lifecycle_clock,
              row_state.transaction_id,
              column_state.total_count,
              column_state.active_count,
              column_state.lifecycle_clock,
              column_state.transaction_id,
              cell_state.total_count,
              cell_state.active_count,
              cell_state.lifecycle_clock,
              cell_state.transaction_id
            FROM dataset_state
            CROSS JOIN row_state
            CROSS JOIN column_state
            CROSS JOIN cell_state
            """,
            [str(dataset_id)],
        )
        state = cursor.fetchone()
    if not state:
        raise DatasetTableSnapshotUnavailable(
            "The dataset revision fingerprint is unavailable."
        )
    active_rows = int(state[5])
    if active_rows < 0:
        raise DatasetTableSnapshotUnavailable(
            "The dataset revision fingerprint is invalid."
        )
    revision = sha256(
        json.dumps(
            state,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return DatasetTableRevision(
        revision=revision,
        active_rows=active_rows,
    )


def capture_dataset_table_revision(
    *,
    dataset_id: str,
    snapshot: str,
    deadline: DatasetTableReadDeadline | None = None,
) -> DatasetTableRevision:
    # ``snapshot`` proves the caller established REPEATABLE READ before any
    # application query. Cross-request identity comes from the bounded
    # lifecycle fingerprint instead of carrying a potentially large PostgreSQL
    # xip list in the cursor.
    if not snapshot:
        raise DatasetTableSnapshotUnavailable(
            "The dataset snapshot could not be established."
        )
    return _read_revision_state(str(dataset_id), deadline=deadline)


def assert_dataset_table_revision(
    *,
    dataset_id: str,
    revision: DatasetTableRevision,
    deadline: DatasetTableReadDeadline | None = None,
) -> None:
    current = _read_revision_state(str(dataset_id), deadline=deadline)
    if current != revision:
        raise DatasetTableSnapshotChanged(
            "dataset_snapshot_changed",
            "The dataset changed while rows were loading. Restart the import.",
        )


def encode_dataset_table_cursor(
    *,
    dataset_id: str,
    organization_id: str,
    workspace_id: str | None,
    revision: DatasetTableRevision,
    page_index: int,
    page_size: int,
    seen_rows: int,
    last_order: int,
    last_id: str,
) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "dataset_id": str(dataset_id),
        "organization_id": str(organization_id),
        "workspace_id": str(workspace_id) if workspace_id else None,
        "revision": revision.revision,
        "active_rows": revision.active_rows,
        "page_index": int(page_index),
        "page_size": int(page_size),
        "seen_rows": int(seen_rows),
        "last_order": int(last_order),
        "last_id": str(last_id),
    }
    return signing.dumps(
        payload,
        key=settings.SECRET_KEY,
        salt=CURSOR_SALT,
        compress=True,
    )


def decode_dataset_table_cursor(
    token: str,
    *,
    dataset_id: str,
    organization_id: str,
    workspace_id: str | None,
    page_index: int,
    page_size: int,
) -> DatasetTableCursor:
    try:
        payload = signing.loads(
            token,
            key=settings.SECRET_KEY,
            salt=CURSOR_SALT,
            max_age=_cursor_max_age_seconds(),
        )
    except signing.SignatureExpired as exc:
        raise DatasetTableCursorError(
            "dataset_cursor_expired",
            "The dataset continuation expired. Restart the import.",
        ) from exc
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise DatasetTableCursorError(
            "invalid_dataset_cursor",
            "The dataset continuation is invalid.",
        ) from exc

    expected_scope = (
        isinstance(payload, dict)
        and payload.get("v") == CURSOR_VERSION
        and payload.get("dataset_id") == str(dataset_id)
        and payload.get("organization_id") == str(organization_id)
        and payload.get("workspace_id") == (str(workspace_id) if workspace_id else None)
        and payload.get("page_index") == int(page_index)
        and payload.get("page_size") == int(page_size)
    )
    if not expected_scope:
        raise DatasetTableCursorError(
            "dataset_cursor_mismatch",
            "The dataset continuation does not match this request.",
        )

    integer_fields = ("active_rows", "seen_rows")
    if (
        not isinstance(payload.get("last_id"), str)
        or not payload["last_id"]
        or not isinstance(payload.get("revision"), str)
        or len(payload["revision"]) != 64
        or any(character not in "0123456789abcdef" for character in payload["revision"])
        or not isinstance(payload.get("last_order"), int)
        or any(
            not isinstance(payload.get(field), int)
            or isinstance(payload[field], bool)
            or payload[field] < 0
            for field in integer_fields
        )
    ):
        raise DatasetTableCursorError(
            "invalid_dataset_cursor",
            "The dataset continuation is invalid.",
        )

    revision = DatasetTableRevision(
        revision=payload["revision"],
        active_rows=payload["active_rows"],
    )
    return DatasetTableCursor(
        revision=revision,
        page_index=payload["page_index"],
        page_size=payload["page_size"],
        seen_rows=payload["seen_rows"],
        last_order=payload["last_order"],
        last_id=payload["last_id"],
    )
