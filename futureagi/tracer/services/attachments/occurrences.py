"""Occurrence reads, never blob-ID reads.

The caller must supply a server-side authority adapter. No default ClickHouse or
application-session adapter exists yet: the deny adapter is intentional. These
primitives are not mounted as public endpoints until those integrations qualify.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.utils import timezone

from accounts.models import User
from tracer.models.attachment import (
    AttachmentOccurrence,
    AttachmentScope,
    AttachmentState,
)
from tracer.models.project import Project
from tracer.services.attachments.bootstrap import session_policy
from tracer.services.attachments.contract import (
    AttachmentError,
    admit_audio,
    span_id,
    trace_uuid,
)


@dataclass(frozen=True)
class Viewer:
    user_id: UUID
    application_session_id: UUID
    organization_id: UUID
    workspace_id: UUID
    project_id: UUID


@dataclass(frozen=True)
class Parent:
    trace_id: UUID
    span_id: str

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != {"trace_id", "span_id"}:
            raise AttachmentError("not_found", 404)
        try:
            return cls(trace_uuid(value["trace_id"]), span_id(value["span_id"]))
        except AttachmentError:
            raise AttachmentError("not_found", 404) from None


@dataclass(frozen=True)
class SourceState:
    """Latest retained source facts, NOT caller-supplied timestamps or OTLP data."""

    scope_id: UUID
    parent: Parent
    generation: int
    started_at: datetime
    expires_at: datetime
    readable: bool
    writable: bool = False


class Authority(Protocol):
    def session_is_live(self, viewer: Viewer) -> bool:
        """Check original app session, logout and active org/workspace/project."""
        ...

    def source(
        self, viewer: Viewer, scope: AttachmentScope, parent: Parent
    ) -> SourceState | None:
        """Collapse latest source versions/remaps/tombstones before permission check."""
        ...


class DenyAuthority:
    def session_is_live(self, viewer):
        return False

    def source(self, viewer, scope, parent):
        return None


def not_found():
    return AttachmentError("not_found", 404)


def scope_for(viewer, authority, *, write=False):
    if not getattr(
        settings, "FI_ATTACHMENTS_ENABLED", False
    ) or not authority.session_is_live(viewer):
        raise not_found()
    scope = (
        AttachmentScope.objects.select_related("workspace", "organization")
        .filter(
            project_id=viewer.project_id,
            organization_id=viewer.organization_id,
            workspace_id=viewer.workspace_id,
            enabled=True,
            workspace__is_active=True,
            workspace__organization_id=viewer.organization_id,
        )
        .first()
    )
    if (
        scope is None
        or not Project.no_workspace_objects.filter(
            pk=viewer.project_id,
            organization_id=viewer.organization_id,
            workspace_id=viewer.workspace_id,
            deleted=False,
        ).exists()
    ):
        raise not_found()
    user = User.objects.filter(pk=viewer.user_id, is_active=True).first()
    if user is None or not user.can_read_from_workspace(scope.workspace):
        raise not_found()
    if write and not user.can_write_to_workspace(scope.workspace):
        raise not_found()
    return scope


def source_for(viewer, authority, scope, parent, *, write=False):
    state = authority.source(viewer, scope, parent)
    now = timezone.now()
    if (
        state is None
        or state.scope_id != scope.id
        or state.parent != parent
        or not state.readable
        or (write and not state.writable)
        or type(state.generation) is not int
        or state.generation < 0
        or not isinstance(state.started_at, datetime)
        or not isinstance(state.expires_at, datetime)
        or timezone.is_naive(state.started_at)
        or timezone.is_naive(state.expires_at)
        or not state.started_at <= now < state.expires_at
    ):
        raise not_found()
    return state


def get_occurrence(
    viewer, authority, occurrence_id, parent, *, write=False, ready=False
):
    scope = scope_for(viewer, authority, write=write)
    state = source_for(viewer, authority, scope, parent, write=write)
    try:
        occurrence_id = UUID(str(occurrence_id))
    except (ValueError, TypeError, AttributeError):
        raise not_found() from None
    row = (
        AttachmentOccurrence.objects.select_related("attachment", "capture_session")
        .filter(
            scope=scope,
            client_id=occurrence_id,
            trace_id=parent.trace_id,
            span_id=parent.span_id,
        )
        .first()
    )
    now = timezone.now()
    if (
        row is None
        or row.deleted_at
        or not row.bound_at
        or row.expires_at <= now
        or row.source_started_at != state.started_at
        or row.source_generation != state.generation
        or row.source_expires_at is None
        or row.source_expires_at <= now
        or row.policy_version != scope.policy_version
        or row.deletion_generation != scope.deletion_generation
        or row.attachment.deleted_at
        or row.attachment.state == AttachmentState.DELETED
        or row.attachment.scope_id != scope.id
        or row.capture_session.scope_id != scope.id
        or row.attachment.policy_version != scope.policy_version
        or row.attachment.deletion_generation != scope.deletion_generation
        or row.capture_session.revoked_at
        or (ready and row.attachment.state != AttachmentState.READY)
    ):
        raise not_found()
    try:
        admit_audio(
            scope,
            session_policy(row.capture_session),
            direction=row.direction,
            source_kind=row.attachment.source_kind,
        )
    except (AttachmentError, TypeError):
        raise not_found() from None
    return row, scope, min(row.expires_at, row.source_expires_at, state.expires_at)


def metadata(row, scope, expires_at):
    return {
        "reference": {
            "attachment_id": str(row.attachment.client_id),
            "occurrence_id": str(row.client_id),
            "direction": row.direction,
            "modality": "audio",
            "mime_type": "audio/wav",
        },
        "scope": {
            "organization_id": str(scope.organization_id),
            "workspace_id": str(scope.workspace_id),
            "project_id": str(scope.project_id),
        },
        "parent": {"trace_id": str(row.trace_id), "span_id": row.span_id},
        "state": row.attachment.state,
        "binding": "bound",
        "capture_complete": row.capture_complete,
        "policy_version": str(row.policy_version),
        "deletion_generation": row.deletion_generation,
        "expires_at": expires_at.isoformat(),
    }


def resolve_metadata(viewer, authority, items):
    """Per-item authorization, without a batch-wide first-item shortcut."""
    if not isinstance(items, list) or not 1 <= len(items) <= 64:
        raise AttachmentError("invalid_request")
    results = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"occurrence_id", "parent"}:
            raise AttachmentError("invalid_request")
        try:
            row, scope, expiry = get_occurrence(
                viewer, authority, item["occurrence_id"], Parent.parse(item["parent"])
            )
            results.append(metadata(row, scope, expiry))
        except AttachmentError:
            results.append(
                {"occurrence_id": item["occurrence_id"], "status": "not_found"}
            )
    return results


def list_occurrences(
    viewer, authority, *, trace_id, parent_span_id=None, limit=50, cursor=None
):
    """Bounded scan, no total/denied counts. Cursors are scoped and signed."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise AttachmentError("invalid_request")
    scope = scope_for(viewer, authority)
    trace = trace_uuid(trace_id)
    span = span_id(parent_span_id) if parent_span_id is not None else None
    binding = [
        str(scope.id),
        str(viewer.user_id),
        str(viewer.application_session_id),
        str(trace),
        span,
        scope.policy_version,
        scope.deletion_generation,
    ]
    after = None
    if cursor:
        try:
            claims = signing.loads(cursor, salt="fi.attachments.v1.page", max_age=300)
            if claims["binding"] != binding:
                raise ValueError
            after = UUID(claims["after"])
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            raise not_found() from None
    query = AttachmentOccurrence.objects.filter(
        scope=scope, trace_id=trace, deleted_at__isnull=True
    )
    if span:
        query = query.filter(span_id=span)
    if after:
        query = query.filter(id__gt=after)
    # A cursor must refer only to an authorized item, never the last denied child.
    items, last = [], None
    candidates = list(query.order_by("id")[:501])
    for candidate in candidates[:500]:
        try:
            row, current_scope, expiry = get_occurrence(
                viewer,
                authority,
                candidate.client_id,
                Parent(candidate.trace_id, candidate.span_id),
            )
        except AttachmentError:
            continue
        if len(items) == limit:
            return {
                "items": items,
                "next_cursor": signing.dumps(
                    {"binding": binding, "after": str(last)},
                    salt="fi.attachments.v1.page",
                ),
            }
        items.append(metadata(row, current_scope, expiry))
        last = row.id
    if len(candidates) > 500:
        # Never silently report an exhausted collection when the bounded scan
        # stopped. Nor encode a denied child's identity in a continuation token.
        if last is None:
            raise AttachmentError("metadata_unavailable", 503)
        return {
            "items": items,
            "next_cursor": signing.dumps(
                {"binding": binding, "after": str(last)}, salt="fi.attachments.v1.page"
            ),
        }
    return {"items": items, "next_cursor": None}
