"""Bounded, eventually revisited manual assignments for live Tag populations.

Assignment edits have no telemetry arrival version. Nor is updated_at a commit
watermark: a transaction can commit after a poll's snapshot with an older time.
Instead, each cohort rotates through its project's existing assignment identity
index, at most one finite page per successful poll. A fixed high key makes each
sweep finite even while new assignments arrive. Changes behind the cursor, late
commits and telemetry excluded by a snapshot are visited in the next sweep.

The caller checkpoints only after both admission and expansion finish under its
cohort fence. Interruption retries the page; repeated classification preserves
frozen membership and uses the normal ownership/put-back paths. This is eventual,
not immediate: latency scales with assignment pages and successful polling. No
per-edit full scan, task fan-out, queue, telemetry write or new index is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING

from django.db import DatabaseError, connection, transaction

from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.eval_tasks.sampling.errors import SamplingReadPressure
from tracer.services.postgres_read_policy import application_postgres_reads

if TYPE_CHECKING:
    from tracer.models.eval_sampling import EvalSamplingCohort
    from tracer.models.eval_task import EvalTask

ASSIGNMENT_PAGE_SIZE = 64
STATE_KEY = "manual_tag_discovery"
# This order is exactly the unique_entity_tag_assignment index after project_id.
_FIELDS = "entity_kind, trace_id, span_id, start_hour, observation_type, service_name"
_KEY = f"({_FIELDS})"
_PLACEHOLDERS = "(%s, %s, %s, %s, %s, %s)"


@dataclass(frozen=True)
class AssignmentPage:
    trace_ids: tuple[str, ...]
    cursor: dict


def _encode(key) -> list | None:
    return (
        [value.isoformat() if isinstance(value, datetime) else value for value in key]
        if key
        else None
    )


def _decode(key) -> tuple | None:
    if key is None:
        return None
    return (*key[:3], datetime.fromisoformat(key[3]), *key[4:])


def _has_tag_filter(task: EvalTask) -> bool:
    filters = task.filters or {}
    return any(
        item.get("column_id") in {"tag", "tags"}
        and str((item.get("filter_config") or {}).get("col_type", "")).upper()
        != "SPAN_ATTRIBUTE"
        for name in ("filters", "span_attributes_filters")
        for item in filters.get(name) or []
    )


def assignment_page(
    task: EvalTask, policy: dict, cohort: EvalSamplingCohort
) -> AssignmentPage | None:
    """Read one primary-only indexed page; do not acknowledge or mutate it.

    All assignments are visited, including empty lists: removing a tag can make
    a negative Tag predicate eligible. Filtering on names/revision timestamps
    would lose those changes. Event windows and physical liveness are resolved
    by the caller's trace-scoped ClickHouse replay, not by this index walk.
    """
    if (
        policy["mode"] != "live"
        or task.row_type not in {"spans", "traces"}
        or not _has_tag_filter(task)
    ):
        return None
    state = (cohort.selection_context or {}).get(STATE_KEY) or {}
    if state.get("version") != 1:
        state = {}
    after, through = _decode(state.get("after")), _decode(state.get("through"))
    deadline = ReadDeadline.start(3000)
    try:
        with (
            application_postgres_reads(
                connection=connection,
                atomic=partial(transaction.atomic, using="default"),
                check_request=deadline.remaining_ms,
                statement_timeout_ms=deadline.remaining_ms,
                read_only=True,
            ),
            connection.cursor() as cursor,
        ):
            if through is None or after == through:
                after = None
                descending = ", ".join(
                    f"{field.strip()} DESC" for field in _FIELDS.split(",")
                )
                cursor.execute(
                    f"SELECT {_FIELDS} FROM tracer_entity_tag_assignment "
                    f"WHERE project_id = %s ORDER BY {descending} LIMIT 1",
                    [str(task.project_id)],
                )
                through = cursor.fetchone()
            rows = []
            if through is not None:
                lower = f" AND {_KEY} > {_PLACEHOLDERS}" if after is not None else ""
                cursor.execute(
                    f"SELECT {_FIELDS} FROM tracer_entity_tag_assignment "
                    f"WHERE project_id = %s AND {_KEY} <= {_PLACEHOLDERS}{lower} "
                    f"ORDER BY {_FIELDS} LIMIT %s",
                    [
                        str(task.project_id),
                        *through,
                        *(after or ()),
                        ASSIGNMENT_PAGE_SIZE,
                    ],
                )
                rows = cursor.fetchall()
            deadline.remaining_ms()
    except (DatabaseError, ReadDeadlineExceeded) as exc:
        raise SamplingReadPressure(
            "Sampling manual-tag discovery is temporarily unavailable. Retry shortly."
        ) from exc
    return AssignmentPage(
        trace_ids=tuple(dict.fromkeys(row[1] for row in rows)),
        cursor={
            "version": 1,
            "after": _encode(
                rows[-1] if len(rows) == ASSIGNMENT_PAGE_SIZE else through
            ),
            "through": _encode(through),
        },
    )
