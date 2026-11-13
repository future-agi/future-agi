"""Occurrence reads, never blob-ID reads.

The caller supplies a server-side authority adapter: ``authority.LiveAuthority``
(current application session plus ClickHouse source state) in the HTTP layer,
``DenyAuthority`` everywhere else. No request field constructs a Viewer.
"""

import base64
import hmac
import json
import struct
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from accounts.models import User
from tracer.models.attachment import (
    AttachmentOccurrence,
    AttachmentScope,
    AttachmentState,
)
from tracer.models.project import Project
from tracer.services.attachments.bootstrap import session_policy
from tracer.services.attachments.contract import (
    PLACEMENT_FIELDS,
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
    # None only for the media-session exchange, which carries no project.
    workspace_id: UUID | None
    project_id: UUID | None


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
    if viewer.project_id is None or viewer.workspace_id is None:
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


def check_readable(row, scope, state, now, *, ready=False):
    """Raise not_found unless ``row`` is readable under ``scope`` and ``state``.

    The one per-row gate behind every occurrence read: ``get_occurrence`` and
    the batched trace/Voice reader (``control_plane.list_authorized``) both
    call it, so a prefetched row is authorized exactly like a fetched one.
    ``row`` needs ``attachment`` and ``capture_session`` loaded.
    """
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
    if row is None:
        raise not_found()
    check_readable(row, scope, state, timezone.now(), ready=ready)
    return row, scope, min(row.expires_at, row.source_expires_at, state.expires_at)


def metadata(row, scope, expires_at):
    reference = {
        "attachment_id": str(row.attachment.client_id),
        "occurrence_id": str(row.client_id),
        "direction": row.direction,
        "modality": row.attachment.modality,
        "mime_type": row.attachment.mime_type,
    }
    if row.purpose:
        reference["purpose"] = row.purpose
    for name in PLACEMENT_FIELDS:
        value = getattr(row, name)
        if value is not None:
            reference[name] = value
    document = {
        "reference": reference,
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
    if row.recording_id:
        document["recording_id"] = str(row.recording.client_id)
    return document


CURSOR_SALT = "fi.attachments.v1.page"
CURSOR_SECONDS = 300


def _cursor_mac(binding, after, issued):
    message = json.dumps([binding, after.hex, issued], separators=(",", ":"))
    return salted_hmac(
        CURSOR_SALT, message, secret=settings.SECRET_KEY, algorithm="sha256"
    ).digest()[:16]


def encode_cursor(binding, after):
    """F01 ``Token``-shaped (``[a-zA-Z0-9_-]``, 48 chars) scoped signed cursor."""
    issued = int(time.time())
    raw = after.bytes + struct.pack(">I", issued) + _cursor_mac(binding, after, issued)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(binding, cursor):
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        if len(raw) != 36:
            raise ValueError
        after = UUID(bytes=raw[:16])
        (issued,) = struct.unpack(">I", raw[16:20])
        if not hmac.compare_digest(raw[20:], _cursor_mac(binding, after, issued)):
            raise ValueError
        if not 0 <= time.time() - issued <= CURSOR_SECONDS:
            raise ValueError
        return after
    except (ValueError, TypeError, struct.error):
        raise not_found() from None


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
    after = decode_cursor(binding, cursor) if cursor else None
    query = AttachmentOccurrence.objects.filter(
        scope=scope, trace_id=trace, deleted_at__isnull=True
    )
    if span:
        query = query.filter(span_id=span)
    if after:
        query = query.filter(id__gt=after)
    # A cursor must refer only to an authorized item, never the last denied child.
    items: list[dict[str, Any]]
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
            return {"items": items, "next_cursor": encode_cursor(binding, last)}
        items.append(metadata(row, current_scope, expiry))
        last = row.id
    if len(candidates) > 500:
        # Never silently report an exhausted collection when the bounded scan
        # stopped. Nor encode a denied child's identity in a continuation token.
        if last is None:
            raise AttachmentError("metadata_unavailable", 503)
        return {"items": items, "next_cursor": encode_cursor(binding, last)}
    return {"items": items, "next_cursor": None}


@transaction.atomic
def link(viewer, authority, body):
    """F01 ``linkAttachmentOccurrence``: a new occurrence of readable bytes.

    Requires ordinary scoped authority over BOTH the readable source occurrence
    and the writable target parent. The new occurrence inherits the source's
    attachment, session and direction, and is bound to the target's retained
    source state; it never extends the source's expiry.
    """
    source_parent = Parent.parse(body["source_parent"])
    target_parent = Parent.parse(body["target_parent"])
    row, scope, source_expiry = get_occurrence(
        viewer, authority, body["source_occurrence_id"], source_parent
    )
    scope = AttachmentScope.objects.select_for_update().get(pk=scope.id)
    if (
        body["policy_version"] != str(scope.policy_version)
        or body["deletion_generation"] != scope.deletion_generation
    ):
        raise AttachmentError("expired", 409)
    scope_for(viewer, authority, write=True)
    target = source_for(viewer, authority, scope, target_parent, write=True)
    occurrence_id = UUID(body["occurrence_id"])
    existing = AttachmentOccurrence.objects.filter(
        scope=scope, client_id=occurrence_id
    ).first()
    if existing is not None:
        if (
            existing.attachment_id != row.attachment_id
            or (existing.trace_id, existing.span_id)
            != (target_parent.trace_id, target_parent.span_id)
            or existing.deleted_at
        ):
            raise AttachmentError("idempotency_conflict", 409)
        created = existing
    else:
        try:
            with transaction.atomic():
                created = AttachmentOccurrence.objects.create(
                    scope=scope,
                    client_id=occurrence_id,
                    attachment=row.attachment,
                    capture_session=row.capture_session,
                    trace_id=target_parent.trace_id,
                    span_id=target_parent.span_id,
                    direction=row.direction,
                    purpose=row.purpose,
                    # LinkRequest carries no placement: the new occurrence is the
                    # same content in the same role, so it inherits the source's.
                    **{name: getattr(row, name) for name in PLACEMENT_FIELDS},
                    policy_version=scope.policy_version,
                    deletion_generation=scope.deletion_generation,
                    bound_at=timezone.now(),
                    source_started_at=target.started_at,
                    source_expires_at=target.expires_at,
                    source_generation=target.generation,
                    expires_at=min(source_expiry, target.expires_at),
                    capture_complete=row.capture_complete,
                )
        except IntegrityError:
            raise AttachmentError("idempotency_conflict", 409) from None
    linked, current_scope, expiry = get_occurrence(
        viewer, authority, created.client_id, target_parent
    )
    return metadata(linked, current_scope, expiry)
