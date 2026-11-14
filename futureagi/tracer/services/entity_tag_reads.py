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
from tracer.types.entity_tags import TagAssignmentKey, TagSourceRow

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


def attach_legacy_trace_tags(
    execute,
    rows: list[dict[str, Any]],
    *,
    deadline: ReadDeadline | None = None,
) -> None:
    """Give root rows the curated ``traces.tags`` the trace list shows.

    Tags saved by the previous editor live only there. Every reader that
    enriches roots must see them, or a save from that surface (which then
    suppresses the legacy fallback) silently drops them. Uses the same latest
    live ``traces`` row as the trace list and the effective-tag filter.
    """
    from tracer.services.clickhouse.read_budget import (
        is_clickhouse_api_read_unavailable_error,
    )

    roots = [
        row
        for row in rows
        # Exactly the rows tag_assignment_key treats as the trace's root.
        if "parent_span_id" in row
        and row["parent_span_id"] in (None, "")
        # Content merges default this to an empty list; only a populated value
        # (the trace list's own CH join, trace detail's PG row) is a source.
        and row.get("trace_tags") in (None, "", "[]", [])
        and row.get("project_id")
        and row.get("trace_id")
    ]
    if not roots:
        return
    if len(roots) > MAX_TAG_READ_ROWS:
        raise TagReadUnavailable("tag_candidate_budget_exceeded")
    deadline = deadline or ReadDeadline.start(TAG_READ_WALL_MS, enforce_on_server=True)
    projects = tuple(sorted({str(row["project_id"]) for row in roots}))
    traces = tuple(sorted({str(row["trace_id"]) for row in roots}))
    try:
        result = execute(
            """
            SELECT toString(project_id) AS project_id, toString(id) AS trace_id,
                legacy.1 AS tags
            FROM (
                SELECT project_id, id,
                    argMax(tuple(tags, is_deleted), _version) AS legacy
                FROM traces
                WHERE project_id IN %(legacy_projects)s
                  AND id IN %(legacy_traces)s
                GROUP BY project_id, id
            )
            WHERE legacy.2 = 0
            """,
            {"legacy_projects": projects, "legacy_traces": traces},
            timeout_ms=deadline.remaining_ms(),
            settings={"max_result_rows": len(traces) * len(projects) + 1},
        )
    except ReadDeadlineExceeded:
        raise TagReadUnavailable("tag_legacy_unavailable") from None
    except Exception as exc:
        if is_clickhouse_api_read_unavailable_error(exc):
            raise TagReadUnavailable("tag_legacy_unavailable") from None
        raise
    legacy = {
        (str(row["project_id"]), str(row["trace_id"])): row["tags"]
        for row in result.data or []
    }
    for row in roots:
        row["trace_tags"] = legacy.get(
            (str(row["project_id"]), str(row["trace_id"])), "[]"
        )


def tag_fields(row: dict[str, Any]) -> dict[str, Any]:
    """The same editable tag contract on detail, drawer and table rows."""
    return {key: row[key] for key in _TAG_FIELDS}


# The tag editor's write contract on a list row, not exported data.
_TAG_EDITOR_FIELDS = ("source_tag_names", "manual_tags", "tag_revision", "tag_target")


def tag_export_row(
    row: dict[str, Any], *, drop: tuple[str, ...] = ()
) -> dict[str, Any]:
    """A list row as a CSV export shows it: tag names only, no editor fields."""
    exported = {
        key: value
        for key, value in row.items()
        if key not in _TAG_EDITOR_FIELDS and key not in drop
    }
    tags = exported.get("tags")
    if isinstance(tags, list):
        exported["tags"] = [
            tag.get("name") if isinstance(tag, dict) else tag for tag in tags
        ]
    return exported


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
                    record_key = tuple(
                        str(record[field]) if field == "project_id" else record[field]
                        for field in _IDENTITY_FIELDS
                    )
                    if record_key not in batch or record_key in assignments:
                        raise TagReadUnavailable("tag_assignment_identity_mismatch")
                    assignments[record_key] = record
                deadline.remaining_ms()
        enriched: list[dict[str, Any]] = []
        payload_bytes = 0
        for row, key in zip(rows, keys, strict=True):
            deadline.remaining_ms()
            assignment = assignments.get(key)
            manual = assignment["manual_tags"] if assignment is not None else None
            target = _tag_target(row, kind=kind)
            source: TagSourceRow = {
                "tags": row.get("tags"),
                "attrs_string": row.get("attrs_string"),
                "attributes_extra": row.get("attributes_extra"),
                "span_attributes": row.get("span_attributes"),
            }
            try:
                values = effective_tags(
                    source, manual_tags=manual, legacy_tags=row.get("trace_tags")
                )
            except TagReadUnavailable as exc:
                if exc.code != "tag_source_budget_exceeded":
                    raise
                # One tag value of this row alone is over the source budget.
                # Keep the response and every other row; this row shows what
                # can be decoded and is read-only, so no save is ever built
                # from an incomplete view.
                values = effective_tags(
                    source,
                    manual_tags=manual,
                    legacy_tags=row.get("trace_tags"),
                    skip_oversized=True,
                )
                target = None
            enriched.append(
                {
                    **values,
                    "manual_tags": manual if manual is not None else [],
                    "tag_revision": assignment["revision"]
                    if assignment is not None
                    else 0,
                    "tag_target": target,
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
    for row, updates in zip(rows, enriched, strict=True):
        row.update(updates)
