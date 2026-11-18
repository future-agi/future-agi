"""Recording manifests: written by a segment capability, read by a viewer.

Write (F01 ``updateRecordingManifest``): the capability must carry this
``recording_id`` and the ``complete`` operation. It stays valid for the
manifest after its own segment's ``complete`` until the capability expires
(BR-7). Every segment must be a live upload of THIS recording from the same
capture session, at its own index, so a manifest can never relink foreign
occurrences. Manifests are prefix-monotonic. The same key with the same body
replays as success; the same key with a different body conflicts (BR-5).

Read (F01 ``readRecordingManifest``): the viewer must be able to read the
recording's exact parent and EVERY referenced occurrence. A denied/missing
segment denies the whole manifest; only persisted capture gaps are returned.
"""

from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentManifestRevision,
    AttachmentOccurrence,
    AttachmentRecording,
    AttachmentScope,
    AttachmentState,
    AttachmentUpload,
    CaptureSession,
)
from tracer.services.attachments.bootstrap import authorize, check_session
from tracer.services.attachments.contract import (
    AttachmentError,
    fingerprint,
)
from tracer.services.attachments.occurrences import (
    Parent,
    check_readable,
    not_found,
    scope_for,
    source_for,
)

MANIFEST_UPLOAD_STATES = (
    AttachmentState.PENDING,
    AttachmentState.VALIDATING,
    AttachmentState.READY,
)


def _prefix(previous, current):
    """A later revision only appends: earlier entries never change or vanish."""
    if not previous:
        return True
    if previous.get("capture_complete") and previous != current:
        return False
    for field in (
        "timebase",
        "sample_format",
        "sample_rate_hz",
        "channels",
        "codec",
        "container",
        "layout",
        "tracks",
    ):
        if previous[field] != current[field]:
            return False
    for field in ("segments", "gaps", "playout"):
        if current[field][: len(previous[field])] != previous[field]:
            return False
    return current["duration_ms"] >= previous["duration_ms"]


def put_manifest(token, *, scope_id, recording_id, body, key):
    """``body`` is a RecordingManifest already validated by ``wire.validate``."""
    session, claims = authorize(
        token, operation="complete", scope_id=scope_id, recording_id=recording_id
    )
    if body["recording_id"] != str(recording_id):
        raise AttachmentError("invalid_request")
    digest = fingerprint(body)
    with transaction.atomic():
        scope = AttachmentScope.objects.select_for_update().get(pk=scope_id)
        session = CaptureSession.objects.select_for_update().get(pk=session.id)
        check_session(session)
        recording = (
            AttachmentRecording.objects.select_for_update()
            .filter(
                scope=scope,
                client_id=UUID(str(recording_id)),
                capture_session=session,
                deleted_at__isnull=True,
            )
            .first()
        )
        if recording is None:
            raise AttachmentError("not_found", 404)
        owner = (
            AttachmentUpload.objects.select_for_update()
            .select_related("occurrence")
            .filter(
                pk=UUID(claims["upload_id"]),
                scope=scope,
                capture_session=session,
                recording=recording,
            )
            .first()
        )
        if (
            owner is None
            or owner.producer_kind != "authenticated_stream"
            or owner.state not in MANIFEST_UPLOAD_STATES
            or owner.occurrence.deleted_at
            or owner.deletion_generation != scope.deletion_generation
        ):
            raise AttachmentError("deleted", 409)
        accepted = AttachmentManifestRevision.objects.filter(
            recording=recording, idempotency_key=key
        ).first()
        if accepted is not None:
            if accepted.fingerprint != digest:
                raise AttachmentError("manifest_conflict", 409)
            return recording
        uploads = {
            str(row.occurrence.client_id): row
            for row in AttachmentUpload.objects.select_related("occurrence").filter(
                scope=scope,
                recording=recording,
                capture_session=session,
                state__in=MANIFEST_UPLOAD_STATES,
            )
        }
        for segment in body["segments"]:
            row = uploads.get(segment["occurrence_id"])
            if (
                row is None
                or row.segment_index != segment["index"]
                or row.occurrence.deleted_at
            ):
                raise AttachmentError("manifest_conflict", 409)
        if not _prefix(recording.manifest, body):
            raise AttachmentError("manifest_conflict", 409)
        revision = recording.manifest_revision + 1
        try:
            with transaction.atomic():
                AttachmentManifestRevision.objects.create(
                    scope=scope,
                    recording=recording,
                    idempotency_key=key,
                    fingerprint=digest,
                    revision=revision,
                )
        except IntegrityError:
            raise AttachmentError("manifest_conflict", 409) from None
        recording.manifest = body
        recording.manifest_revision = revision
        recording.duration_ms = body["duration_ms"]
        recording.capture_complete = body["capture_complete"]
        recording.published_at = timezone.now()
        recording.save(
            update_fields=[
                "manifest",
                "manifest_revision",
                "duration_ms",
                "capture_complete",
                "published_at",
            ]
        )
        return recording


def read_manifest(viewer, authority, *, recording_id, parent):
    scope = scope_for(viewer, authority)
    parent = Parent.parse(parent)
    source = source_for(viewer, authority, scope, parent)
    try:
        recording_id = UUID(str(recording_id))
    except (ValueError, TypeError, AttributeError):
        raise not_found() from None
    recording = AttachmentRecording.objects.filter(
        scope=scope,
        client_id=recording_id,
        trace_id=parent.trace_id,
        span_id=parent.span_id,
        deleted_at__isnull=True,
        deletion_generation=scope.deletion_generation,
        manifest__isnull=False,
    ).first()
    if recording is None or recording.manifest is None:
        raise not_found()
    manifest = dict(recording.manifest)
    # The manifest is a complete authorization unit, not the 64-item metadata
    # page. Fetch its exact recording/parent occurrences in one bounded query;
    # the persisted F01 manifest bounds the requested IDs. Never repair an
    # authorization failure by dropping/reindexing segments or inventing gaps.
    try:
        ids = [UUID(segment["occurrence_id"]) for segment in manifest["segments"]]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise not_found() from None
    rows = {
        row.client_id: row
        for row in AttachmentOccurrence.objects.select_related(
            "attachment", "capture_session"
        ).filter(
            scope=scope,
            recording=recording,
            trace_id=parent.trace_id,
            span_id=parent.span_id,
            client_id__in=ids,
        )
    }
    now = timezone.now()
    for occurrence_id in ids:
        check_readable(rows.get(occurrence_id), scope, source, now, ready=True)
    return manifest
