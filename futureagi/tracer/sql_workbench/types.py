"""Domain snapshots before transport serialization (REST and MCP share these)."""

from datetime import datetime
from typing import NotRequired, TypedDict

SQLCell = str | int | float | bool | None


class SQLColumn(TypedDict):
    name: str
    type: str


class SQLResult(TypedDict, total=False):
    columns: list[SQLColumn]
    rows: list[list[SQLCell]]
    truncated: bool
    row_count: int
    result_bytes: int
    elapsed_ms: int
    offset: int
    next_offset: int | None
    has_more: bool
    page_row_count: int
    previewed: bool
    retrieval_note: str


class ExecutionSnapshot(TypedDict):
    id: str
    project_id: str
    sql: str
    state: str
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime
    error: str
    result: SQLResult
    expired: bool
    reused: NotRequired[bool]
