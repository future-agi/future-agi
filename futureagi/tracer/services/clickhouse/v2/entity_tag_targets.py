"""Resolve a tag mutation against an authorized, complete CH25 physical row.

This module does not authorize projects or write either database. The caller
must resolve the non-deleted Project within the current organization/workspace
and enforce its write role *before* calling it. A supplied reference is a stale
selection check, not a capability or an authorization credential.

Span labels intentionally follow the physical six-key identity. A correction
within its hour preserves the key; a service/type/hour correction does not carry
an assignment to a different selectable row. Root edits alias the logical trace
assignment. They never attach a trace label to every descendant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

import structlog

from tracer.services.clickhouse.read_budget import ReadDeadline, WallCappedAnalytics
from tracer.services.clickhouse.v2.physical_span_detail_reads import (
    PhysicalSpanDetailError,
    read_physical_span_detail,
)
from tracer.types.entity_tags import TagAssignmentKey, TagTargetReference, TagTargetRow

if TYPE_CHECKING:
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

logger = structlog.get_logger(__name__)

TRACE_ASSIGNMENT_HOUR = datetime(1970, 1, 1, tzinfo=UTC)


class TagTargetError(RuntimeError):
    STATUS_CODES = {
        "tag_scope_required": 400,
        "tag_target_not_found": 404,
        "tag_target_changed": 409,
        "tag_target_unavailable": 503,
    }

    def __init__(self, code: str):
        self.code = code
        self.status_code = self.STATUS_CODES[code]
        super().__init__(code)


@dataclass(frozen=True)
class TagTarget:
    key: TagAssignmentKey
    row: TagTargetRow

    @property
    def project_id(self) -> str:
        return self.key[0]


def resolve_tag_target(
    *,
    analytics: AnalyticsQueryService,
    authorized_project_id: str | UUID,
    reference: TagTargetReference,
    trace_id: str | UUID | None,
    deadline: ReadDeadline,
) -> TagTarget:
    """Complete winner first; only then liveness, selected revision and root role.

    ``reference`` must already be validated by the mutation contract. The cap is
    an actual ``server_execution_cap_ms``, not the legacy timeout hint. Locked
    profiles are unavailable until separately qualified; never silently run an
    uncapped write-precondition read on such a connection.
    """
    if str(reference["project_id"]) != str(authorized_project_id):
        raise TagTargetError("tag_target_not_found")
    if trace_id is not None and str(trace_id) != reference["trace_id"]:
        raise TagTargetError("tag_scope_required")
    try:
        if getattr(analytics, "supports_per_query_read_settings", None) is not True:
            raise TagTargetError("tag_target_unavailable")
        try:
            row = read_physical_span_detail(
                analytics=WallCappedAnalytics(analytics, deadline, floor_ms=25),
                span_id=reference["span_id"],
                reference=reference,
                authorized_project_id=authorized_project_id,
            )
        finally:
            # Expired reads cannot attest absence or a changed target either.
            deadline.remaining_ms(floor_ms=25)
    except PhysicalSpanDetailError as exc:
        code = {
            "span_reference_not_found": "tag_target_not_found",
            "span_reference_changed": "tag_target_changed",
        }.get(exc.code, "tag_target_unavailable")
        raise TagTargetError(code) from None
    except TagTargetError:
        raise
    except Exception as exc:
        # Fail closed, keeping the stack but not a driver's payload/diagnostic.
        unavailable = TagTargetError("tag_target_unavailable")
        logger.exception(
            "tag_target_read_unavailable",
            error_type=type(exc).__name__,
            exc_info=(TagTargetError, unavailable, exc.__traceback__),
        )
        raise unavailable from None

    if "parent_span_id" not in row or not isinstance(
        row["parent_span_id"], str | type(None)
    ):
        raise TagTargetError("tag_target_unavailable")
    row = cast(TagTargetRow, row)
    root = row["parent_span_id"] in (None, "")
    if trace_id is not None and not root:
        raise TagTargetError("tag_target_changed")
    if root:
        key: TagAssignmentKey = (
            str(authorized_project_id),
            "trace",
            row["trace_id"],
            "",
            TRACE_ASSIGNMENT_HOUR,
            "",
            "",
        )
    else:
        key = (
            str(authorized_project_id),
            "span",
            row["trace_id"],
            row["id"],
            row["start_hour"],
            row["observation_type"],
            row["service_name"],
        )
    return TagTarget(key=key, row=row)
