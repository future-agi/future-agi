"""Enrich already-authorized, bounded CH candidates with primary-only assignments.

Never enumerate assignments by project or query telemetry again. The caller must
pass complete live source rows from its bounded CH read. Roots share the logical
trace assignment; children use all six physical coordinates and never inherit it.
"""

import json
from datetime import UTC, datetime
from functools import partial
from typing import Any, Literal

from django.db import DatabaseError, connection, transaction
from django.db.models import Q

from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.clickhouse.v2.entity_tag_targets import TRACE_ASSIGNMENT_HOUR
from tracer.services.entity_tags import TagReadUnavailable, effective_tags
from tracer.services.postgres_read_policy import application_postgres_reads
from tracer.types.entity_tags import TagAssignmentKey

TAG_READ_BATCH_SIZE = 200
MAX_TAG_READ_ROWS = 4096
MAX_TAG_READ_BYTES = 8 * 1024 * 1024
TAG_READ_WALL_MS = 3000
_IDENTITY_FIELDS = (
    "project_id",
    "entity_kind",
    "trace_id",
    "span_id",
    "start_hour",
    "observation_type",
    "service_name",
)
_TAG_FIELDS = ("tags", "source_tag_names", "manual_tags", "tag_revision", "tag_target")


def tag_assignment_key(
    row: dict[str, Any], *, kind: Literal["trace", "span"]
) -> TagAssignmentKey:
    project, trace = row.get("project_id"), row.get("trace_id")
    if not project or not trace:
        raise TagReadUnavailable("tag_identity_incomplete")
    if kind == "trace" or (
        "parent_span_id" in row and row["parent_span_id"] in (None, "")
    ):
        return (str(project), "trace", str(trace), "", TRACE_ASSIGNMENT_HOUR, "", "")
    start = row.get("start_hour") or row.get("start_time")
    if (
        not isinstance(start, datetime)
        or not row.get("id")
        or row.get("observation_type") is None
        or row.get("service_name") is None
        or "parent_span_id" not in row
    ):
        raise TagReadUnavailable("tag_identity_incomplete")
    # clickhouse-driver can return naive UTC for DateTime64 columns.
    start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    return (
        str(project),
        "span",
        str(trace),
        str(row["id"]),
        start.replace(minute=0, second=0, microsecond=0),
        str(row["observation_type"]),
        str(row["service_name"]),
    )


def _tag_target(
    row: dict[str, Any], *, kind: Literal["trace", "span"]
) -> dict[str, Any] | None:
    """Project the selected source winner, never reconstruct a target in the UI.

    A source without its physical revision remains readable but not editable.
    This reference is a selection hint; the write API still authorizes the project
    and re-verifies the complete live winner before committing an assignment.
    """
    prefix = "_root_" if kind == "trace" else ""
    start = row.get("start_time")
    span = row.get("root_span_id" if kind == "trace" else "id")
    observation_type = row.get(prefix + "observation_type")
    service = row.get(prefix + "service_name")
    version = row.get("_root_version" if kind == "trace" else "_version")
    if (
        not isinstance(start, datetime)
        or not span
        or observation_type is None
        or service is None
        or version is None
    ):
        return None
    start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    return {
        "project_id": str(row["project_id"]),
        "trace_id": str(row["trace_id"]),
        "span_id": str(span),
        "start_hour": start.replace(minute=0, second=0, microsecond=0).isoformat(),
        "observation_type": str(observation_type),
        "service_name": str(service),
        "expected_start_time": start.isoformat(timespec="microseconds"),
        "expected_version": str(version),
    }


def tag_fields(row: dict[str, Any]) -> dict[str, Any]:
    """The same editable tag contract on detail, drawer and table rows."""
    return {key: row[key] for key in _TAG_FIELDS}


def enrich_entity_tags(
    rows: list[dict[str, Any]],
    *,
    kind: Literal["trace", "span"],
    deadline: ReadDeadline | None = None,
) -> None:
    """Attach effective tags atomically, in finite exact-key PG batches.

    An empty saved assignment remains distinguishable from absence. A failed or
    incomplete read must fail the response, not silently drop the user's labels.
    No mutation of the input occurs until every batch and source value succeeds.
    """
    if len(rows) > MAX_TAG_READ_ROWS:
        raise TagReadUnavailable("tag_candidate_budget_exceeded")
    if not rows:
        return
    deadline = deadline or ReadDeadline.start(TAG_READ_WALL_MS, enforce_on_server=True)
    keys = [tag_assignment_key(row, kind=kind) for row in rows]
    unique_keys = list(dict.fromkeys(keys))
    assignments = {}
    try:
        with application_postgres_reads(
            connection=connection,
            atomic=partial(transaction.atomic, using="default"),
            check_request=deadline.remaining_ms,
            statement_timeout_ms=deadline.remaining_ms,
        ):
            for offset in range(0, len(unique_keys), TAG_READ_BATCH_SIZE):
                batch = unique_keys[offset : offset + TAG_READ_BATCH_SIZE]
                predicate = Q()
                for key in batch:
                    predicate |= Q(**dict(zip(_IDENTITY_FIELDS, key, strict=True)))
                records = list(
                    EntityTagAssignment.objects.using("default")
                    .filter(predicate)
                    .values(*_IDENTITY_FIELDS, "manual_tags", "revision")[
                        : len(batch) + 1
                    ]
                )
                if len(records) > len(batch):
                    raise TagReadUnavailable("tag_assignment_budget_exceeded")
                for record in records:
                    key = tuple(
                        str(record[field]) if field == "project_id" else record[field]
                        for field in _IDENTITY_FIELDS
                    )
                    if key not in batch or key in assignments:
                        raise TagReadUnavailable("tag_assignment_identity_mismatch")
                    assignments[key] = record
                deadline.remaining_ms()
        enriched = []
        payload_bytes = 0
        for row, key in zip(rows, keys, strict=True):
            deadline.remaining_ms()
            assignment = assignments.get(key)
            manual = assignment["manual_tags"] if assignment is not None else None
            enriched.append(
                {
                    **effective_tags(
                        row, manual_tags=manual, legacy_tags=row.get("trace_tags")
                    ),
                    "manual_tags": manual if manual is not None else [],
                    "tag_revision": assignment["revision"]
                    if assignment is not None
                    else 0,
                    "tag_target": _tag_target(row, kind=kind),
                }
            )
            payload_bytes += len(
                json.dumps(enriched[-1], ensure_ascii=True).encode("utf-8")
            )
            if payload_bytes > MAX_TAG_READ_BYTES:
                raise TagReadUnavailable("tag_payload_budget_exceeded")
        deadline.remaining_ms()
    except (DatabaseError, ReadDeadlineExceeded):
        raise TagReadUnavailable("tag_assignment_unavailable") from None
    for row, values in zip(rows, enriched, strict=True):
        row.update(values)
