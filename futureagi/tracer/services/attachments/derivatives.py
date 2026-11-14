"""Server-side channel derivatives and bounded waveform peaks (B11).

Each published segment of a stereo recording gets two independently playable
mono WAV derivatives (``channel-0``, ``channel-1``) and every segment gets a
``peaks`` pyramid. Work is per segment (at most 30 s / 8 MiB), streamed in the
seccomp sandbox, so a two-hour recording never needs unbounded concatenation
or a whole-file browser decode.

Derivatives are immutable, server-keyed (deterministic ids, so the deletion
path can always find them) and inherit the segment occurrence's authority,
policy and expiry: publication is fenced exactly like segment publication, and
``identity_for`` serves nothing once the source occurrence is gone.
"""

import hashlib
import os
import tempfile
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from tracer.models.attachment import AttachmentOccurrence, AttachmentScope
from tracer.models.attachment_recording import (
    AttachmentRecording,
    AttachmentRecordingDerivative,
    AttachmentRecordingSegment,
    RecordingSegmentState,
    RecordingState,
)
from tracer.services.attachments import sandbox
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.storage import ObjectIdentity

HEADER_BYTES = 44
PEAK_LEVEL_FACTORS = (1, 10, 100)
REPRESENTATIONS = ("channel-0", "channel-1", "peaks")


def derivative_id(segment, representation):
    """Deterministic, so erasure of a segment can always find its derivatives."""
    return uuid.uuid5(segment.attachment_client_id, f"derivative:{representation}")


def peaks_bytes(*, frame_count, rate, channels):
    """Exact peaks output size; bounded by the 30 s segment ceiling."""
    base = max(1, rate * 10 // 1000)
    buckets = -(-frame_count // base)
    total = 0
    for factor in PEAK_LEVEL_FACTORS:
        total += -(-buckets // factor) * channels * 4
    return total


def _work_dir():
    return getattr(settings, "FI_ATTACHMENTS_WORK_DIR", "") or tempfile.gettempdir()


def _live(segment):
    """Segment occurrence alive, recording ready, fences unchanged."""
    scope = AttachmentScope.objects.get(pk=segment.scope_id)
    recording = AttachmentRecording.objects.get(pk=segment.recording_id)
    occurrence = AttachmentOccurrence.objects.filter(
        scope=scope, client_id=segment.occurrence_client_id
    ).first()
    now = timezone.now()
    if (
        not scope.enabled
        or recording.state != RecordingState.READY
        or recording.deleted_at
        or recording.deletion_generation != scope.deletion_generation
        or segment.state != RecordingSegmentState.PUBLISHED
        or occurrence is None
        or occurrence.deleted_at
        or occurrence.expires_at <= now
        or (occurrence.source_expires_at and occurrence.source_expires_at <= now)
        or occurrence.deletion_generation != scope.deletion_generation
        or occurrence.policy_version != scope.policy_version
    ):
        raise AttachmentError("validation_fenced", 409)
    return scope, recording, occurrence


def _copy_segment(segment, storage, output):
    """Bounded read of the exact immutable serving version, hash-verified."""
    identity = ObjectIdentity(segment.storage_key, segment.storage_version)
    body = storage.open_range(
        identity,
        start=0,
        end=segment.byte_length - 1,
        expected_length=segment.byte_length,
    )
    digest, size = hashlib.sha256(), 0
    try:
        while True:
            chunk = body.read(min(256 * 1024, segment.byte_length - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > segment.byte_length:
                raise AttachmentError("snapshot_changed", 409)
            digest.update(chunk)
            output.write(chunk)
    finally:
        body.close()
    output.flush()
    if size != segment.byte_length or digest.hexdigest() != segment.sha256:
        raise AttachmentError("snapshot_changed", 409)


def generate(segment_id, *, scope_id, storage):
    """Produce and publish every missing derivative of one published segment."""
    segment = AttachmentRecordingSegment.objects.get(pk=segment_id, scope_id=scope_id)
    _, recording, _ = _live(segment)
    wanted = ["peaks"]
    if recording.channels == 2:
        wanted = ["channel-0", "channel-1", "peaks"]
    done = set(
        AttachmentRecordingDerivative.objects.filter(
            segment=segment, published_at__isnull=False
        ).values_list("representation", flat=True)
    )
    published = []
    if not set(wanted) <= done:
        with tempfile.NamedTemporaryFile(dir=_work_dir()) as source:
            _copy_segment(segment, storage, source)
            for representation in wanted:
                if representation in done:
                    continue
                published.append(
                    _produce(segment, representation, source.name, storage)
                )
    # Every wanted representation is published: the segment leaves the queue.
    AttachmentRecordingSegment.objects.filter(
        pk=segment.pk, derivatives_done_at__isnull=True
    ).update(derivatives_done_at=timezone.now())
    return published


def _produce(segment, representation, source_path, storage):
    recording = AttachmentRecording.objects.get(pk=segment.recording_id)
    args: tuple[int, ...]
    if representation == "peaks":
        limit = peaks_bytes(
            frame_count=segment.frame_count,
            rate=recording.sample_rate,
            channels=recording.channels,
        )
        operation, args = "peaks", ()
    else:
        limit = HEADER_BYTES + segment.frame_count * 2
        operation, args = "split", (int(representation[-1]),)
    with tempfile.NamedTemporaryFile(dir=_work_dir()) as output:
        facts = sandbox.run(
            operation, source_path, *args, output=output, output_limit=limit
        )
        output.flush()
        if (
            os.fstat(output.fileno()).st_size != limit
            or facts.get("frame_count") != segment.frame_count
            or facts.get("sample_rate") != recording.sample_rate
        ):
            raise AttachmentError("validation_failed", 422)
        if representation != "peaks":
            # A channel derivative must itself be a valid mono segment.
            probed = sandbox.probe(output.name)
            if probed["channels"] != 1 or probed["frame_count"] != segment.frame_count:
                raise AttachmentError("validation_failed", 422)
        output.seek(0)
        digest = hashlib.sha256()
        for chunk in iter(lambda: output.read(256 * 1024), b""):
            digest.update(chunk)
        object_id = derivative_id(segment, representation)
        identity = storage.promote(
            output,
            upload_id=object_id,
            expected_length=limit,
            expected_sha256=digest.hexdigest(),
        )
    try:
        return _publish(segment, representation, identity, digest, limit, facts)
    except AttachmentError:
        # Deleted or fenced while it was being produced: the object was already
        # promoted, so remove it, unless a published row already owns that key.
        # The erase drain repeats the erase after its horizon in case this
        # process dies first.
        if not AttachmentRecordingDerivative.objects.filter(
            segment=segment, representation=representation
        ).exists():
            storage.erase_upload(object_id)
        raise


def _publish(segment, representation, identity, digest, limit, facts):
    with transaction.atomic():
        AttachmentScope.objects.select_for_update().get(pk=segment.scope_id)
        scope, _, occurrence = _live(segment)
        row, _ = AttachmentRecordingDerivative.objects.get_or_create(
            segment=segment,
            representation=representation,
            defaults={
                "scope": scope,
                "storage_key": identity.key,
                "storage_version": identity.version,
                "sha256": digest.hexdigest(),
                "byte_length": limit,
                "facts": {
                    key: facts[key]
                    for key in ("sample_rate", "channels", "frame_count", "levels")
                    if key in facts
                },
                "policy_version": occurrence.policy_version,
                "deletion_generation": occurrence.deletion_generation,
                "published_at": timezone.now(),
            },
        )
    return row


def identity_for(*, scope_id, occurrence_id, representation):
    """Gateway lookup: (ObjectIdentity, length, facts) or None. Fail closed."""
    if representation not in REPRESENTATIONS:
        return None
    segment = AttachmentRecordingSegment.objects.filter(
        scope_id=scope_id, occurrence_client_id=occurrence_id
    ).first()
    if segment is None:
        return None
    try:
        scope, _, occurrence = _live(segment)
    except AttachmentError:
        return None
    row = AttachmentRecordingDerivative.objects.filter(
        segment=segment,
        representation=representation,
        published_at__isnull=False,
        policy_version=scope.policy_version,
        deletion_generation=scope.deletion_generation,
    ).first()
    if row is None:
        return None
    return (
        ObjectIdentity(row.storage_key, row.storage_version),
        row.byte_length,
        row.facts,
    )


def erase_for_segment(segment, storage):
    """Erase every derivative object of a segment (deterministic keys)."""
    for representation in REPRESENTATIONS:
        storage.erase_upload(derivative_id(segment, representation))


def erase_for_attachment(*, scope_id, attachment_client_id, storage):
    """Erase hook for a tombstoned imported segment: its derivatives.

    Called by ``retention.erase`` for attachment-keyed erase work. The segment
    leaves the derivative queue (``ERASED``) and nothing is served for it.
    Returns whether the attachment was a recording segment.
    """
    segment = AttachmentRecordingSegment.objects.filter(
        scope_id=scope_id, attachment_client_id=attachment_client_id
    ).first()
    if segment is None:
        return False
    erase_for_segment(segment, storage)
    AttachmentRecordingSegment.objects.filter(pk=segment.pk).update(
        state=RecordingSegmentState.ERASED
    )
    return True


def _live_occurrence(now):
    return AttachmentOccurrence.objects.filter(
        scope_id=OuterRef("scope_id"),
        client_id=OuterRef("occurrence_client_id"),
        deleted_at__isnull=True,
        expires_at__gt=now,
    ).filter(Q(source_expires_at__isnull=True) | Q(source_expires_at__gt=now))


def _peaks_published():
    # ``generate`` publishes peaks last, so published peaks mean every wanted
    # representation of that segment is published.
    return AttachmentRecordingDerivative.objects.filter(
        segment_id=OuterRef("pk"), published_at__isnull=False, representation="peaks"
    )


def _open():
    """Open segments, in queue order: ``attachment_derivative_due_idx`` exactly."""
    return AttachmentRecordingSegment.objects.filter(
        state=RecordingSegmentState.PUBLISHED, derivatives_done_at__isnull=True
    ).order_by(Coalesce("derivative_checked_at", "created_at"), "pk")


def pending(*, limit, now=None):
    """``[(segment id, scope id)]`` still missing derivatives, fairest first.

    Only open published segments of enabled scopes whose occurrence is live
    (not deleted, not expired) qualify, so deleted or expired recordings never
    occupy the queue; segments whose last attempt failed go to the back. The
    scan reads the partial index, which holds open segments only: finished
    ones are stamped ``derivatives_done_at`` by ``generate`` or ``purge``.
    """
    now = now or timezone.now()
    return list(
        _open()
        .filter(scope__enabled=True)
        .filter(Exists(_live_occurrence(now)))
        .exclude(Exists(_peaks_published()))
        .values_list("pk", "scope_id")[:limit]
    )


def purge(*, limit, now=None):
    """Take terminal segments out of the derivative queue; returns the count.

    Terminal means no derivative work can ever be needed again: the peaks are
    already published (a ``generate`` that died before its stamp), or the
    segment's occurrence is deleted or expired, which the erase path ends in
    ``erased``. Each is stamped ``derivatives_done_at`` and so leaves the
    partial index. Rows are never deleted: ``recording_manifest`` fails closed
    only because it still finds a segment that is not live. A disabled scope
    is not terminal (it may be enabled again); its segments stay queued.
    """
    now = now or timezone.now()
    terminal = list(
        _open()
        .filter(~Exists(_live_occurrence(now)) | Exists(_peaks_published()))
        .values_list("pk", flat=True)[:limit]
    )
    return AttachmentRecordingSegment.objects.filter(
        pk__in=terminal, derivatives_done_at__isnull=True
    ).update(derivatives_done_at=now)


def defer(segment_id):
    """A failed attempt: send the segment to the back of the queue."""
    AttachmentRecordingSegment.objects.filter(pk=segment_id).update(
        derivative_checked_at=timezone.now()
    )


def recording_manifest(*, scope_id, recording_id):
    """The published manifest only while every segment occurrence survives."""
    recording = AttachmentRecording.objects.filter(
        scope_id=scope_id, client_id=recording_id, state=RecordingState.READY
    ).first()
    if recording is None or recording.manifest is None or recording.deleted_at:
        return None
    for segment in AttachmentRecordingSegment.objects.filter(recording=recording):
        try:
            _live(segment)
        except AttachmentError:
            return None
    return recording.manifest
