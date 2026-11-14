"""Authorized, revision-checked assignment writes; never write telemetry tables."""

from functools import partial
from uuid import UUID

import structlog
from clickhouse_driver.errors import Error as ClickHouseError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.request import Request

from tfc.permissions.utils import get_org_membership
from tfc.routers import force_primary
from tracer.models.entity_tag_assignment import EntityTagAssignment
from tracer.models.project import Project
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.clickhouse.v2.entity_tag_targets import (
    TagTarget,
    TagTargetError,
    resolve_tag_target,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.entity_tags import TagReadUnavailable, effective_tags
from tracer.services.postgres_read_policy import application_postgres_reads
from tracer.types.entity_tags import (
    TagTargetReference,
    TagUpdateRequest,
    TagUpdateResult,
)

logger = structlog.get_logger(__name__)
TAG_WRITE_WALL_MS = 3000


class TagAssignmentError(TagTargetError):
    STATUS_CODES = {
        **TagTargetError.STATUS_CODES,
        "tag_write_forbidden": 403,
        "tag_revision_conflict": 409,
        "tag_assignment_unavailable": 503,
    }


def _postgres_budget(deadline: ReadDeadline):
    # Primary-only, transaction-local statement ceilings also bound lock waits.
    return application_postgres_reads(
        connection=connection,
        atomic=partial(transaction.atomic, using="default"),
        check_request=deadline.remaining_ms,
        statement_timeout_ms=deadline.remaining_ms,
    )


def _can_write_to_workspace(user, workspace) -> bool:
    return bool(user.can_write_to_workspace(workspace))


def _authorize_project(request: Request, project_id: str | UUID) -> Project:
    organization = getattr(request, "organization", None) or getattr(
        request.user, "organization", None
    )
    workspace = getattr(request, "workspace", None)
    if (
        organization is None
        or workspace is None
        or str(workspace.organization_id) != str(organization.id)
    ):
        raise TagAssignmentError("tag_write_forbidden")

    # Canonical RBAC checks; a stale ambient organization may not authorize the
    # explicitly selected request organization. All these reads are primary.
    membership = get_org_membership(request.user)
    if membership is None or str(membership.organization_id) != str(organization.id):
        raise TagAssignmentError("tag_write_forbidden")
    # The request authentication's own workspace write rule: an org Admin
    # writes everywhere, anyone else by their role in this workspace. Never
    # max(org, workspace) level, which lets an org Member who is a workspace
    # Viewer through.
    if not _can_write_to_workspace(request.user, workspace):
        raise TagAssignmentError("tag_write_forbidden")

    scope = Q(workspace_id=workspace.id)
    if workspace.is_default:
        # Preserve the established default-workspace treatment of legacy rows.
        scope |= Q(workspace__isnull=True) | Q(
            workspace__is_default=True, workspace__organization_id=organization.id
        )
    try:
        return (
            Project.no_workspace_objects.using("default")
            .only("id")
            .get(
                scope,
                id=project_id,
                organization_id=organization.id,
                deleted=False,
            )
        )
    except Project.DoesNotExist:
        raise TagAssignmentError("tag_target_not_found") from None


def assignment_identity(target: TagTarget) -> dict[str, object]:
    """Exact indexed identity, shared by the write path and bounded readers."""
    fields = (
        "project_id",
        "entity_kind",
        "trace_id",
        "span_id",
        "start_hour",
        "observation_type",
        "service_name",
    )
    return dict(zip(fields, target.key, strict=True))


def _save_assignment(
    target: TagTarget, data: TagUpdateRequest, deadline: ReadDeadline
) -> int:
    identity = assignment_identity(target)
    expected = data["expected_tag_revision"]
    try:
        with _postgres_budget(deadline), transaction.atomic(using="default"):
            if expected == 0:
                EntityTagAssignment.objects.using("default").create(
                    **identity,
                    manual_tags=data["tags"],
                    revision=1,
                )
            else:
                updated = (
                    EntityTagAssignment.objects.using("default")
                    .filter(
                        **identity,
                        revision=expected,
                    )
                    .update(
                        manual_tags=data["tags"],
                        revision=expected + 1,
                        updated_at=timezone.now(),
                    )
                )
                if updated != 1:
                    raise TagAssignmentError("tag_revision_conflict")
            # Expiry before commit rolls back. Never turn a committed save into a
            # timeout by doing another dependency read or admission check after it.
            deadline.remaining_ms()
    except IntegrityError as exc:
        diagnostic = getattr(exc.__cause__, "diag", None)
        if (
            getattr(diagnostic, "constraint_name", None)
            == "unique_entity_tag_assignment"
        ):
            raise TagAssignmentError("tag_revision_conflict") from None
        raise
    return expected + 1


def update_entity_tags(
    *,
    request: Request,
    data: TagUpdateRequest,
    trace_id: str | None = None,
) -> TagUpdateResult:
    """Consume the validated scope; authorize before contacting ClickHouse.

    The exact source winner is checked before the single assignment CAS. There
    is no cross-store transaction: a later collector replay is independent and
    effective tags always union its source names with this manual assignment.
    """
    deadline = ReadDeadline.start(TAG_WRITE_WALL_MS, enforce_on_server=True)
    try:
        with force_primary(), _postgres_budget(deadline):
            project = _authorize_project(request, data["project_id"])
        reference: TagTargetReference = {
            "project_id": data["project_id"],
            "trace_id": data["trace_id"],
            "span_id": data["span_id"],
            "start_hour": data["start_hour"],
            "observation_type": data["observation_type"],
            "service_name": data["service_name"],
            "expected_start_time": data["expected_start_time"],
            "expected_version": data["expected_version"],
        }
        try:
            analytics = V2AnalyticsQueryService()
        except (ClickHouseError, OSError, TypeError, ValueError) as exc:
            # Client construction: an unreachable server or invalid connection
            # settings. The tag target is unavailable, never "not found".
            logger.warning(
                "tag_target_client_unavailable", error_type=type(exc).__name__
            )
            raise TagTargetError("tag_target_unavailable") from None
        target = resolve_tag_target(
            analytics=analytics,
            authorized_project_id=project.id,
            reference=reference,
            trace_id=trace_id,
            deadline=deadline,
        )
        # Decode source values before committing so an over-budget source cannot
        # yield an unavailable response after changing the assignment.
        effective = effective_tags(target.row, manual_tags=data["tags"])
        revision = _save_assignment(target, data, deadline)
        return {
            "id": trace_id if trace_id is not None else data["span_id"],
            **effective,
            "manual_tags": data["tags"],
            "tag_revision": revision,
        }
    except (DatabaseError, ReadDeadlineExceeded, TagReadUnavailable) as exc:
        # Never put DB diagnostics or customer tags into logs/public responses.
        logger.warning(
            "tag_assignment_write_unavailable", error_type=type(exc).__name__
        )
        raise TagAssignmentError("tag_assignment_unavailable") from None
