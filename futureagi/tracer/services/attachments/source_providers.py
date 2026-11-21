"""Trusted source metadata and reservation-fenced, immutable bounded reads.

Only sealed server-producer locators are opened. The public import payload is
an F01 opaque SourceRef, never a URL, a header map or a provider API command.
Publication calls revalidate once outside its transaction, then again under
its ordered session/source locks; the latter does local authority checks only.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from tracer.models.attachment import AttachmentScope, CaptureSession
from tracer.models.attachment_recording import (
    AttachmentImportJob,
    AttachmentProviderConnection,
    AttachmentProviderRecording,
    AttachmentSourceEnvelope,
)
from tracer.services.attachments import bootstrap, envelopes, safe_fetch
from tracer.services.attachments.contract import AttachmentError, admit_audio
from tracer.services.attachments.interfaces import LeaseFence, SourceFacts, SourceRef


def _denied():
    return AttachmentError("source_unavailable", 403)


def _envelope_provider(scope_id, envelope_id):
    rows = list(
        AttachmentProviderRecording.objects.filter(
            scope_id=scope_id, output_envelope_id=envelope_id
        )[:2]
    )
    if len(rows) > 1:
        raise _denied()  # never choose arbitrary ancestry
    return rows[0] if rows else None


def lock_source(*, scope_id, source):
    """Local source lock order shared by import and upload publication.

    An envelope may be an alias for a provider output. Discover its ancestry
    BEFORE locks, then recheck under connection -> envelope -> registration.
    SourceFacts stays frozen; output_envelope_id is the actual persisted link.
    """
    if not transaction.get_connection().in_atomic_block:
        raise AttachmentError("conflict", 409)
    if source.kind == "RemoteUrlEnvelope":
        before = _envelope_provider(scope_id, source.id)
        envelope_id = source.id
    elif source.kind == "ProviderFile":
        before = AttachmentProviderRecording.objects.get(
            pk=source.id, scope_id=scope_id
        )
        envelope_id = before.output_envelope_id
    else:
        raise _denied()
    connection = None
    if before is not None:
        connection = AttachmentProviderConnection.objects.select_for_update().get(
            pk=before.connection_id, scope_id=scope_id
        )
    envelope = None
    if envelope_id is not None:
        # Short-lived sealed material may already have been swept at terminal
        # cleanup. This helper only locks; authorize=True callers subsequently
        # revalidate and reject missing envelopes, while cleanup still proceeds.
        envelope = (
            AttachmentSourceEnvelope.objects.select_for_update()
            .filter(pk=envelope_id, scope_id=scope_id)
            .first()
        )
    provider = None
    if before is not None:
        provider = AttachmentProviderRecording.objects.select_for_update().get(
            pk=before.id, scope_id=scope_id
        )
        if (provider.connection_id, provider.output_envelope_id) != (
            before.connection_id,
            envelope_id,
        ):
            raise AttachmentError("conflict", 409)
    if source.kind == "RemoteUrlEnvelope":
        current = _envelope_provider(scope_id, source.id)
        if (current.id if current is not None else None) != (
            provider.id if provider is not None else None
        ):
            raise AttachmentError("conflict", 409)  # rediscover; no late lock
    return connection, envelope, provider


def _session(scope_id, importer, capture_session_id):
    session = CaptureSession.objects.filter(
        pk=capture_session_id,
        scope_id=scope_id,
        writer_kind=importer.kind,
        writer_id=importer.id,
    ).first()
    if session is None:
        raise _denied()
    scope = bootstrap.check_session(session)
    policy = bootstrap.session_policy(session)
    if not scope.allow_import or not policy.allow_import:
        raise _denied()
    return scope, session, policy


def _envelope_facts(row) -> SourceFacts:
    try:
        return SourceFacts(
            source_kind="RemoteUrlEnvelope",
            source_id=row.id,
            creator_kind=row.creator_kind,
            creator_id=row.creator_id,
            generation_kind=row.generation_kind,
            generation=row.generation,
            byte_length=row.source_bytes,
            mime_type=row.mime_type,
            duration_ms=row.duration_ms,
            expires_at=min(row.expires_at, row.source_expires_at),
            authority_generation=row.authority_generation,
            parent_trace_id=row.trace_id,
            parent_span_id=row.span_id,
            direction=row.direction,
            connection_id=None,
        )
    except (TypeError, ValueError):
        raise _denied() from None


def _provider_facts(row, session, importer) -> SourceFacts:
    connection = AttachmentProviderConnection.objects.filter(
        pk=row.connection_id,
        scope_id=row.scope_id,
        provider="livekit",
        enabled=True,
        revoked_at__isnull=True,
    ).first()
    if (
        connection is None
        or row.provider_state != "complete"
        or row.expires_at <= timezone.now()
        or (row.registrar_kind, row.registrar_id) != (importer.kind, importer.id)
        or row.source_authority_generation != connection.authority_generation
        or (row.capture_session_client_id, row.correlation_nonce)
        != (session.client_id, session.correlation_nonce)
    ):
        raise _denied()
    # A temporarily unqualified reader is not a permanent source revocation.
    # Recheck the same deployment registry advertised by allowed_sources(),
    # also when the provider output is named through an opaque envelope alias.
    from tracer.services.attachments.provider_recordings import READERS

    if connection.provider not in READERS:
        raise AttachmentError("source_unavailable", 503)
    envelope = envelopes.describe(
        scope_id=row.scope_id, envelope_id=row.output_envelope_id
    )
    envelopes.check_capture_session(envelope, session.id)
    sealed = _envelope_facts(envelope)
    try:
        facts = SourceFacts(
            source_kind="ProviderFile",
            source_id=row.id,
            creator_kind=row.registrar_kind,
            creator_id=row.registrar_id,
            generation_kind=row.source_generation_kind,
            generation=row.source_generation,
            byte_length=row.output_bytes,
            mime_type=row.source_mime_type,
            duration_ms=row.output_duration_ms,
            expires_at=min(row.expires_at, row.source_expires_at, sealed.expires_at),
            authority_generation=row.source_authority_generation,
            parent_trace_id=row.trace_id,
            parent_span_id=row.span_id,
            direction=row.direction,
            connection_id=row.connection_id,
        )
    except (TypeError, ValueError):
        raise _denied() from None
    # A provider envelope cannot replace the registered object's generation,
    # creator, parent, length, duration or direction, even in the same scope.
    for name in (
        "creator_kind",
        "creator_id",
        "generation_kind",
        "generation",
        "byte_length",
        "mime_type",
        "duration_ms",
        "parent_trace_id",
        "parent_span_id",
        "direction",
    ):
        if getattr(facts, name) != getattr(sealed, name):
            raise _denied()
    return facts


def _resolve(
    *, source, scope_id, importer, capture_session_id
) -> tuple[SourceFacts, AttachmentScope]:
    if not isinstance(source, SourceRef):
        raise _denied()
    scope, session, policy = _session(scope_id, importer, capture_session_id)
    if source.kind == "RemoteUrlEnvelope":
        row = envelopes.describe(scope_id=scope_id, envelope_id=source.id)
        envelopes.check_capture_session(row, session.id)
        facts = _envelope_facts(row)
        # Provider-backed envelopes retain the connection's revocation fence
        # even when named through the canonical RemoteUrlEnvelope wire form.
        provider = _envelope_provider(scope_id, row.id)
        if provider is not None:
            _provider_facts(provider, session, importer)
    else:
        row = AttachmentProviderRecording.objects.filter(
            pk=source.id, scope_id=scope_id
        ).first()
        if row is None:
            raise _denied()
        facts = _provider_facts(row, session, importer)
    if (facts.creator_kind, facts.creator_id) != (importer.kind, importer.id):
        raise _denied()
    if facts.expires_at <= timezone.now():
        raise AttachmentError("expired", 410)
    admit_audio(
        scope, policy, direction=facts.direction, source_kind="imported_recording"
    )
    return facts, scope


def _parent(facts, scope, importer):
    from tracer.services.attachments.authority import LiveAuthority
    from tracer.services.attachments.occurrences import Parent

    parent = Parent(facts.parent_trace_id, facts.parent_span_id)
    state = LiveAuthority().source(
        SimpleNamespace(user_id=importer.id if importer.kind == "user" else None),
        scope,
        parent,
    )
    if state is None:
        # Unknown authority is fail-closed, but not evidence of permanent denial.
        raise AttachmentError("source_unavailable", 503)
    if not state.readable or state.expires_at <= timezone.now():
        raise _denied()
    # The producer has to seal under the applicable retention cap; it cannot
    # prolong a source simply because its retrieval credentials last longer.
    if facts.expires_at > state.expires_at:
        raise _denied()


def resolve(
    *, source: SourceRef, scope_id: UUID, importer, capture_session_id: UUID
) -> SourceFacts:
    """Metadata/auth only, before capacity admission; never fetch source bytes."""
    if transaction.get_connection().in_atomic_block:
        raise AttachmentError("conflict", 409)
    facts, scope = _resolve(
        source=source,
        scope_id=scope_id,
        importer=importer,
        capture_session_id=capture_session_id,
    )
    _parent(facts, scope, importer)
    return facts


def revalidate(
    *, facts: SourceFacts, scope_id: UUID, importer, capture_session_id: UUID
) -> None:
    """Outside locks checks live parent; inside ordered locks checks local fences.

    Publication must do both: Reader.verify_remote() immediately beforehand,
    then this local pass while holding the same source/session/creator locks.
    """
    current, scope = _resolve(
        source=SourceRef(facts.source_kind, facts.source_id),
        scope_id=scope_id,
        importer=importer,
        capture_session_id=capture_session_id,
    )
    if current != facts:
        raise AttachmentError("snapshot_changed", 409)
    if not transaction.get_connection().in_atomic_block:
        _parent(facts, scope, importer)


def _job_facts(job):
    connection_id = None
    if job.source_kind == "ProviderFile":
        connection_id = (
            AttachmentProviderRecording.objects.filter(
                pk=job.provider_recording_id,
                scope_id=job.scope_id,
            )
            .values_list("connection_id", flat=True)
            .first()
        )
    try:
        return SourceFacts(
            source_kind=job.source_kind,
            source_id=job.envelope_id
            if job.source_kind == "RemoteUrlEnvelope"
            else job.provider_recording_id,
            creator_kind=job.source_creator_kind,
            creator_id=job.source_creator_id,
            generation_kind=job.source_generation_kind,
            generation=job.source_generation,
            byte_length=job.source_byte_length,
            mime_type=job.source_mime_type,
            duration_ms=job.source_duration_ms,
            expires_at=job.source_expires_at,
            authority_generation=job.source_authority_generation,
            parent_trace_id=job.source_trace_id,
            parent_span_id=job.source_span_id,
            direction=job.source_direction,
            connection_id=connection_id,
        )
    except (TypeError, ValueError):
        raise _denied() from None


def _check_job(job, fence):
    now = timezone.now()
    if (
        not isinstance(fence, LeaseFence)
        or fence.owner_kind != "import"
        or fence.owner_id != job.id
        or job.lease_token != fence.token
        or job.lease_epoch != fence.epoch
        or job.lease_until is None
        or min(job.lease_until, fence.expires_at, job.deadline) <= now
        or job.state not in {"importing", "validating"}
        or job.lease_phase not in {"recovering", "writing", "publishing"}
        or job.finished_at is not None
        or job.storage_binding_id is None
        or job.reservation_id is None
        or job.reservation_settled_at is not None
        or not job.reservation_charge_bytes
        or job.reservation_expires_at is None
        or job.reservation_expires_at <= now
    ):
        raise AttachmentError("conflict", 409)
    # Source IO cannot run while database authority/capacity locks are held.
    if transaction.get_connection().in_atomic_block:
        raise AttachmentError("conflict", 409)
    scope = AttachmentScope.objects.get(pk=job.scope_id)
    if (
        job.policy_version != scope.policy_version
        or job.deletion_generation != scope.deletion_generation
    ):
        raise _denied()
    if job.upload_id:
        upload = job.upload
        if (
            upload.producer_kind != "import"
            or upload.reservation_id is None
            or upload.reservation_settled_at is not None
            or not upload.reservation_charge_bytes
            or upload.reservation_expires_at is None
            or upload.reservation_expires_at <= now
            or upload.attachment.storage_binding_id != job.storage_binding_id
        ):
            raise AttachmentError("conflict", 409)
        target = upload.attachment
    else:
        target = job.recording
    if target.state not in {"pending_upload", "importing", "validating"}:
        raise _denied()


class SourceReader:
    """Each bounded read keeps bytes private until after all rechecks succeed."""

    def __init__(self, job, fence):
        self.job_id, self.fence = job.id, fence
        self.facts = _job_facts(job)
        self._closed = False
        self._check()
        envelope_id = job.envelope_id
        if job.provider_recording_id:
            envelope_id = AttachmentProviderRecording.objects.get(
                pk=job.provider_recording_id,
                scope_id=job.scope_id,
            ).output_envelope_id
        url = envelopes.open_url(scope_id=job.scope_id, envelope_id=envelope_id)
        destination = safe_fetch.parse_destination(
            url,
            approved_hosts=getattr(settings, "FI_ATTACHMENTS_IMPORT_HOSTS", ()),
        )
        self._source: safe_fetch.RangeSource | None = safe_fetch.RangeSource(
            destination=destination,
            total_bytes=self.facts.byte_length,
            window=safe_fetch.MAX_RANGE_BYTES,
            timeout_seconds=30,
            bytes_per_second=8 * 1024 * 1024,
            generation=self.facts.generation,
            generation_kind=self.facts.generation_kind,
        )
        self.verify_remote()

    def _check(self):
        if self._closed:
            raise _denied()
        job = AttachmentImportJob.objects.get(pk=self.job_id)
        _check_job(job, self.fence)
        if _job_facts(job) != self.facts:
            raise AttachmentError("snapshot_changed", 409)
        if job.capture_session_id is None:
            raise _denied()
        revalidate(
            facts=self.facts,
            scope_id=job.scope_id,
            importer=SimpleNamespace(kind=job.importer_kind, id=job.importer_id),
            capture_session_id=job.capture_session_id,
        )

    def _range_source(self) -> safe_fetch.RangeSource:
        if self._source is None:
            raise _denied()
        return self._source

    def verify_remote(self):
        """Call immediately before publication, outside DB locks as for reads."""
        self._check()
        revision, length, mime = safe_fetch.inspect_metadata(self._range_source())
        if (revision, length, mime) != (
            self.facts.generation,
            self.facts.byte_length,
            self.facts.mime_type,
        ):
            raise AttachmentError("snapshot_changed", 409)
        self._check()

    def read_range(self, *, start: int, end: int) -> bytes:
        if (
            type(start) is not int
            or type(end) is not int
            or start < 0
            or end < start
            or end >= self.facts.byte_length
            or end - start + 1 > safe_fetch.MAX_RANGE_BYTES
        ):
            raise AttachmentError("validation_failed", 422)
        self.verify_remote()
        with io.BytesIO() as sink:
            self._range_source().read_range(start, end - start + 1, sink)
            self.verify_remote()
            return sink.getvalue()

    def close(self):
        self._closed = True
        self._source = None

    def __enter__(self):
        if self._closed:
            raise _denied()
        return self

    def __exit__(self, *_exc):
        self.close()


def open_source(*, job_id: UUID, fence: LeaseFence) -> SourceReader:
    job = AttachmentImportJob.objects.get(pk=job_id)
    _check_job(job, fence)
    return SourceReader(job, fence)
