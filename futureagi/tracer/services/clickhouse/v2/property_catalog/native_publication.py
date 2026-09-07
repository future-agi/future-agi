"""Hold native write closure through exact ACTIVE confirmation and publication.

The workspace coordinator remains the source of authorization. This additional
scope guard proves all native dependencies and prevents a new registration from
racing the final check. It does not authorize a source read, replacement build,
new generation, or retransmission of a Sent attempt.
"""

from contextlib import contextmanager
from threading import get_ident

from .durable_native_writer import DurableNativeCatalogWriter, NativeWriteUnresolved
from .native_write_journal import (
    NativePublicationBinding,
    NativeWriteScope,
    native_parameters_sha256,
)
from .native_write_proof import _COLUMNS

_TABLE = "property_catalog_activations"
_COMPLETION_AUTHORITY = object()


def _intent(writer, record):
    from .activation import ActivationRecord, ActivationStatus
    from .state_store import _activation_row

    if (
        type(record) is not ActivationRecord
        or record.status is not ActivationStatus.ACTIVE
    ):
        raise TypeError("native publication requires an exact ACTIVE record")
    row = _activation_row(record)
    scope = NativeWriteScope.from_rows(_TABLE, (row,), writer.proof.identity)
    token = f"property-catalog-activation-v1:{record.build_token}:{record.activation_sha256}"
    digest = native_parameters_sha256(_COLUMNS[_TABLE], (row,))
    return scope, token, digest


class NativePublicationWriteScope:
    """Explicit thread-bound lease passed only to the exact ACTIVE insert."""

    def __init__(self, owner, scoped, remaining, record, binding, closing):
        self._owner, self._scoped, self._remaining = owner, scoped, remaining
        self._record, self._binding, self._closing = record, binding, closing
        self._open, self._closed = True, None

    def require(self, writer, scope):
        if (
            not self._open
            or writer is not self._owner.writer
            or scope != self._scoped.scope
        ):
            raise NativeWriteUnresolved("native publication scope is closed or differs")
        self._scoped._check()
        self.remaining()
        return self._scoped

    def require_insert(self, writer, scope, *, table, token, digest):
        scoped = self.require(writer, scope)
        if (
            self._closed is not None
            or table != _TABLE
            or token != self._binding.deduplication_token
            or digest != self._binding.parameters_sha256
        ):
            raise NativeWriteUnresolved(
                "native publication only permits its unconfirmed exact ACTIVE insert"
            )
        return scoped

    def remaining(self):
        if not self._open:
            raise NativeWriteUnresolved("native publication scope is no longer held")
        return self._remaining()

    def confirm_complete(self, record):
        self.require(self._owner.writer, self._scoped.scope)
        if record != self._record:
            raise NativeWriteUnresolved("native publication changed its ACTIVE record")
        attempt = self._owner._confirm_scoped(
            self._scoped, record, remaining=self.remaining
        )
        self._closed = self._scoped.finish_closure(self._closing, attempt)
        self._scoped.require_closed(self._closed)


class NativeCompletionProof:
    """Ephemeral capture-retirement proof; no SQL, dispatch, or lock acquisition."""

    def __init__(self, writer, scoped, record, generation, remaining, *, _authority):
        if _authority is not _COMPLETION_AUTHORITY:
            raise TypeError("completion proof requires the native completion barrier")
        self._writer, self._scoped, self._record = writer, scoped, record
        self._scope, self._identity = scoped.scope, writer.proof.identity
        self._database, self._generation = writer.database, generation
        self._remaining, self._thread, self._open = remaining, get_ident(), True

    def require_capture(self, spec):
        from .source_capture import SourceCaptureSpec

        if not self._open or get_ident() != self._thread:
            raise NativeWriteUnresolved(
                "native completion proof requires its open owning thread"
            )
        self._remaining()
        self._scoped._check()
        if (
            self._writer.database != self._database
            or self._writer.proof.identity != self._identity
            or self._scoped.scope != self._scope
        ):
            raise NativeWriteUnresolved("native completion proof binding changed")
        # pending() opens per-receipt sessions. A nonempty index is sufficient
        # to reject here without taking another lock or settling any attempt.
        index = self._scoped._read_index()
        if index["generation"] != self._generation or index["pending"]:
            raise NativeWriteUnresolved("native completion generation changed")
        if type(spec) is not SourceCaptureSpec:
            raise TypeError("native completion requires a typed capture spec")
        spec.__post_init__()
        if (
            spec.installation_id != self._identity.producer_stream_id
            or spec.catalog_database != self._database
            or any(
                getattr(spec, field) != getattr(self._record, field)
                for field in ("organization_id", "workspace_id", "build_token")
            )
        ):
            raise NativeWriteUnresolved("capture differs from native completed build")
        self._remaining()
        return True


class NativePublicationBarrier:
    def __init__(self, writer, *, timeout_ms=30_000):
        if type(writer) is not DurableNativeCatalogWriter:
            raise TypeError("native publication requires the real durable writer")
        writer.proof._budget(timeout_ms)
        self.writer, self.timeout_ms = writer, timeout_ms

    @contextmanager
    def publication(self, *, record, publication, fence, checkpoint_states):
        from .publication_journal import ActivationPublicationSession, _validate

        if type(publication) is not ActivationPublicationSession:
            raise TypeError("native publication requires the coordinator's journal")
        if publication.document is None or publication.positive_only:
            raise NativeWriteUnresolved(
                "legacy ACTIVE has no managed publication intent"
            )
        _validate(publication.document)
        publication.check_evidence(fence=fence, checkpoint_states=checkpoint_states)
        if (
            publication.database != self.writer.database
            or publication.record != record
            or publication.document["phase"] not in {"armed", "resolved"}
        ):
            raise NativeWriteUnresolved(
                "native publication is not its armed exact intent"
            )
        evidence = publication.document["publication"]
        scope, token, digest = _intent(self.writer, record)
        previous = evidence["previous_active"]
        binding = NativePublicationBinding(
            deduplication_token=token,
            parameters_sha256=digest,
            publication_sha256=evidence["sha256"],
            fence_sha256=fence.fence_sha256,
            build_lease_sha256=fence.build_lease_sha256,
            checkpoint_state_sha256s=tuple(checkpoint_states),
            previous_active_sha256=previous["activation_sha256"] if previous else None,
        )
        remaining = self.writer.proof._budget(self.timeout_ms)
        self.writer._validate_driver()
        with self.writer._journal() as journal, journal.scope(scope) as scoped:
            journal.track_recovery_scope(scoped)
            self._retain_active(scoped, token, digest, required=False)
            self.writer._recover_scoped(
                scoped, remaining=remaining, prepared_publication=binding
            )
            closing = scoped.begin_closure(binding)
            guard = NativePublicationWriteScope(
                self, scoped, remaining, record, binding, closing
            )
            try:
                yield guard
                # This check remains inside the scope lock even after the
                # publication journal is resolved by the caller.
                if guard._closed is None:
                    raise NativeWriteUnresolved("native publication was not confirmed")
                scoped.require_closed(guard._closed)
                remaining()
            finally:
                guard._open = False

    def confirm(self, record):
        """Positive ACTIVE/predecessor evidence, never authority to send a write."""
        with self.completion(record):
            return True

    @contextmanager
    def completion(self, record):
        """Hold settled build evidence through local retirement/repair receipts.

        Reader-control writes must finish before entry: this guard permits no
        INSERT and cannot be reentered through another native writer.
        """
        scope, _, _ = _intent(self.writer, record)
        remaining = self.writer.proof._budget(self.timeout_ms)
        self.writer._validate_driver()
        with self.writer._journal() as journal, journal.scope(scope) as scoped:
            journal.track_recovery_scope(scoped)
            self._confirm_scoped(scoped, record, remaining=remaining)
            generation = scoped.generation
            proof = NativeCompletionProof(
                self.writer,
                scoped,
                record,
                generation,
                remaining,
                _authority=_COMPLETION_AUTHORITY,
            )
            try:
                yield proof
                if scoped.generation != generation or scoped.pending():
                    raise NativeWriteUnresolved("native completion generation changed")
                remaining()
            finally:
                proof._open = False

    def _confirm_scoped(self, scoped, record, *, remaining):
        scope, token, digest = _intent(self.writer, record)
        if scope != scoped.scope:
            raise NativeWriteUnresolved("native ACTIVE confirmation crossed scopes")
        expected = self._retain_active(scoped, token, digest, required=True)
        self.writer._recover_scoped(
            scoped, remaining=remaining, final_confirmation=expected
        )
        with scoped.session(_TABLE, token) as session:
            attempt = session.load()
            if (
                attempt is None
                or attempt.parameters_sha256 != digest
                or attempt.state != "complete"
            ):
                raise NativeWriteUnresolved(
                    "visible ACTIVE lacks its exact native receipt"
                )
            # Recovery just proved this exact target last under the same BUILD
            # lock, including fresh all-member coverage even for Complete.
        if scoped.pending():
            raise NativeWriteUnresolved(
                "ACTIVE still has unresolved native dependencies"
            )
        remaining()
        return attempt

    def _retain_active(self, scoped, token, digest, *, required):
        with scoped.session(_TABLE, token) as session:
            attempt = session.load()
            if attempt is None:
                if not required:
                    return
                raise NativeWriteUnresolved(
                    "visible ACTIVE lacks its exact native receipt"
                )
            if attempt.parameters_sha256 != digest:
                raise NativeWriteUnresolved(
                    "visible ACTIVE lacks its exact native receipt"
                )
            self.writer._retain_complete(session, attempt)
            return attempt
