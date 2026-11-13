"""Session-bound opaque grants; no storage location leaves this service.

Only bounded blobs whose type the validation probe established are served, and
only under that validated type (``SERVED``), never the client's declaration.
``original`` resolves for every served type; ``playable`` only for audio and
video, whose original is already browser-playable. Channel, peaks and
thumbnail representations resolve to ``not_found`` until server-produced
derivatives are bound. A grant never substitutes for a current source
permission: every gateway request re-runs the full occurrence authorization.

A media session belongs to one viewer and one application session. Resolution
issues grants against the viewer's newest live media session; the cookie path
is narrowed to the gateway, so resolve never sees the cookie itself.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentBlob,
    AttachmentMediaSession,
    AttachmentPlaybackGrant,
    AttachmentState,
)
from tracer.services.attachments.accounting import audit
from tracer.services.attachments.bootstrap import LIMITS
from tracer.services.attachments.contract import AttachmentError
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
SERVED_REPRESENTATIONS = ("original", "playable")
MAX_SERVED_BYTES = LIMITS["max_upload_bytes"]


@dataclass(frozen=True)
class Served:
    """How the gateway serves one validated type.

    ``inline`` media render in place; everything else downloads under a CSP
    sandbox. ``media`` (audio/video) is rangeable and has a playable form.
    """

    filename: str
    inline: bool = True
    media: bool = False


SERVED = {
    "audio/wav": Served("recording.wav", media=True),
    "audio/ogg": Served("audio.ogg", media=True),
    "audio/mpeg": Served("audio.mp3", media=True),
    "audio/mp4": Served("audio.m4a", media=True),
    "audio/webm": Served("audio.webm", media=True),
    "audio/flac": Served("audio.flac", media=True),
    "video/mp4": Served("video.mp4", media=True),
    "video/webm": Served("video.webm", media=True),
    "image/png": Served("image.png"),
    "image/jpeg": Served("image.jpg"),
    "image/webp": Served("image.webp"),
    "application/pdf": Served("document.pdf", inline=False),
}


def servable(blob, representation):
    """The blob's serving rule, or None when it may not be served this way."""
    served = SERVED.get(blob.mime_type) if blob is not None else None
    if (
        served is None
        or representation not in SERVED_REPRESENTATIONS
        or (representation == "playable" and not served.media)
        or not blob.storage_version
        or not 0 < blob.byte_length <= MAX_SERVED_BYTES
    ):
        return None
    return served


NOT_READY = {
    AttachmentState.PENDING: "pending",
    AttachmentState.VALIDATING: "pending",
    AttachmentState.FAILED: "failed",
    AttachmentState.REJECTED: "failed",
    AttachmentState.EXPIRED: "expired",
    AttachmentState.DELETED: "deleted",
}


def digest(token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise not_found()
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def live_media_session(user_id, application_session_id):
    return (
        AttachmentMediaSession.objects.filter(
            user_id=user_id,
            application_session_id=application_session_id,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-expires_at")
        .first()
    )


@transaction.atomic
def exchange(viewer, authority):
    """Mint the opaque media cookie for a live application session."""
    if not authority.session_is_live(viewer):
        raise not_found()
    token = secrets.token_urlsafe(32)
    session = AttachmentMediaSession.objects.create(
        user_id=viewer.user_id,
        organization_id=viewer.organization_id,
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
def issue(viewer, authority, *, media_session, row, scope, expiry, representation):
    if representation not in SERVED_REPRESENTATIONS:
        raise not_found()
    if (
        media_session.user_id != viewer.user_id
        or media_session.application_session_id != viewer.application_session_id
        or media_session.organization_id != scope.organization_id
    ):
        raise not_found()
    blob = AttachmentBlob.objects.filter(attachment=row.attachment, scope=scope).first()
    if servable(blob, representation) is None:
        raise not_found()
    token = secrets.token_urlsafe(32)
    expiry = min(
        expiry,
        media_session.expires_at,
        timezone.now() + timedelta(seconds=GRANT_SECONDS),
    )
    grant = AttachmentPlaybackGrant.objects.create(
        scope=scope,
        media_session=media_session,
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


def resolve(viewer, authority, items):
    """F01 ``resolveAttachmentOccurrences``: per-item authorization, in order.

    A batch never inherits the first item's authorization. Unauthorized and
    nonexistent items are both ``not_found``.
    """
    scope_for(viewer, authority)
    media_session = live_media_session(viewer.user_id, viewer.application_session_id)
    results = []
    for item in items:
        occurrence_id = item["occurrence_id"]
        try:
            row, scope, expiry = get_occurrence(
                viewer, authority, occurrence_id, Parent.parse(item["parent"])
            )
        except AttachmentError:
            results.append({"occurrence_id": occurrence_id, "status": "not_found"})
            continue
        state = row.attachment.state
        if state != AttachmentState.READY:
            results.append(
                {
                    "occurrence_id": occurrence_id,
                    "status": NOT_READY.get(state, "not_found"),
                }
            )
            continue
        try:
            if media_session is None:
                raise not_found()
            results.append(
                issue(
                    viewer,
                    authority,
                    media_session=media_session,
                    row=row,
                    scope=scope,
                    expiry=expiry,
                    representation=item["representation"],
                )
            )
        except AttachmentError:
            results.append({"occurrence_id": occurrence_id, "status": "not_found"})
    return results


def authorize(cookie, grant_token, occurrence_id, authority):
    """Re-authorize the media session, grant, viewer and occurrence together."""
    session = AttachmentMediaSession.objects.filter(
        token_digest=digest(cookie),
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
    if session is None:
        raise not_found()
    grant = (
        AttachmentPlaybackGrant.objects.select_related("occurrence", "scope")
        .filter(
            token_digest=digest(grant_token),
            media_session=session,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if grant is None or str(grant.occurrence.client_id) != str(occurrence_id):
        raise not_found()
    scope = grant.scope
    viewer = Viewer(
        session.user_id,
        session.application_session_id,
        scope.organization_id,
        scope.workspace_id,
        scope.project_id,
    )
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
        or grant.representation not in SERVED_REPRESENTATIONS
    ):
        raise not_found()
    blob = AttachmentBlob.objects.filter(scope=scope, attachment=row.attachment).first()
    if servable(blob, grant.representation) is None:
        raise not_found()
    return grant, blob, viewer
