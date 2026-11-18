"""Session-bound opaque grants; no storage location leaves this service.

Only bounded blobs whose type the validation probe established are served, and
only under that validated type (``SERVED``), never the client's declaration.
``original`` resolves for every served type; ``playable`` only for audio and
video, whose original is already browser-playable. Channel and peaks select
asset-owned, published derivatives using the canonical channel+index request.
Unavailable representations resolve to ``not_found``, never another version. A grant never substitutes for a current source
permission: every gateway request re-runs the full occurrence authorization.

A media session belongs to one viewer, organization and original access row.
Resolution selects only the explicit non-secret exchange handle. Independent
sessions have separate gateway cookie paths and never revoke each other.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentMediaSession,
    AttachmentPlaybackGrant,
    AttachmentState,
)
from tracer.services.attachments.accounting import audit
from tracer.services.attachments.bootstrap import LIMITS
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import (
    ObjectIdentity,
    RepresentationSelection,
)
from tracer.services.attachments.occurrences import (
    Parent,
    Viewer,
    get_occurrence,
    not_found,
    scope_for,
)
from tracer.services.attachments.wire import is_valid

MEDIA_PATH = "/tracer/v1/attachment-media/"
COOKIE_NAME = "fi_media_session_v2"
SESSION_HEADER = "X-Attachment-Media-Session"
EXPIRY_HEADER = "X-Attachment-Media-Expires-At"
# Per original access row + organization, never a global/player-count limit.
# Uses the same shared-cache, fail-closed counter pattern as gateway egress.
EXCHANGES_PER_MINUTE = 60
SESSION_SECONDS = 300
GRANT_SECONDS = 60
SERVED_REPRESENTATIONS = ("original", "playable", "channel", "peaks")
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
    "application/octet-stream": Served("attachment.bin", inline=False),
}


def servable(selection, representation):
    """Serving policy for a verified, immutable selection; never declared MIME."""
    if not isinstance(selection, RepresentationSelection):
        return None
    served = SERVED.get(selection.mime_type)
    expected = (
        "channel"
        if selection.representation in {"channel-0", "channel-1"}
        else selection.representation
    )
    if (
        served is None
        or representation not in SERVED_REPRESENTATIONS
        or representation != expected
        or (selection.mime_type == "application/octet-stream" and representation != "peaks")
        or (representation in {"playable", "channel"} and not served.media)
        or not 0 < selection.byte_length <= MAX_SERVED_BYTES
    ):
        return None
    return served


def resolve_representation(*, occurrence, representation, channel=None):
    """Metadata only. Caller MUST authorize this exact occurrence first.

    Public channel+index encoding is unchanged. Physical selectors never enter
    the request, and an unavailable derivative never falls back to the original.
    """
    if (
        representation not in SERVED_REPRESENTATIONS
        or (
            representation == "channel"
            and (type(channel) is not int or channel not in (0, 1))
        )
        or (representation != "channel" and channel is not None)
    ):
        raise not_found()
    asset = occurrence.attachment
    if (
        asset.scope_id != occurrence.scope_id
        or asset.state != AttachmentState.READY
        or asset.deleted_at
        or not asset.verified_blob_id
        or not asset.verified_at
        or not asset.storage_binding_id
    ):
        raise not_found()
    try:
        if representation in {"channel", "peaks"} or (
            representation == "playable" and asset.verified_mime_type not in SERVED
        ):
            from tracer.services.attachments.derivatives import identity_for

            selection = identity_for(
                attachment_id=asset.id,
                representation=f"channel-{channel}"
                if representation == "channel"
                else representation,
            )
        else:
            selection = RepresentationSelection(
                representation=representation,
                identity=ObjectIdentity(
                    asset.storage_binding_id,
                    "serving",
                    asset.verified_storage_key,
                    asset.verified_storage_version,
                ),
                byte_length=asset.verified_byte_length,
                mime_type=asset.verified_mime_type,
                sha256=asset.verified_sha256,
                derivative_id=None,
            )
    except (ValueError, TypeError):
        raise not_found() from None
    if servable(selection, representation) is None:
        raise not_found()
    return selection


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


def session_path(session_id):
    return f"{MEDIA_PATH}{session_id}/"


def bound_media_session(viewer, authority, session_id):
    """A selector is not a credential; require the exact original identity."""
    if not is_valid("UUID", session_id) or not authority.session_is_live(viewer):
        raise not_found()
    session = AttachmentMediaSession.objects.filter(
        pk=session_id,
        user_id=viewer.user_id,
        application_session_id=viewer.application_session_id,
        organization_id=viewer.organization_id,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
    if session is None:
        raise not_found()
    return session


def _admit_exchange(viewer):
    window = int(timezone.now().timestamp()) // 60
    key = (
        f"fi:attachments:exchange:{viewer.user_id}:"
        f"{viewer.application_session_id}:{viewer.organization_id}:{window}"
    )
    try:
        cache.add(key, 0, timeout=120)
        used = cache.incr(key)
    except Exception:  # noqa: BLE001 - shared admission fails closed
        raise AttachmentError("throttled", 429) from None
    if not isinstance(used, int) or used > EXCHANGES_PER_MINUTE:
        raise AttachmentError("throttled", 429)


@transaction.atomic
def exchange(viewer, authority):
    """Mint the opaque media cookie for a live application session."""
    if not authority.session_is_live(viewer):
        raise not_found()
    _admit_exchange(viewer)
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
def issue(
    viewer,
    authority,
    *,
    media_session,
    row,
    scope,
    expiry,
    representation,
    channel=None,
):
    media_session = bound_media_session(viewer, authority, str(media_session.id))
    if representation not in SERVED_REPRESENTATIONS:
        raise not_found()
    if (
        media_session.user_id != viewer.user_id
        or media_session.application_session_id != viewer.application_session_id
        or media_session.organization_id != scope.organization_id
    ):
        raise not_found()
    # Do not trust a caller's prefetched row/expiry as ongoing authorization.
    row, scope, current_expiry = get_occurrence(
        viewer, authority, row.client_id, Parent(row.trace_id, row.span_id), ready=True
    )
    selection = resolve_representation(
        occurrence=row, representation=representation, channel=channel
    )
    expiry = min(expiry, current_expiry)
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
        selected_channel=channel,
        selected_derivative_id=selection.derivative_id,
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
        "gateway_path": f"{session_path(media_session.id)}{row.client_id}/{token}",
        "expires_at": expiry.isoformat(),
    }


def resolve(viewer, authority, items, *, media_session_id):
    """F01 ``resolveAttachmentOccurrences``: per-item authorization, in order.

    A batch never inherits the first item's authorization. Unauthorized and
    nonexistent items are both ``not_found``.
    """
    scope_for(viewer, authority)
    media_session = bound_media_session(viewer, authority, media_session_id)
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
            results.append(
                issue(
                    viewer,
                    authority,
                    media_session=media_session,
                    row=row,
                    scope=scope,
                    expiry=expiry,
                    representation=item["representation"],
                    channel=item.get("channel"),
                )
            )
        except AttachmentError:
            results.append({"occurrence_id": occurrence_id, "status": "not_found"})
    return results


def authorize(cookie, grant_token, occurrence_id, authority, *, media_session_id):
    """Route session == cookie-derived session == grant session, on every check."""
    if not is_valid("UUID", media_session_id):
        raise not_found()
    session = AttachmentMediaSession.objects.filter(
        pk=media_session_id,
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
    if session.organization_id != scope.organization_id:
        raise not_found()
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
    if row.id != grant.occurrence_id or row.scope_id != grant.scope_id:
        raise not_found()
    selection = resolve_representation(
        occurrence=row,
        representation=grant.representation,
        channel=grant.selected_channel,
    )
    if selection.derivative_id != grant.selected_derivative_id:
        raise not_found()
    return grant, selection, viewer
