"""Small shared safety contract for interactive dataset table reads.

These reads intentionally remain live/offset based.  The helper bounds their
work and gives every SQL/Python stage one wall-clock budget without claiming
the cross-request snapshot guarantees of ``dataset_table_snapshot``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from django.db import connection

from model_hub.services.dataset_read_limits import DATASET_READ_LIMITS
from model_hub.services.dataset_table_snapshot import (
    DATASET_TABLE_EXACT_MAX_CELLS,
    DATASET_TABLE_EXACT_MAX_COLUMNS,
    DATASET_TABLE_SERVER_WALL_SECONDS,
)

DATASET_INTERACTIVE_MAX_PAGE_SIZE = DATASET_READ_LIMITS.interactive_max_page_size
DATASET_INTERACTIVE_MAX_OFFSET_ROWS = DATASET_READ_LIMITS.interactive_max_offset_rows
DATASET_ROW_ADJACENCY_MAX_ROWS = DATASET_READ_LIMITS.row_adjacency_max_rows


class BoundedDatasetReadDeadlineExceeded(TimeoutError):
    """The interactive read exhausted its one server-side wall."""


class BoundedDatasetReadLimitExceeded(RuntimeError):
    """The requested projection cannot fit inside the interactive contract."""


class BoundedDatasetPageDepthExceeded(ValueError):
    """A numbered page would require an excessive database offset scan."""


@dataclass(frozen=True)
class BoundedDatasetReadDeadline:
    expires_at: float

    @classmethod
    def start(cls) -> BoundedDatasetReadDeadline:
        return cls(time.monotonic() + DATASET_TABLE_SERVER_WALL_SECONDS)

    def remaining_ms(self) -> int:
        remaining = int((self.expires_at - time.monotonic()) * 1_000)
        if remaining <= 0:
            raise BoundedDatasetReadDeadlineExceeded(
                "The dataset read deadline was exceeded."
            )
        return remaining

    def checkpoint(self) -> None:
        self.remaining_ms()

    def before_query(self) -> None:
        """Apply a transaction-local PostgreSQL timeout to the next query."""

        timeout_ms = self.remaining_ms()
        if connection.vendor != "postgresql":
            # Unit tests and local lightweight backends still get the Python
            # wall. Production PostgreSQL additionally receives statement_timeout.
            return
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [str(timeout_ms)],
            )

    # The existing bounded cell preflight accepts the exact-read deadline
    # protocol. Keep that small protocol compatible without coupling the live
    # reads to exact-snapshot semantics.
    def set_statement_timeout(self) -> None:
        self.before_query()


def assert_bounded_page(*, page_size: int, current_page_index: int) -> None:
    if page_size < 1 or page_size > DATASET_INTERACTIVE_MAX_PAGE_SIZE:
        raise BoundedDatasetReadLimitExceeded(
            f"page_size must be between 1 and {DATASET_INTERACTIVE_MAX_PAGE_SIZE}."
        )
    if current_page_index < 0:
        raise BoundedDatasetReadLimitExceeded(
            "current_page_index must be zero or greater."
        )
    if page_size * current_page_index > DATASET_INTERACTIVE_MAX_OFFSET_ROWS:
        raise BoundedDatasetPageDepthExceeded(
            "The requested page is beyond the bounded numbered-page window."
        )


def assert_bounded_projection(*, column_count: int, page_row_count: int) -> None:
    if column_count > DATASET_TABLE_EXACT_MAX_COLUMNS:
        raise BoundedDatasetReadLimitExceeded(
            f"Interactive dataset reads support at most "
            f"{DATASET_TABLE_EXACT_MAX_COLUMNS} columns."
        )
    if column_count * page_row_count > DATASET_TABLE_EXACT_MAX_CELLS:
        raise BoundedDatasetReadLimitExceeded(
            f"Interactive dataset pages support at most "
            f"{DATASET_TABLE_EXACT_MAX_CELLS} projected cells."
        )
