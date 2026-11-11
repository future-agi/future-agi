"""Session-bound opaque grants; no storage location leaves this service.

Only bounded validated PCM WAV originals/playable are implemented. Channels,
peaks, manifests, application-session lookup and HTTP routing remain independent
integration gates. A grant never substitutes for a current source permission.
"""

import hashlib
import re
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentBlob,
    AttachmentMediaSession,
    AttachmentPlaybackGrant,
)
from tracer.services.attachments.accounting import audit
from tracer.services.attachments.occurrences import (
    Parent,
    Viewer,
    get_occurrence,
    not_found,
    scope_for,
)

MEDIA_PATH = "/tracer/v1/attachment-media/"
COOKIE_NAME = "fi_media_session"
SESSION_SECONDS = 300
GRANT_SECONDS = 60


def digest(token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise not_found()
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def viewer_for(session):
    scope = session.scope
    return Viewer(
        session.user_id,
        session.application_session_id,
        scope.organization_id,
        scope.workspace_id,
        scope.project_id,
    )


def media_session(token, authority):
    session = (
        AttachmentMediaSession.objects.select_related("scope")
        .filter(
            token_digest=digest(token),
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if session is None:
        raise not_found()
    viewer = viewer_for(session)
    scope = scope_for(viewer, authority)
    if scope.id != session.scope_id:
        raise not_found()
    return session, viewer


@transaction.atomic
def exchange(viewer, authority):
    scope = scope_for(viewer, authority)
    token = secrets.token_urlsafe(32)
    session = AttachmentMediaSession.objects.create(
        scope=scope,
        user_id=viewer.user_id,
        application_session_id=viewer.application_session_id,
        token_digest=digest(token),
        expires_at=timezone.now() + timedelta(seconds=SESSION_SECONDS),
    )
    return session, token


def revoke_application_session(*, user_id, application_session_id):
    return AttachmentMediaSession.objects.filter(
        user_id=user_id,
        application_session_id=application_session_id,
        revoked_at__isnull=True,
    ).update(revoked_at=timezone.now())


@transaction.atomic
def issue(
    viewer,
    authority,
    *,
    media_session_id,
    occurrence_id,
    parent,
    representation="original",
):
    if representation not in {"original", "playable"}:
        raise not_found()
    session = (
        AttachmentMediaSession.objects.select_related("scope")
        .filter(
            pk=media_session_id,
            user_id=viewer.user_id,
            application_session_id=viewer.application_session_id,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if session is None or viewer_for(session) != viewer:
        raise not_found()
    row, scope, expiry = get_occurrence(
        viewer, authority, occurrence_id, parent, ready=True
    )
    if scope.id != session.scope_id:
        raise not_found()
    if not AttachmentBlob.objects.filter(
        attachment=row.attachment, scope=scope, mime_type="audio/wav"
    ).exists():
        raise not_found()
    token = secrets.token_urlsafe(32)
    expiry = min(
        expiry, session.expires_at, timezone.now() + timedelta(seconds=GRANT_SECONDS)
    )
    grant = AttachmentPlaybackGrant.objects.create(
        scope=scope,
        media_session=session,
        occurrence=row,
        token_digest=digest(token),
        representation=representation,
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
        source_generation=row.source_generation,
        expires_at=expiry,
    )
    audit(
        scope=scope,
        actor_id=viewer.user_id,
        entity_id=grant.id,
        event="grant",
        reason="issued",
    )
    return {
        "occurrence_id": str(row.client_id),
        "status": "ready",
        "gateway_path": f"{MEDIA_PATH}{row.client_id}/{token}",
        "expires_at": expiry.isoformat(),
    }


def authorize(cookie, grant_token, occurrence_id, authority):
    session, viewer = media_session(cookie, authority)
    grant = (
        AttachmentPlaybackGrant.objects.select_related("occurrence")
        .filter(
            token_digest=digest(grant_token),
            media_session=session,
            scope_id=session.scope_id,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if grant is None or str(grant.occurrence.client_id) != str(occurrence_id):
        raise not_found()
    original = grant.occurrence
    row, scope, _ = get_occurrence(
        viewer,
        authority,
        occurrence_id,
        Parent(original.trace_id, original.span_id),
        ready=True,
    )
    if (
        grant.policy_version != scope.policy_version
        or grant.deletion_generation != scope.deletion_generation
        or grant.source_generation != row.source_generation
        or grant.representation not in {"original", "playable"}
    ):
        raise not_found()
    blob = AttachmentBlob.objects.filter(
        scope=scope, attachment=row.attachment, mime_type="audio/wav"
    ).first()
    if (
        blob is None
        or not blob.storage_version
        or not 0 < blob.byte_length <= 8 * 1024 * 1024
    ):
        raise not_found()
    return grant, blob
