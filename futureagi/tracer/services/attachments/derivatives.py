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
    with tempfile.NamedTemporaryFile(dir=_work_dir()) as source:
        _copy_segment(segment, storage, source)
        for representation in wanted:
            if representation in done:
                continue
            published.append(_produce(segment, representation, source.name, storage))
    return published


def _produce(segment, representation, source_path, storage):
    recording = AttachmentRecording.objects.get(pk=segment.recording_id)
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
