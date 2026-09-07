"""Concrete DEV revision reservation and source-stream fencing.

All mutations are fully qualified, serialized by an injected lock shared by
every DEV command writer, and verified by an immediate raw read.  The exact
same revision assignment bytes are consumable by the Go hot producer.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from .activation import (
    BuildPlanSourceScope,
    BuildPlanStream,
    RevisionBuildPlan,
    RevisionFence,
    RevisionFenceStatus,
    RevisionLease,
    StreamDrainProof,
)
from .codec import (
    canonical_json,
    canonical_uuid,
    framed_sha256,
    require_sha256,
)
from .models import SourceAdapter
from .mutation_lock import CatalogMutationSerializer
from .proof_limits import (
    MAX_DELIVERIES_PER_REVISION,
    MAX_DELIVERY_REPLAYS,
    MAX_LOGICAL_STATE_VARIANTS,
)
from .publication_journal import ActivationPublicationSession
from .publisher import (
    PROPERTY_CATALOG_TABLES,
    CatalogWriteLease,
    SharedCatalogDeadline,
    require_catalog_database,
)
from .runtime_limits import RUNTIME_LIMITS
from .state_store import ClickHouseCatalogStateStore, PropertyCatalogStateError

if TYPE_CHECKING:
    from .durable_lifecycle import PersistedReservation, PriorActiveEvidence

_T = TypeVar("_T")
_SUPERSESSION_VERSION = (1 << 64) - 1
_SUPERSESSION_FORMAT = "futureagi.property-catalog.supersession.v1"
_MAX_SUPERSESSION_JOURNAL_BYTES = 2_097_152

_SOURCE_STREAM_TABLE = "property_catalog_source_streams"
_DELIVERY_TABLE = "property_catalog_deliveries"
_ACTIVATION_TABLE = "property_catalog_activations"
_CHECKPOINT_TABLE = "property_catalog_checkpoints"
_ZERO_SHA256 = "0" * 64
_MAX_RESERVATION_CANDIDATES = 4
_MAX_STATE_VARIANTS = MAX_LOGICAL_STATE_VARIANTS
_MAX_DELIVERIES = MAX_DELIVERIES_PER_REVISION
_MAX_DELIVERY_REPLAYS = MAX_DELIVERY_REPLAYS
REVISION_LEASE_SECONDS = RUNTIME_LIMITS.revision_lease_seconds
MAX_REVISION_LEASE_SECONDS = RUNTIME_LIMITS.max_revision_lease_seconds
COORDINATOR_TIMEOUT_MS = RUNTIME_LIMITS.state_store_timeout_ms
_SOURCE_STREAM_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "source_adapter",
    "producer_stream_id",
    "envelope_version",
    "first_sequence",
    "last_sequence",
    "max_contiguous_sequence",
    "last_issued_sequence",
    "fenced_sequence",
    "terminal_payload_sha256",
    "build_plan_json",
    "build_lease_sha256",
    "status",
    "gap_count",
    "gap_reasons",
    "kafka_partition",
    "kafka_high_water_offset",
    "started_at",
    "updated_at",
    "drain_deadline",
    "fenced_at",
    "_version",
)
_SOURCE_STREAM_LOGICAL_COLUMNS = tuple(
    column
    for column in _SOURCE_STREAM_COLUMNS
    if column not in {"updated_at", "_version"}
)
_HOT_DELIVERY_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "source_adapter",
    "producer_stream_id",
    "sequence",
    "terminal",
    "envelope_format",
    "envelope_version",
    "envelope_id",
    "payload_sha256",
    "previous_payload_sha256",
    "source_batch_digest",
    "outcome",
    "gap_reasons",
    "source_rows",
    "definition_rows",
    "value_rows",
    "tombstone_rows",
    "transport",
    "kafka_partition",
    "kafka_offset",
    "delivered_at",
    "_version",
)


class PropertyCatalogCoordinatorError(RuntimeError):
    """A monotonic reservation or immutable source-stream state is unsafe."""


class CatalogCoordinatorClient(Protocol):
    catalog_database: str

    def query(
        self, sql: str, params: Mapping[str, Any], *, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]: ...

    def insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None: ...


class ProducerFenceSink(Protocol):
    def publish(self, assignment: ProducerRevisionAssignment) -> None: ...


@dataclass(frozen=True, slots=True)
class ProducerRevisionAssignment:
    """Python representation of ``propertycatalog.RevisionFence`` v2."""

    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    projection_version: int
    build_lease_sha256: str
    build_token: str
    project_ids: tuple[str, ...]
    span_since_us: int
    span_until_us: int
    issued_at: datetime
    expires_at: datetime
    drain_deadline: datetime | None
    fenced_sequence: int
    status: str
    fence_sha256: str = _ZERO_SHA256

    def __post_init__(self) -> None:
        for field_name in ("organization_id", "workspace_id", "build_token"):
            object.__setattr__(
                self,
                field_name,
                canonical_uuid(getattr(self, field_name), field=field_name),
            )
        require_sha256(self.build_lease_sha256, field="build_lease_sha256")
        scope = BuildPlanSourceScope(
            project_ids=self.project_ids,
            span_since_us=self.span_since_us,
            span_until_us=self.span_until_us,
        )
        object.__setattr__(self, "project_ids", scope.project_ids)
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("producer assignment lease is not increasing")
        if self.status not in {"building", "draining", "fenced"}:
            raise ValueError("producer assignment status is invalid")
        if self.status == "building":
            if self.drain_deadline is not None or self.fenced_sequence != 0:
                raise ValueError("building assignment cannot carry a drain fence")
        else:
            if self.drain_deadline is None:
                raise ValueError("draining/fenced assignment requires a deadline")
            _require_utc(self.drain_deadline, "drain_deadline")
            if self.drain_deadline <= self.issued_at:
                raise ValueError("drain deadline must follow assignment issue")
        _positive_uint(self.catalog_epoch, 16, "catalog_epoch")
        _positive_uint(self.catalog_revision, 64, "catalog_revision")
        _positive_uint(self.projection_version, 16, "projection_version")
        if type(self.fenced_sequence) is not int or not 0 <= self.fenced_sequence < (
            1 << 64
        ):
            raise ValueError("fenced_sequence must be a UInt64")
        expected = producer_assignment_sha256(self)
        if self.fence_sha256 == _ZERO_SHA256:
            object.__setattr__(self, "fence_sha256", expected)
        elif self.fence_sha256 != expected:
            raise ValueError("producer assignment digest mismatch")

    @property
    def document(self) -> Mapping[str, Any]:
        return {
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "catalog_epoch": self.catalog_epoch,
            "catalog_revision": self.catalog_revision,
            "projection_version": self.projection_version,
            "build_lease_sha256": self.build_lease_sha256,
            "build_token": self.build_token,
            "project_ids": list(self.project_ids),
            "span_since_us": self.span_since_us,
            "span_until_us": self.span_until_us,
            "issued_at": _time_text(self.issued_at),
            "expires_at": _time_text(self.expires_at),
            "drain_deadline": (
                _time_text(self.drain_deadline) if self.drain_deadline else ""
            ),
            "fenced_sequence": self.fenced_sequence,
            "status": self.status,
            "fence_sha256": self.fence_sha256,
        }


def producer_assignment_sha256(assignment: ProducerRevisionAssignment) -> str:
    return framed_sha256(
        "futureagi.property-catalog.revision-fence.v2",
        assignment.organization_id,
        assignment.workspace_id,
        assignment.catalog_epoch,
        assignment.catalog_revision,
        assignment.projection_version,
        assignment.build_lease_sha256,
        assignment.build_token,
        len(assignment.project_ids),
        *assignment.project_ids,
        assignment.span_since_us,
        assignment.span_until_us,
        _time_text(assignment.issued_at),
        _time_text(assignment.expires_at),
        _time_text(assignment.drain_deadline) if assignment.drain_deadline else "",
        assignment.fenced_sequence,
        assignment.status,
    )


def encode_producer_assignment(assignment: ProducerRevisionAssignment) -> bytes:
    document = {
        "format": "futureagi.property-catalog-revision-fence",
        "version": 2,
        "fences": [assignment.document],
    }
    return (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )


class AtomicSingleTenantFenceFile:
    """Atomically publish one explicit DEV tenant assignment for the Go runtime."""

    def __init__(self, path: str) -> None:
        candidate = Path(path)
        if not candidate.is_absolute() or not candidate.parent.is_dir():
            raise ValueError(
                "producer fence path must be absolute with an existing parent"
            )
        self._path = candidate

    def publish(self, assignment: ProducerRevisionAssignment) -> None:
        raw = encode_producer_assignment(assignment)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".property-catalog-fence-", dir=self._path.parent
        )
        temporary = Path(temporary_name)
        keep = True
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, raw)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self._path)
            keep = False
            directory_fd = os.open(self._path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if keep:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass


class FileSupersessionJournal:
    """Fsynced control-write intent on the SAME persistent volume as the lock.

    One slot per workspace is replaced only after the previous intent completed.
    Completed supersessions also remain in the ClickHouse reservation log, but
    permanent file receipts must be retained to veto stale replica reads. This
    directory must be shared by every writer and survive process/pod loss.
    """

    def __init__(self, directory: str) -> None:
        self._directory = Path(directory)
        if not self._directory.is_absolute() or not self._directory.is_dir():
            raise ValueError(
                "recovery journal requires an existing absolute shared directory"
            )

    def _path(self, key: str) -> Path:
        return self._directory / (
            framed_sha256(_SUPERSESSION_FORMAT, key) + ".supersession.json"
        )

    def load(self, key: str) -> dict[str, Any] | None:
        intent = self.load_record(key)
        if intent is not None and (
            intent.get("format") != _SUPERSESSION_FORMAT
            or type(intent.get("complete")) is not bool
            or set(intent)
            != {
                "format",
                "complete",
                "predecessor",
                "revoked",
                "replacement",
                "active_head",
            }
        ):
            raise PropertyCatalogCoordinatorError("supersession journal is corrupt")
        return intent

    def load_record(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        try:
            descriptor = os.open(
                path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise PropertyCatalogCoordinatorError(
                        "supersession journal is not a regular file"
                    )
                if info.st_size > _MAX_SUPERSESSION_JOURNAL_BYTES:
                    raise PropertyCatalogCoordinatorError(
                        "supersession journal exceeds its bound"
                    )
                raw = stream.read(_MAX_SUPERSESSION_JOURNAL_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PropertyCatalogCoordinatorError(
                "cannot read supersession journal"
            ) from exc
        try:
            document = json.loads(raw)
            if (
                len(raw) > _MAX_SUPERSESSION_JOURNAL_BYTES
                or not isinstance(document, dict)
                or set(document) != {"key", "intent", "sha256"}
                or (_journal_json(document) + "\n").encode() != raw
            ):
                raise ValueError("invalid canonical journal")
            intent = document["intent"]
            if (
                document["key"] != key
                or not isinstance(intent, dict)
                or document["sha256"]
                != framed_sha256(_SUPERSESSION_FORMAT, key, _journal_json(intent))
            ):
                raise ValueError("invalid intent")
            return intent
        except (KeyError, TypeError, ValueError, RecursionError) as exc:
            raise PropertyCatalogCoordinatorError(
                "supersession journal is corrupt"
            ) from exc

    def save(self, key: str, intent: Mapping[str, Any]) -> None:
        archive_key = f"{key}:revoked:{intent['revoked']['build_lease_sha256']}"
        archived = self.load_record(archive_key)
        record = {"revoked": intent["revoked"]}
        if archived is not None and archived != record:
            raise PropertyCatalogCoordinatorError(
                "supersession revocation record changed"
            )
        self.save_record(key, intent)
        # Retain revocation independently of the current pending-operation slot.
        # This blocks old activation even if a replica serves a stale FENCED row
        # after later supersessions have replaced that slot.
        if archived is None:
            self.save_record(archive_key, record)

    def is_revoked(self, key: str, plan_sha256: str) -> bool:
        return self.load_record(f"{key}:revoked:{plan_sha256}") is not None

    def save_record(self, key: str, intent: Mapping[str, Any]) -> None:
        document = {
            "key": key,
            "intent": intent,
            "sha256": framed_sha256(_SUPERSESSION_FORMAT, key, _journal_json(intent)),
        }
        raw = (_journal_json(document) + "\n").encode()
        if len(raw) > _MAX_SUPERSESSION_JOURNAL_BYTES:
            raise PropertyCatalogCoordinatorError(
                "supersession intent exceeds journal bound"
            )
        descriptor, temporary = tempfile.mkstemp(
            prefix=".supersession-", dir=self._directory
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path(key))
            directory_fd = os.open(self._directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _journal_json(document: Mapping[str, Any]) -> str:
    # The record contains several independently bounded build plans; the
    # default property-definition JSON bound is too small for this container.
    return canonical_json(document, max_bytes=_MAX_SUPERSESSION_JOURNAL_BYTES)


def _journal_row_document(row: Mapping[str, Any]) -> dict[str, Any]:
    # Normalize native CH UUID/DateTime values before persisting exact intent.
    return json.loads(
        json.dumps(
            dict(row),
            default=lambda value: (
                value.astimezone(UTC).isoformat(timespec="microseconds")
                if isinstance(value, datetime)
                else str(value)
            ),
        )
    )


def _journal_source_row(document: Mapping[str, Any]) -> dict[str, Any]:
    if set(document) != set(_SOURCE_STREAM_COLUMNS):
        raise PropertyCatalogCoordinatorError("supersession row columns changed")
    row = {column: document[column] for column in _SOURCE_STREAM_COLUMNS}
    for column in ("started_at", "updated_at", "drain_deadline", "fenced_at"):
        if row[column] is not None:
            row[column] = _datetime(row[column], column)
    return row


class ClickHouseRevisionCoordinator:
    """Reserve revisions, open exact streams, drain, and persist final fences."""

    def __init__(
        self,
        client: CatalogCoordinatorClient,
        *,
        database: str,
        serializer: CatalogMutationSerializer,
        producer_fence_sink: ProducerFenceSink,
        hot_producer_stream_id: str,
        deadline: SharedCatalogDeadline,
        lease_seconds: int = REVISION_LEASE_SECONDS,
        timeout_ms: int = COORDINATOR_TIMEOUT_MS,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        recovery_journal_directory: str | None = None,
    ) -> None:
        require_catalog_database(database)
        if getattr(client, "catalog_database", None) != database:
            raise ValueError("coordinator client database identity mismatch")
        if not callable(getattr(serializer, "serialize", None)):
            raise TypeError("coordinator requires a mutation serializer")
        if not callable(getattr(producer_fence_sink, "publish", None)):
            raise TypeError("coordinator requires a producer fence sink")
        if (
            type(lease_seconds) is not int
            or not 1 <= lease_seconds <= MAX_REVISION_LEASE_SECONDS
        ):
            raise ValueError(
                f"revision lease_seconds must be in [1, {MAX_REVISION_LEASE_SECONDS}]"
            )
        self._client = client
        self._database = database
        self._serializer = serializer
        self._sink = producer_fence_sink
        self._hot_stream_id = canonical_uuid(
            hot_producer_stream_id, field="hot_producer_stream_id"
        )
        self._deadline = deadline
        self._lease_seconds = lease_seconds
        self._timeout_ms = min(max(timeout_ms, 1), COORDINATOR_TIMEOUT_MS)
        self._now = now
        self._recovery_journal = (
            FileSupersessionJournal(recovery_journal_directory)
            if recovery_journal_directory is not None
            else None
        )
        self._publication_context: ContextVar[ActivationPublicationSession | None] = (
            ContextVar("property_catalog_activation_publication", default=None)
        )

    def publication_session(
        self, *, fence: RevisionFence
    ) -> ActivationPublicationSession:
        """Expose exact publication only while this coordinator owns the lock."""
        session = self._publication_context.get()
        if (
            session is None
            or session.lease.build_lease_sha256 != fence.build_lease_sha256
        ):
            raise PropertyCatalogCoordinatorError(
                "ACTIVE publication requires its workspace activation lock"
            )
        return session

    def recover_pending(
        self, *, organization_id: str, workspace_id: str, catalog_epoch: int
    ) -> None:
        """Finish only the exact journaled control writes, before state selection."""
        key = self._revision_key(
            organization_id=organization_id,
            workspace_id=workspace_id,
            catalog_epoch=catalog_epoch,
        )
        self._serializer.serialize(key, lambda: self._recover_pending_serialized(key))

    def _recover_pending_serialized(self, key: str) -> RevisionLease | None:
        if self._recovery_journal is None:
            return None
        intent = self._recovery_journal.load(key)
        if intent is None or intent["complete"]:
            return None
        revoked = _journal_source_row(intent["revoked"])
        replacement = _journal_source_row(intent["replacement"])
        old_lease, new_lease = _row_lease(revoked), _row_lease(replacement)
        if self._revision_key_for_lease(old_lease) != key or (
            self._revision_key_for_lease(new_lease) != key
            or new_lease.catalog_revision != old_lease.catalog_revision + 1
            or new_lease.build_token == old_lease.build_token
            or revoked["status"] != "failed"
            or revoked["_version"] != _SUPERSESSION_VERSION
            or replacement["status"] != "open"
            or replacement["_version"] != 1
        ):
            raise PropertyCatalogCoordinatorError("invalid supersession intent scope")
        self._assert_active_head(old_lease, intent["active_head"])
        self._recovery_journal.save(key, intent)
        # A failed, terminal-version reservation dominates any delayed old
        # control row. Late DATA rows stay in the old revision/token namespace.
        self._ensure_supersession_row(revoked, predecessor=intent["predecessor"])
        self._ensure_supersession_row(replacement, predecessor=None)
        self._assert_active_head(old_lease, intent["active_head"])
        self._recovery_journal.save(key, {**intent, "complete": True})
        return new_lease

    def _ensure_supersession_row(
        self, row: Mapping[str, Any], *, predecessor: Mapping[str, Any] | None
    ) -> None:
        lease = _row_lease(row)
        token = (
            f"property-catalog-supersession-v1:{lease.build_token}:{row['_version']}"
        )
        current = self._read_stream(
            lease=lease,
            source_adapter=SourceAdapter.SYSTEM_MANIFEST,
            producer_stream_id=lease.build_token,
        )
        if (
            current is not None
            and _row_identity(current) == _row_identity(row)
            and (_uint(current.get("_version"), "_version") == row["_version"])
        ):
            self._confirm_supersession_receipt(row, deduplication_token=token)
            return
        if predecessor is None:
            if current is not None:
                raise PropertyCatalogCoordinatorError("replacement reservation changed")
        elif current is not None and _journal_row_document(current) != predecessor:
            # The CAS already committed to the fsynced intent under the lock.
            # A delayed, lower-version fence may arrive after that point. It
            # cannot activate (the journal veto is permanent), so quarantine it
            # too; never reinterpret contradictory bytes at the observed version.
            current_version = _uint(current.get("_version"), "_version")
            if (
                current_version == predecessor["_version"]
                or current_version >= _SUPERSESSION_VERSION
                or _row_lease(current) != lease
                or _text(current.get("status")) not in {"open", "draining", "fenced"}
                or _uint(current.get("gap_count"), "gap_count")
            ):
                raise PropertyCatalogCoordinatorError(
                    "supersession reservation CAS failed"
                )
        # Submit the frozen intent through the writer. A managed writer settles
        # an already-sent attempt from its original receipt without resending it;
        # this visibility read alone never proves an uncertain write completed.
        self._insert(
            _SOURCE_STREAM_TABLE,
            row,
            deduplication_token=token,
        )
        verified = self._read_stream(
            lease=lease,
            source_adapter=SourceAdapter.SYSTEM_MANIFEST,
            producer_stream_id=lease.build_token,
        )
        if (
            verified is None
            or _row_identity(verified) != _row_identity(row)
            or (_uint(verified.get("_version"), "_version") != row["_version"])
        ):
            raise PropertyCatalogCoordinatorError(
                "supersession write remains uncertain"
            )
        self._confirm_supersession_receipt(row, deduplication_token=token)

    def _confirm_supersession_receipt(
        self, row: Mapping[str, Any], *, deduplication_token: str
    ) -> None:
        confirm = getattr(self._client, "confirm_reservation_receipt", None)
        if callable(confirm):
            self._validate_target()
            confirm(
                _qualified(self._database, _SOURCE_STREAM_TABLE),
                (row,),
                columns=tuple(row),
                timeout_ms=self._deadline.remaining_ms(cap_ms=self._timeout_ms),
                deduplication_token=deduplication_token,
            )

    def _assert_active_head(self, lease: RevisionLease, expected: Any) -> None:
        if self._recovery_journal is not None:
            started = self._recovery_journal.load_record(
                self._revision_key_for_lease(lease) + ":activation"
            )
            if started is not None and (
                expected is None
                or started["catalog_revision"] > expected[0]
                or (
                    started["catalog_revision"] == expected[0]
                    and started["build_token"] != expected[1]
                )
            ):
                if self._confirm_terminal_marker(lease, started) is None:
                    raise PropertyCatalogCoordinatorError(
                        "activation outcome is unresolved or active lineage advanced"
                    )
        # Compare a trustworthy ACTIVE predecessor, not the highest consumed
        # slot (which may now be disabled/building). This read holds no new
        # lock: callers already own the workspace lock. The same bounded SQL
        # and all-status conflict checks used for publication retain allocation
        # maxima independently; nothing here retires a publication marker.
        self._validate_target()
        try:
            history = ClickHouseCatalogStateStore(
                self._client,
                database=self._database,
                serializer=self._serializer,
                deadline=self._deadline,
                timeout_ms=self._timeout_ms,
            ).load_activation_history(
                organization_id=lease.organization_id,
                workspace_id=lease.workspace_id,
                catalog_epoch=lease.catalog_epoch,
            )
        except (PropertyCatalogStateError, TypeError, ValueError) as exc:
            raise PropertyCatalogCoordinatorError(
                "supersession active head is ambiguous or invalid"
            ) from exc
        if history.maximum_revision > lease.catalog_revision:
            # Supersession allocates old revision + 1. Filtering a newer
            # invalidated build out of the predecessor must not permit reuse.
            raise PropertyCatalogCoordinatorError(
                "a newer revision exists before supersession"
            )
        latest = max(
            history.active_records,
            key=lambda record: record.activation_sequence,
            default=None,
        )
        actual = (
            None
            if latest is None
            else [
                latest.catalog_revision,
                latest.build_token,
                latest.projection_version,
                latest.activation_sequence,
                latest.activation_sha256,
                "active",
            ]
        )
        if actual != expected:
            raise PropertyCatalogCoordinatorError(
                "active lineage changed before supersession"
            )

    def _confirm_terminal_marker(self, lease: RevisionLease, marker: Any):
        """Exact native proof only, inside the caller's existing workspace lock."""
        from .durable_native_writer import DurableNativeCatalogWriter
        from .terminal_repair_execution import NativeTerminalRepairExecutor

        writer = getattr(self._client, "_durable_writer", None)
        if type(writer) is not DurableNativeCatalogWriter:
            return None
        return NativeTerminalRepairExecutor(writer, self).confirm_marker_serialized(
            lease,
            marker,
            timeout_ms=self._deadline.remaining_ms(cap_ms=self._timeout_ms),
        )

    def source_snapshot_invalid(self, lease: RevisionLease) -> bool:
        """A code-owned audit failure, not an operator-set expiry override."""
        if self._recovery_journal is None:
            return False
        key = f"{self._revision_key_for_lease(lease)}:source-invalid:{lease.build_lease_sha256}"
        expected = {
            "format": "futureagi.property-catalog-source-invalid.v1",
            "build_lease_sha256": lease.build_lease_sha256,
            "reason": "independent_source_audit_changed",
        }
        record = self._recovery_journal.load_record(key)
        if record is not None and record != expected:
            raise PropertyCatalogCoordinatorError(
                "source invalidation record is corrupt"
            )
        return record is not None

    def invalidate_source_snapshot(self, lease: RevisionLease) -> None:
        """Veto a never-fenced build after its authoritative audit changed.

        Persist before returning failure. The next cycle can use the existing
        journaled replacement/CAS protocol without waiting for lease expiry.
        Payload/checkpoint rows remain isolated under the old build identity.
        """
        if self._recovery_journal is None:
            raise PropertyCatalogCoordinatorError(
                "source invalidation requires a durable journal"
            )
        key = self._revision_key_for_lease(lease)

        def invalidate_serialized() -> None:
            if self.source_snapshot_invalid(lease):
                return
            started = self._recovery_journal.load_record(key + ":activation")
            if (
                started is not None
                and started["catalog_revision"] >= lease.catalog_revision
            ):
                raise PropertyCatalogCoordinatorError(
                    "cannot invalidate a possibly activated snapshot"
                )
            # FENCED may already have an uncertain activation append. Never
            # revoke it, nor bypass the reservation's exact scope/lease checks.
            self._assert_reservation(lease, expected_status=("open", "draining"))
            self._recovery_journal.save_record(
                f"{key}:source-invalid:{lease.build_lease_sha256}",
                {
                    "format": "futureagi.property-catalog-source-invalid.v1",
                    "build_lease_sha256": lease.build_lease_sha256,
                    "reason": "independent_source_audit_changed",
                },
            )

        self._serializer.serialize(key, invalidate_serialized)

    def serialize_source_capture_retirement(
        self,
        *,
        database: str,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        build_token: str,
        build_lease_sha256: str,
        operation: Callable[[], None],
    ) -> bool:
        """Run derived-source cleanup under an exact permanent build veto.

        Audit invalidation or an archived terminal reservation prevents the old
        publisher from resuming. Neither proves native repair completion; this
        permission is solely for deleting the build's derived source capture.
        """
        if database != self._database or self._recovery_journal is None:
            raise PropertyCatalogCoordinatorError(
                "source cleanup requires the bound durable coordinator"
            )
        for name, value in (
            ("organization_id", organization_id),
            ("workspace_id", workspace_id),
            ("build_token", build_token),
        ):
            if canonical_uuid(value, field=name) != value:
                raise ValueError(f"{name} must be canonical")
        if type(catalog_epoch) is not int or not 1 <= catalog_epoch < (1 << 16):
            raise ValueError("catalog_epoch must be a positive UInt16")
        require_sha256(build_lease_sha256, field="build_lease_sha256")
        if not callable(operation):
            raise TypeError("source cleanup requires an operation")
        key = self._revision_key(
            organization_id=organization_id,
            workspace_id=workspace_id,
            catalog_epoch=catalog_epoch,
        )

        def cleanup_serialized() -> bool:
            record = self._recovery_journal.load_record(
                f"{key}:source-invalid:{build_lease_sha256}"
            )
            if record is not None and record != {
                "format": "futureagi.property-catalog-source-invalid.v1",
                "build_lease_sha256": build_lease_sha256,
                "reason": "independent_source_audit_changed",
            }:
                raise PropertyCatalogCoordinatorError(
                    "source invalidation record is corrupt"
                )
            if record is None:
                archived = self._recovery_journal.load_record(
                    f"{key}:revoked:{build_lease_sha256}"
                )
                if archived is None:
                    return False
                try:
                    if set(archived) != {"revoked"}:
                        raise ValueError("revocation fields changed")
                    row = _journal_source_row(archived["revoked"])
                    lease = _row_lease(row)
                    if (
                        self._revision_key_for_lease(lease) != key
                        or lease.build_token != build_token
                        or lease.build_lease_sha256 != build_lease_sha256
                        or row["build_token"] != build_token
                        or row["producer_stream_id"] != build_token
                        or row["source_adapter"] != str(SourceAdapter.SYSTEM_MANIFEST)
                        or type(row["envelope_version"]) is not int
                        or row["envelope_version"] != 0
                        or row["status"] != "failed"
                        or type(row["_version"]) is not int
                        or row["_version"] != _SUPERSESSION_VERSION
                    ):
                        raise ValueError("revocation differs from captured build")
                except (KeyError, TypeError, ValueError) as exc:
                    raise PropertyCatalogCoordinatorError(
                        "source cleanup revocation record is invalid"
                    ) from exc
            operation()
            return True

        return self._serializer.serialize(key, cleanup_serialized)

    def replace_expired(
        self,
        *,
        expired_reservation: PersistedReservation,
        prior_active: PriorActiveEvidence | None,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        projection_version: int,
        build_token: str,
        source_scope: BuildPlanSourceScope,
        planned_streams: tuple[BuildPlanStream, ...],
        now: datetime,
    ) -> RevisionLease:
        """Replace expired or source-invalidated work under fencing/activation CAS.

        This is an application CAS under the shared lock, not a CH transaction.
        Both control records use the existing source-stream log. Payload rows
        and checkpoints of the abandoned build are neither copied nor deleted.
        """
        if self._recovery_journal is None:
            raise PropertyCatalogCoordinatorError(
                "expired recovery requires a shared durable journal directory"
            )
        key = self._revision_key(
            organization_id=organization_id,
            workspace_id=workspace_id,
            catalog_epoch=catalog_epoch,
        )
        old = expired_reservation.lease
        if (
            key != self._revision_key_for_lease(old)
            or projection_version != old.projection_version
        ):
            raise PropertyCatalogCoordinatorError(
                "supersession changed workspace scope"
            )
        self._validate_planned_streams(planned_streams)
        _require_utc(now, "now")
        if any(
            stream.key == (SourceAdapter.SYSTEM_MANIFEST, build_token)
            for stream in planned_streams
        ):
            raise PropertyCatalogCoordinatorError(
                "replacement stream collides with reservation"
            )

        def replace_serialized() -> RevisionLease:
            recovered = self._recover_pending_serialized(key)
            if recovered is not None:
                return recovered
            prior_intent = self._recovery_journal.load(key)
            if prior_intent is not None and (
                prior_intent["revoked"]["build_token"] == old.build_token
            ):
                return _row_lease(_journal_source_row(prior_intent["replacement"]))
            current_time = self._now()
            _require_utc(current_time, "coordinator now")
            if (
                old.expires_at > current_time or old.expires_at > now
            ) and not self.source_snapshot_invalid(old):
                raise PropertyCatalogCoordinatorError(
                    "only expired reservations or source-invalidated builds can be superseded"
                )
            current = self._assert_reservation(
                old, expected_status=("open", "draining"), allow_source_invalidated=True
            )
            if expired_reservation.state_version is None or (
                _uint(current.get("_version"), "_version")
                != expired_reservation.state_version
            ):
                raise PropertyCatalogCoordinatorError(
                    "supersession reservation version CAS failed"
                )
            if (
                _text(current.get("status")) != expired_reservation.status.value
                or _row_lease(current) != old
            ):
                raise PropertyCatalogCoordinatorError(
                    "supersession reservation CAS failed"
                )
            active_head = (
                None
                if prior_active is None
                else [
                    prior_active.catalog_revision,
                    prior_active.build_token,
                    prior_active.projection_version,
                    prior_active.activation_sequence,
                    prior_active.activation_sha256,
                    "active",
                ]
            )
            self._assert_active_head(old, active_head)
            if (
                prior_active is not None
                and prior_active.catalog_revision >= old.catalog_revision
            ):
                raise PropertyCatalogCoordinatorError(
                    "cannot supersede an active revision"
                )
            for table in (_SOURCE_STREAM_TABLE, _CHECKPOINT_TABLE):
                maximum = self._query(
                    "SELECT coalesce(max(catalog_revision), 0) AS max_revision "
                    f"FROM {_qualified(self._database, table)} "
                    "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
                    "AND catalog_epoch=%(catalog_epoch)s",
                    {
                        "organization_id": organization_id,
                        "workspace_id": workspace_id,
                        "catalog_epoch": catalog_epoch,
                    },
                )
                if (
                    len(maximum) != 1
                    or _uint(maximum[0].get("max_revision"), "max_revision")
                    > old.catalog_revision
                ):
                    raise PropertyCatalogCoordinatorError(
                        "a newer revision exists before supersession"
                    )
            if build_token == old.build_token or (
                prior_active is not None and build_token == prior_active.build_token
            ):
                raise PropertyCatalogCoordinatorError(
                    "supersession requires a fresh token"
                )
            plan = RevisionBuildPlan(
                organization_id=organization_id,
                workspace_id=workspace_id,
                catalog_epoch=catalog_epoch,
                catalog_revision=old.catalog_revision + 1,
                projection_version=projection_version,
                build_token=build_token,
                source_scope=source_scope,
                streams=planned_streams,
            )
            lease = RevisionLease(
                organization_id=organization_id,
                workspace_id=workspace_id,
                catalog_epoch=catalog_epoch,
                catalog_revision=plan.catalog_revision,
                projection_version=projection_version,
                build_token=build_token,
                build_plan_json=plan.canonical_json,
                build_lease_sha256=plan.sha256,
                issued_at=current_time,
                expires_at=current_time + timedelta(seconds=self._lease_seconds),
            )
            replacement = _stream_row(
                lease=lease,
                source_adapter=SourceAdapter.SYSTEM_MANIFEST,
                producer_stream_id=build_token,
                envelope_version=0,
                status="open",
                now=lease.issued_at,
                drain_deadline=lease.expires_at,
            )
            revoked = dict(current)
            revoked.update(
                status="failed",
                _version=_SUPERSESSION_VERSION,
                updated_at=current_time,
                gap_count=1,
                gap_reasons=[
                    _journal_json(
                        {
                            "format": _SUPERSESSION_FORMAT,
                            "replacement": _journal_row_document(replacement),
                            "active_head": active_head,
                        }
                    )
                ],
            )
            intent = {
                "format": _SUPERSESSION_FORMAT,
                "complete": False,
                "predecessor": _journal_row_document(current),
                "revoked": _journal_row_document(revoked),
                "replacement": _journal_row_document(replacement),
                "active_head": active_head,
            }
            self._recovery_journal.save(key, intent)
            result = self._recover_pending_serialized(key)
            assert result is not None
            return result

        return self._serializer.serialize(key, replace_serialized)

    def serialize_activation(
        self, *, fence: RevisionFence, operation: Callable[[], _T]
    ) -> _T:
        """Hold the workspace lock through the activation store's append.

        All activation callers must use this boundary. Supersession never
        revokes FENCED reservations, including ambiguous activation appends.
        """
        plan = RevisionBuildPlan.from_json(fence.build_plan_json)
        if self._recovery_journal is None:
            raise PropertyCatalogCoordinatorError(
                "activation requires a shared durable journal directory"
            )
        key = self._revision_key(
            organization_id=plan.organization_id,
            workspace_id=plan.workspace_id,
            catalog_epoch=plan.catalog_epoch,
        )

        def activate_serialized() -> _T:
            self._recover_pending_serialized(key)
            if self._recovery_journal.is_revoked(key, plan.sha256):
                raise PropertyCatalogCoordinatorError(
                    "activation revision was durably superseded"
                )
            rows = self._query(
                f"SELECT {', '.join(_SOURCE_STREAM_COLUMNS)} "
                f"FROM {_qualified(self._database, _SOURCE_STREAM_TABLE)} "
                "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
                "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s "
                "AND build_token=%(build_token)s AND source_adapter=%(source_adapter)s "
                "AND producer_stream_id=%(producer_stream_id)s ORDER BY _version DESC LIMIT %(row_limit)s",
                {
                    "organization_id": plan.organization_id,
                    "workspace_id": plan.workspace_id,
                    "catalog_epoch": plan.catalog_epoch,
                    "catalog_revision": plan.catalog_revision,
                    "build_token": plan.build_token,
                    "source_adapter": str(SourceAdapter.SYSTEM_MANIFEST),
                    "producer_stream_id": plan.build_token,
                    "row_limit": _MAX_STATE_VARIANTS + 1,
                },
            )
            _require_below_row_cap(
                rows, cap=_MAX_STATE_VARIANTS, label="activation reservation"
            )
            row = _latest(rows)
            if (
                row is None
                or _text(row.get("status")) != "fenced"
                or (
                    _row_lease(row).build_plan != plan
                    or row.get("fenced_at") is None
                    or _uint(row.get("envelope_version"), "envelope_version") != 0
                )
            ):
                raise PropertyCatalogCoordinatorError(
                    "activation reservation is not fenced or was superseded"
                )
            if self.source_snapshot_invalid(_row_lease(row)):
                raise PropertyCatalogCoordinatorError(
                    "activation source snapshot was invalidated"
                )
            # Persist before invoking the activation store. An ambiguous append
            # cannot then be mistaken for an inactive build by a stale replica.
            activation_key = key + ":activation"
            started = self._recovery_journal.load_record(activation_key)
            marker = {
                "catalog_revision": plan.catalog_revision,
                "build_token": plan.build_token,
                "build_lease_sha256": plan.sha256,
            }
            if (
                started is not None
                and started["catalog_revision"] == plan.catalog_revision
                and started != marker
            ):
                raise PropertyCatalogCoordinatorError(
                    "activation intent conflicts with this build"
                )
            # Persist a forward-protocol admission BEFORE the legacy high-water
            # marker. A crash before exact-row preparation cannot masquerade as
            # an old, possibly sent three-field intent on restart.
            publication = ActivationPublicationSession(
                self._recovery_journal,
                workspace_key=key,
                database=self._database,
                lease=_row_lease(row),
                marker=started,
                confirm_terminal_repair=lambda marker: self._confirm_terminal_marker(
                    _row_lease(row), marker
                ),
            )
            if started is None or started["catalog_revision"] < plan.catalog_revision:
                self._recovery_journal.save_record(activation_key, marker)
            context_token = self._publication_context.set(publication)
            try:
                return operation()
            finally:
                self._publication_context.reset(context_token)

        return self._serializer.serialize(key, activate_serialized)

    def allocate(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        projection_version: int,
        build_token: str,
        source_scope: BuildPlanSourceScope,
        planned_streams: tuple[BuildPlanStream, ...],
        now: datetime,
    ) -> RevisionLease:
        organization_id = canonical_uuid(organization_id, field="organization_id")
        workspace_id = canonical_uuid(workspace_id, field="workspace_id")
        build_token = canonical_uuid(build_token, field="build_token")
        if not isinstance(source_scope, BuildPlanSourceScope):
            raise TypeError("source_scope must be BuildPlanSourceScope")
        self._validate_planned_streams(planned_streams)
        if any(
            stream.key == (SourceAdapter.SYSTEM_MANIFEST, build_token)
            for stream in planned_streams
        ):
            raise PropertyCatalogCoordinatorError(
                "planned stream collides with the revision reservation identity"
            )
        _require_utc(now, "now")
        return self._serializer.serialize(
            self._revision_key(
                organization_id=organization_id,
                workspace_id=workspace_id,
                catalog_epoch=catalog_epoch,
            ),
            lambda: self._allocate_serialized(
                organization_id=organization_id,
                workspace_id=workspace_id,
                catalog_epoch=catalog_epoch,
                projection_version=projection_version,
                build_token=build_token,
                source_scope=source_scope,
                planned_streams=planned_streams,
                now=now,
            ),
        )

    def _allocate_serialized(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        projection_version: int,
        build_token: str,
        source_scope: BuildPlanSourceScope,
        planned_streams: tuple[BuildPlanStream, ...],
        now: datetime,
    ) -> RevisionLease:
        self._recover_pending_serialized(
            self._revision_key(
                organization_id=organization_id,
                workspace_id=workspace_id,
                catalog_epoch=catalog_epoch,
            )
        )
        rows = self._query(
            f"SELECT {', '.join(_SOURCE_STREAM_COLUMNS)} FROM ("
            f"SELECT {', '.join(_SOURCE_STREAM_COLUMNS)}, "
            "dense_rank() OVER (PARTITION BY catalog_revision, build_token "
            "ORDER BY _version DESC) AS reservation_version_rank "
            f"FROM {_qualified(self._database, _SOURCE_STREAM_TABLE)} "
            "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
            "AND catalog_epoch=%(catalog_epoch)s AND envelope_version=0 "
            "AND producer_stream_id=build_token) "
            "WHERE reservation_version_rank=1 AND ("
            "(status IN ('open','draining') AND fenced_at IS NULL "
            "AND drain_deadline > %(now)s) OR build_token=%(build_token)s) "
            "ORDER BY catalog_revision DESC, build_token LIMIT %(row_limit)s",
            {
                "organization_id": organization_id,
                "workspace_id": workspace_id,
                "catalog_epoch": catalog_epoch,
                "build_token": build_token,
                "now": now,
                "row_limit": _MAX_RESERVATION_CANDIDATES + 1,
            },
        )
        _require_below_row_cap(
            rows,
            cap=_MAX_RESERVATION_CANDIDATES,
            label="revision reservation candidates",
        )
        reservations = tuple(
            row
            for row in rows
            if _uint(row.get("envelope_version"), "envelope_version") == 0
            and _text(row.get("producer_stream_id")) == _text(row.get("build_token"))
        )
        if len(reservations) != len(rows):
            raise PropertyCatalogCoordinatorError(
                "reservation query returned a non-reservation source stream"
            )
        reservation_groups: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
        for candidate in reservations:
            key = (
                _uint(candidate.get("catalog_revision"), "catalog_revision"),
                _text(candidate.get("build_token")),
            )
            reservation_groups.setdefault(key, []).append(candidate)
        latest_reservations = tuple(
            latest
            for values in reservation_groups.values()
            if (latest := _latest(values)) is not None
        )
        live_reservations = tuple(
            candidate
            for candidate in latest_reservations
            if _text(candidate.get("status")) in {"open", "draining"}
            and candidate.get("fenced_at") is None
            and _datetime(candidate.get("drain_deadline"), "drain_deadline") > now
        )
        if len(live_reservations) > 1:
            raise PropertyCatalogCoordinatorError(
                "workspace has multiple live nonterminal revision reservations"
            )
        if (
            live_reservations
            and _text(live_reservations[0].get("build_token")) != build_token
        ):
            raise PropertyCatalogCoordinatorError(
                "workspace already has another live nonterminal revision reservation"
            )
        matching_reservations = tuple(
            row
            for row in latest_reservations
            if _text(row.get("build_token")) == build_token
        )
        if len(matching_reservations) > 1:
            raise PropertyCatalogCoordinatorError(
                "build token is reused across catalog revisions"
            )
        row = _latest(matching_reservations)
        if row is not None:
            deadline = _datetime(row.get("drain_deadline"), "drain_deadline")
            expected_plan = RevisionBuildPlan(
                organization_id=organization_id,
                workspace_id=workspace_id,
                catalog_epoch=catalog_epoch,
                catalog_revision=_uint(row.get("catalog_revision"), "catalog_revision"),
                projection_version=projection_version,
                build_token=build_token,
                source_scope=source_scope,
                streams=planned_streams,
            )
            if (
                _text(row.get("build_plan_json")) == expected_plan.canonical_json
                and _text(row.get("build_lease_sha256")) == expected_plan.sha256
                and _uint(row.get("projection_version"), "projection_version")
                == projection_version
                and _text(row.get("status")) in {"open", "draining"}
                and deadline > now
            ):
                lease = RevisionLease(
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    catalog_epoch=catalog_epoch,
                    catalog_revision=_uint(
                        row.get("catalog_revision"), "catalog_revision"
                    ),
                    projection_version=projection_version,
                    build_token=build_token,
                    build_plan_json=expected_plan.canonical_json,
                    build_lease_sha256=expected_plan.sha256,
                    issued_at=_datetime(row.get("started_at"), "started_at"),
                    expires_at=deadline,
                )
                persisted_reservation = self._assert_reservation(
                    lease,
                    expected_status=("open", "draining"),
                )
                if _row_identity(persisted_reservation) != _row_identity(row):
                    raise PropertyCatalogCoordinatorError(
                        "revision reservation changed during allocation"
                    )
                if _text(row.get("status")) == "open":
                    # The producer must not see building until every immutable
                    # stream row exists. publish_building_assignment performs
                    # that exact-inventory admission after open_stream calls.
                    pass
                else:
                    inventory = self._assert_inventory(
                        lease, require_all=True, expected_status="draining"
                    )
                    hot = inventory[(SourceAdapter.SPAN_ATTRIBUTE, self._hot_stream_id)]
                    self._sink.publish(
                        _draining_assignment(
                            lease,
                            drain_deadline=deadline,
                            fenced_sequence=_uint(
                                hot.get("fenced_sequence"), "fenced_sequence"
                            ),
                        )
                    )
                return lease
            raise PropertyCatalogCoordinatorError(
                "build token already has another or expired reservation"
            )
        source_maximum_rows = self._query(
            "SELECT coalesce(max(catalog_revision), 0) AS max_revision "
            f"FROM {_qualified(self._database, _SOURCE_STREAM_TABLE)} "
            "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
            "AND catalog_epoch=%(catalog_epoch)s",
            {
                "organization_id": organization_id,
                "workspace_id": workspace_id,
                "catalog_epoch": catalog_epoch,
            },
        )
        if len(source_maximum_rows) != 1:
            raise PropertyCatalogCoordinatorError(
                "source-stream max revision query did not return one row"
            )
        source_maximum = _uint(
            source_maximum_rows[0].get("max_revision"), "max_revision"
        )
        other_revisions = []
        for table in (_ACTIVATION_TABLE, _CHECKPOINT_TABLE):
            result = self._query(
                "SELECT catalog_revision "
                f"FROM {_qualified(self._database, table)} "
                "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
                "AND catalog_epoch=%(catalog_epoch)s "
                "ORDER BY catalog_revision DESC LIMIT 1",
                {
                    "organization_id": organization_id,
                    "workspace_id": workspace_id,
                    "catalog_epoch": catalog_epoch,
                },
            )
            other_revisions.extend(
                _uint(row.get("catalog_revision"), "catalog_revision") for row in result
            )
        maximum = max(
            (
                source_maximum,
                *other_revisions,
            ),
            default=0,
        )
        if maximum >= (1 << 64) - 1:
            raise PropertyCatalogCoordinatorError("catalog revision space is exhausted")
        build_plan = RevisionBuildPlan(
            organization_id=organization_id,
            workspace_id=workspace_id,
            catalog_epoch=catalog_epoch,
            catalog_revision=maximum + 1,
            projection_version=projection_version,
            build_token=build_token,
            source_scope=source_scope,
            streams=planned_streams,
        )
        lease = RevisionLease(
            organization_id=organization_id,
            workspace_id=workspace_id,
            catalog_epoch=catalog_epoch,
            catalog_revision=maximum + 1,
            projection_version=projection_version,
            build_token=build_token,
            build_plan_json=build_plan.canonical_json,
            build_lease_sha256=build_plan.sha256,
            issued_at=now,
            expires_at=now + timedelta(seconds=self._lease_seconds),
        )
        reservation = _stream_row(
            lease=lease,
            source_adapter=SourceAdapter.SYSTEM_MANIFEST,
            producer_stream_id=build_token,
            envelope_version=0,
            status="open",
            now=now,
            drain_deadline=lease.expires_at,
        )
        self._append_stream_state(reservation)
        return lease

    def open_stream(
        self,
        *,
        lease: RevisionLease,
        source_adapter: SourceAdapter,
        producer_stream_id: str,
    ) -> CatalogWriteLease:
        self._require_live_lease(lease)
        producer_stream_id = canonical_uuid(
            producer_stream_id, field="producer_stream_id"
        )
        if (source_adapter, producer_stream_id) not in {
            stream.key for stream in lease.build_plan.streams
        }:
            raise PropertyCatalogCoordinatorError(
                "source stream is not admitted by the immutable build plan"
            )

        def open_serialized() -> None:
            reservation = self._assert_reservation(
                lease, expected_status=("open", "draining")
            )
            status = _text(reservation.get("status"))
            inventory = self._assert_inventory(
                lease,
                require_all=status == "draining",
                expected_status="draining" if status == "draining" else None,
            )
            if status == "open":
                self._append_stream_state(
                    _stream_row(
                        lease=lease,
                        source_adapter=source_adapter,
                        producer_stream_id=producer_stream_id,
                        envelope_version=1,
                        status="open",
                        now=lease.issued_at,
                        drain_deadline=lease.expires_at,
                    )
                )
            elif (source_adapter, producer_stream_id) not in inventory:
                raise PropertyCatalogCoordinatorError(
                    "draining revision is missing a planned source stream"
                )

        self._serializer.serialize(
            self._revision_key_for_lease(lease),
            open_serialized,
        )
        return CatalogWriteLease(
            organization_id=lease.organization_id,
            workspace_id=lease.workspace_id,
            catalog_epoch=lease.catalog_epoch,
            catalog_revision=lease.catalog_revision,
            build_token=lease.build_token,
            projection_version=lease.projection_version,
            source_adapter=source_adapter,
            producer_stream_id=producer_stream_id,
            build_plan_json=lease.build_plan_json,
            build_lease_sha256=lease.build_lease_sha256,
            expires_at=lease.expires_at,
        )

    def publish_building_assignment(
        self, *, lease: RevisionLease
    ) -> ProducerRevisionAssignment:
        """Admit Go hot traffic only after all ten stream rows are open."""

        self._require_live_lease(lease)
        assignment: ProducerRevisionAssignment | None = None

        def publish_serialized() -> None:
            nonlocal assignment
            reservation = self._assert_reservation(
                lease, expected_status=("open", "draining")
            )
            status = _text(reservation.get("status"))
            inventory = self._assert_inventory(
                lease, require_all=True, expected_status=status
            )
            if status == "open":
                assignment = _building_assignment(lease)
            else:
                hot = inventory[(SourceAdapter.SPAN_ATTRIBUTE, self._hot_stream_id)]
                assignment = _draining_assignment(
                    lease,
                    drain_deadline=lease.expires_at,
                    fenced_sequence=_uint(
                        hot.get("fenced_sequence"), "fenced_sequence"
                    ),
                )
            self._sink.publish(assignment)

        self._serializer.serialize(
            self._revision_key_for_lease(lease), publish_serialized
        )
        assert assignment is not None
        return assignment

    def begin_drain_intent(
        self,
        *,
        lease: RevisionLease,
        completed_stream_proofs: tuple[StreamDrainProof, ...],
        drain_deadline: datetime,
        now: datetime,
    ) -> ProducerRevisionAssignment:
        """Close the revision without inventing the hot producer high-water."""

        self._require_live_lease(lease)
        _require_utc(now, "now")
        _require_utc(drain_deadline, "drain_deadline")
        if (
            not now
            < drain_deadline
            <= lease.issued_at + timedelta(seconds=MAX_REVISION_LEASE_SECONDS)
            or drain_deadline != lease.expires_at
        ):
            raise PropertyCatalogCoordinatorError(
                "drain deadline must equal the immutable lease expiry"
            )
        hot_key = (SourceAdapter.SPAN_ATTRIBUTE, self._hot_stream_id)
        expected = {stream.key for stream in lease.build_plan.streams} - {hot_key}
        proofs = {proof.key: proof for proof in completed_stream_proofs}
        if len(proofs) != len(completed_stream_proofs) or set(proofs) != expected:
            raise PropertyCatalogCoordinatorError(
                "drain intent requires every non-hot stream exactly once"
            )
        assignment = _draining_assignment(
            lease, drain_deadline=drain_deadline, fenced_sequence=0
        )

        def drain_serialized() -> None:
            nonlocal assignment
            reservation = self._assert_reservation(
                lease, expected_status=("open", "draining")
            )
            reservation_status = _text(reservation.get("status"))
            inventory = self._assert_inventory(
                lease,
                require_all=True,
                expected_status="open" if reservation_status == "open" else "draining",
            )
            if reservation_status == "open":
                targets: list[Mapping[str, Any]] = []
                for key in sorted(inventory):
                    if key == hot_key:
                        targets.append(
                            self._transition_stream_row(
                                existing=inventory[key],
                                lease=lease,
                                status="draining",
                                first_sequence=0,
                                last_sequence=0,
                                last_issued_sequence=0,
                                fenced_sequence=0,
                                terminal_payload_sha256=_ZERO_SHA256,
                                drain_deadline=drain_deadline,
                                fenced_at=None,
                                now=now,
                            )
                        )
                        continue
                    proof = proofs[key]
                    targets.append(
                        self._transition_stream_row(
                            existing=inventory[key],
                            lease=lease,
                            status="draining",
                            first_sequence=1,
                            last_sequence=proof.terminal_sequence,
                            last_issued_sequence=proof.last_issued_sequence,
                            fenced_sequence=proof.fenced_sequence,
                            terminal_payload_sha256=proof.terminal_payload_sha256,
                            drain_deadline=drain_deadline,
                            fenced_at=None,
                            now=now,
                        )
                    )
                targets.append(
                    self._transition_reservation_row(
                        existing=reservation,
                        lease=lease,
                        status="draining",
                        drain_deadline=drain_deadline,
                        fenced_at=None,
                        now=now,
                    )
                )
                self._append_revision_states_atomic(lease, tuple(targets))
            else:
                if (
                    _datetime(reservation.get("drain_deadline"), "drain_deadline")
                    != drain_deadline
                ):
                    raise PropertyCatalogCoordinatorError(
                        "revision is already draining under another deadline"
                    )
                for key, row in inventory.items():
                    proof = proofs.get(key)
                    if key == hot_key:
                        boundary = _uint(row.get("fenced_sequence"), "fenced_sequence")
                        expected_values = (0, boundary, boundary, _ZERO_SHA256)
                        assignment = _draining_assignment(
                            lease,
                            drain_deadline=drain_deadline,
                            fenced_sequence=boundary,
                        )
                    else:
                        assert proof is not None
                        expected_values = (
                            proof.terminal_sequence,
                            proof.last_issued_sequence,
                            proof.fenced_sequence,
                            proof.terminal_payload_sha256,
                        )
                    if not _stream_transition_matches(
                        row,
                        lease=lease,
                        first_sequence=0 if key == hot_key else 1,
                        last_sequence=expected_values[0],
                        last_issued_sequence=expected_values[1],
                        fenced_sequence=expected_values[2],
                        terminal_payload_sha256=expected_values[3],
                        drain_deadline=drain_deadline,
                    ):
                        raise PropertyCatalogCoordinatorError(
                            "drain intent evidence changed on replay"
                        )
            self._assert_inventory(lease, require_all=True, expected_status="draining")
            self._sink.publish(assignment)

        self._serializer.serialize(
            self._revision_key_for_lease(lease), drain_serialized
        )
        return assignment

    def bind_hot_drain_boundary(
        self,
        *,
        lease: RevisionLease,
        prepared_proof: Any,
        drain_deadline: datetime,
        now: datetime,
    ) -> ProducerRevisionAssignment:
        """Bind the exact producer boundary only after raw CH data catch-up."""

        _require_utc(now, "now")
        _require_utc(drain_deadline, "drain_deadline")
        if drain_deadline != lease.expires_at or now >= drain_deadline:
            raise PropertyCatalogCoordinatorError(
                "hot boundary bind requires the live immutable lease expiry"
            )
        intent = _draining_assignment(
            lease, drain_deadline=drain_deadline, fenced_sequence=0
        )
        if not callable(getattr(prepared_proof, "validate_prepared", None)):
            raise TypeError("prepared_proof must implement the v2 runtime contract")
        terminal_sequence = getattr(prepared_proof, "terminal_sequence", None)
        if type(terminal_sequence) is not int or not 1 <= terminal_sequence < (1 << 64):
            raise PropertyCatalogCoordinatorError("prepared hot boundary is invalid")
        last_data_sequence = getattr(prepared_proof, "last_data_sequence", None)
        if (
            type(last_data_sequence) is not int
            or not 0 <= last_data_sequence <= _MAX_DELIVERIES
            or terminal_sequence != last_data_sequence + 1
        ):
            raise PropertyCatalogCoordinatorError(
                "prepared hot data boundary is invalid or exceeds its audit bound"
            )
        assignment = _draining_assignment(
            lease,
            drain_deadline=drain_deadline,
            fenced_sequence=terminal_sequence,
        )
        hot_key = (SourceAdapter.SPAN_ATTRIBUTE, self._hot_stream_id)

        def bind_serialized() -> None:
            reservation = self._assert_reservation(lease, expected_status="draining")
            if (
                _datetime(reservation.get("drain_deadline"), "drain_deadline")
                != drain_deadline
            ):
                raise PropertyCatalogCoordinatorError("drain intent deadline changed")
            inventory = self._assert_inventory(
                lease, require_all=True, expected_status="draining"
            )
            hot = inventory[hot_key]
            current_boundary = _uint(hot.get("fenced_sequence"), "fenced_sequence")
            if current_boundary == 0:
                delivery_row_cap = max(
                    _MAX_STATE_VARIANTS,
                    last_data_sequence * _MAX_DELIVERY_REPLAYS + _MAX_DELIVERY_REPLAYS,
                )
                rows = self._query(
                    f"SELECT {', '.join(_HOT_DELIVERY_COLUMNS)} "
                    f"FROM {_qualified(self._database, _DELIVERY_TABLE)} "
                    "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
                    "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s "
                    "AND build_token=%(build_token)s AND source_adapter='span_attribute' "
                    "AND producer_stream_id=%(producer_stream_id)s "
                    "ORDER BY sequence, _version LIMIT %(row_limit)s",
                    {
                        "organization_id": lease.organization_id,
                        "workspace_id": lease.workspace_id,
                        "catalog_epoch": lease.catalog_epoch,
                        "catalog_revision": lease.catalog_revision,
                        "build_token": lease.build_token,
                        "producer_stream_id": self._hot_stream_id,
                        "row_limit": delivery_row_cap + 1,
                    },
                )
                _require_below_row_cap(
                    rows,
                    cap=delivery_row_cap,
                    label="prepared hot physical delivery audit",
                )
                prepared_proof.validate_prepared(assignment=intent, delivery_rows=rows)
                bound = dict(hot)
                bound.update(
                    {
                        "last_issued_sequence": terminal_sequence,
                        "fenced_sequence": terminal_sequence,
                        "updated_at": now,
                        "_version": _uint(hot.get("_version"), "_version") + 1,
                    }
                )
                self._append_revision_states_atomic(lease, (bound,))
            elif current_boundary != terminal_sequence:
                raise PropertyCatalogCoordinatorError(
                    "hot stream is already bound to another terminal sequence"
                )
            self._sink.publish(assignment)

        self._serializer.serialize(self._revision_key_for_lease(lease), bind_serialized)
        return assignment

    def fence(
        self,
        *,
        lease: RevisionLease,
        stream_proofs: tuple[StreamDrainProof, ...],
        checkpoint_state_sha256s: tuple[str, ...],
        final_manifest_sha256: str,
        drain_deadline: datetime,
        now: datetime,
    ) -> RevisionFence:
        _require_utc(now, "now")
        _require_utc(drain_deadline, "drain_deadline")
        if now > drain_deadline or not stream_proofs or not checkpoint_state_sha256s:
            raise PropertyCatalogCoordinatorError(
                "revision is not drained before deadline"
            )
        require_sha256(final_manifest_sha256, field="final_manifest_sha256")
        proofs = tuple(sorted(stream_proofs, key=lambda item: item.key))
        if len({proof.key for proof in proofs}) != len(proofs):
            raise PropertyCatalogCoordinatorError(
                "revision fence has duplicate streams"
            )
        if {proof.key for proof in proofs} != {
            stream.key for stream in lease.build_plan.streams
        }:
            raise PropertyCatalogCoordinatorError(
                "fence inventory does not exactly match the immutable build plan"
            )
        states = tuple(sorted(checkpoint_state_sha256s))
        for state in states:
            require_sha256(state, field="checkpoint_state_sha256")
        fence_sha256 = framed_sha256(
            "futureagi.property-catalog.revision-fence.v3",
            lease.organization_id,
            lease.workspace_id,
            lease.catalog_epoch,
            lease.catalog_revision,
            lease.build_token,
            lease.projection_version,
            lease.build_lease_sha256,
            final_manifest_sha256,
            RevisionFenceStatus.FENCED,
            drain_deadline.isoformat(timespec="microseconds"),
            *(
                component
                for proof in proofs
                for component in (
                    proof.source_adapter,
                    proof.producer_stream_id,
                    proof.last_issued_sequence,
                    proof.fenced_sequence,
                    proof.terminal_sequence,
                    proof.terminal_payload_sha256,
                )
            ),
            *states,
        )
        hot = tuple(
            proof for proof in proofs if proof.producer_stream_id == self._hot_stream_id
        )
        if len(hot) != 1:
            raise PropertyCatalogCoordinatorError("final fence has no unique hot proof")
        result: RevisionFence | None = None

        def fence_serialized() -> None:
            nonlocal result
            reservation = self._assert_reservation(
                lease, expected_status=("draining", "fenced")
            )
            if (
                _datetime(reservation.get("drain_deadline"), "drain_deadline")
                != drain_deadline
            ):
                raise PropertyCatalogCoordinatorError(
                    "revision fence deadline differs from its drain"
                )
            reservation_status = _text(reservation.get("status"))
            inventory = self._assert_inventory(
                lease,
                require_all=True,
                expected_status=(
                    "draining" if reservation_status == "draining" else "complete"
                ),
            )
            if reservation_status == "draining":
                targets = [
                    self._transition_stream_row(
                        existing=inventory[proof.key],
                        lease=lease,
                        status="complete",
                        last_sequence=proof.terminal_sequence,
                        last_issued_sequence=proof.last_issued_sequence,
                        fenced_sequence=proof.fenced_sequence,
                        terminal_payload_sha256=proof.terminal_payload_sha256,
                        drain_deadline=drain_deadline,
                        fenced_at=now,
                        now=now,
                    )
                    for proof in proofs
                ]
                targets.append(
                    self._transition_reservation_row(
                        existing=reservation,
                        lease=lease,
                        status="fenced",
                        drain_deadline=drain_deadline,
                        fenced_at=now,
                        now=now,
                    )
                )
                self._append_revision_states_atomic(lease, tuple(targets))
            else:
                for proof in proofs:
                    if not _stream_transition_matches(
                        inventory[proof.key],
                        lease=lease,
                        last_sequence=proof.terminal_sequence,
                        last_issued_sequence=proof.last_issued_sequence,
                        fenced_sequence=proof.fenced_sequence,
                        terminal_payload_sha256=proof.terminal_payload_sha256,
                        drain_deadline=drain_deadline,
                    ):
                        raise PropertyCatalogCoordinatorError(
                            "fenced revision evidence changed on replay"
                        )
            self._assert_inventory(lease, require_all=True, expected_status="complete")
            self._sink.publish(
                ProducerRevisionAssignment(
                    organization_id=lease.organization_id,
                    workspace_id=lease.workspace_id,
                    catalog_epoch=lease.catalog_epoch,
                    catalog_revision=lease.catalog_revision,
                    projection_version=lease.projection_version,
                    build_lease_sha256=lease.build_lease_sha256,
                    build_token=lease.build_token,
                    project_ids=lease.build_plan.source_scope.project_ids,
                    span_since_us=lease.build_plan.source_scope.span_since_us,
                    span_until_us=lease.build_plan.source_scope.span_until_us,
                    issued_at=lease.issued_at,
                    expires_at=lease.expires_at,
                    drain_deadline=drain_deadline,
                    fenced_sequence=hot[0].fenced_sequence,
                    status="fenced",
                )
            )
            result = RevisionFence(
                organization_id=lease.organization_id,
                workspace_id=lease.workspace_id,
                catalog_epoch=lease.catalog_epoch,
                catalog_revision=lease.catalog_revision,
                build_token=lease.build_token,
                projection_version=lease.projection_version,
                build_plan_json=lease.build_plan_json,
                build_lease_sha256=lease.build_lease_sha256,
                manifest_sha256=final_manifest_sha256,
                status=RevisionFenceStatus.FENCED,
                stream_proofs=proofs,
                checkpoint_state_sha256s=states,
                drain_deadline=drain_deadline,
                fenced_at=now,
                fence_sha256=fence_sha256,
            )

        self._serializer.serialize(
            self._revision_key_for_lease(lease), fence_serialized
        )
        assert result is not None
        return result

    def _transition_stream_row(
        self,
        *,
        existing: Mapping[str, Any],
        lease: RevisionLease,
        status: str,
        first_sequence: int = 1,
        last_sequence: int,
        last_issued_sequence: int,
        fenced_sequence: int,
        terminal_payload_sha256: str,
        drain_deadline: datetime,
        fenced_at: datetime | None,
        now: datetime,
    ) -> Mapping[str, Any]:
        current_status = _text(existing.get("status"))
        allowed_predecessor = "open" if status == "draining" else "draining"
        if current_status != allowed_predecessor:
            raise PropertyCatalogCoordinatorError(
                "source stream transition has no valid predecessor"
            )
        row = dict(existing)
        row.update(
            {
                "first_sequence": first_sequence,
                "last_sequence": last_sequence,
                "max_contiguous_sequence": last_sequence,
                "last_issued_sequence": last_issued_sequence,
                "fenced_sequence": fenced_sequence,
                "terminal_payload_sha256": terminal_payload_sha256,
                "status": status,
                "updated_at": now,
                "drain_deadline": drain_deadline,
                "fenced_at": fenced_at,
                "_version": _uint(existing.get("_version"), "_version") + 1,
            }
        )
        return row

    def _transition_reservation_row(
        self,
        *,
        existing: Mapping[str, Any],
        lease: RevisionLease,
        status: str,
        drain_deadline: datetime,
        fenced_at: datetime | None,
        now: datetime,
    ) -> Mapping[str, Any]:
        current = _text(existing.get("status"))
        expected = "open" if status == "draining" else "draining"
        if current != expected:
            raise PropertyCatalogCoordinatorError(
                "reservation transition has no valid predecessor"
            )
        row = dict(existing)
        row.update(
            {
                "status": status,
                "updated_at": now,
                "drain_deadline": drain_deadline,
                "fenced_at": fenced_at,
                "_version": _uint(existing.get("_version"), "_version") + 1,
            }
        )
        if (
            _text(row.get("build_plan_json")) != lease.build_plan_json
            or _text(row.get("build_lease_sha256")) != lease.build_lease_sha256
        ):
            raise PropertyCatalogCoordinatorError(
                "reservation transition changed immutable build plan"
            )
        return row

    def _append_revision_states_atomic(
        self, lease: RevisionLease, rows: tuple[Mapping[str, Any], ...]
    ) -> None:
        if not rows:
            raise PropertyCatalogCoordinatorError("revision transition is empty")
        normalized = tuple(
            {column: row.get(column) for column in _SOURCE_STREAM_COLUMNS}
            for row in rows
        )
        keys = {
            (
                SourceAdapter(_text(row.get("source_adapter"))),
                _text(row.get("producer_stream_id")),
            )
            for row in normalized
        }
        if len(keys) != len(normalized) or any(
            _text(row.get("build_plan_json")) != lease.build_plan_json
            or _text(row.get("build_lease_sha256")) != lease.build_lease_sha256
            for row in normalized
        ):
            raise PropertyCatalogCoordinatorError(
                "revision transition rows are duplicate or change the build plan"
            )
        self._validate_target()
        self._client.insert(
            _qualified(self._database, _SOURCE_STREAM_TABLE),
            normalized,
            columns=_SOURCE_STREAM_COLUMNS,
            timeout_ms=self._deadline.remaining_ms(cap_ms=self._timeout_ms),
            deduplication_token=(
                "property-catalog-revision-transition-v1:"
                + framed_sha256(
                    "futureagi.property-catalog.revision-transition.v1",
                    lease.build_lease_sha256,
                    *sorted(_row_identity(row) for row in normalized),
                )
            ),
        )
        for row in normalized:
            verified = self._read_stream(
                lease=lease,
                source_adapter=SourceAdapter(_text(row.get("source_adapter"))),
                producer_stream_id=_text(row.get("producer_stream_id")),
            )
            if verified is None or _row_identity(verified) != _row_identity(row):
                raise PropertyCatalogCoordinatorError(
                    "atomic revision transition was not preserved"
                )

    def _assert_reservation(
        self,
        lease: RevisionLease,
        *,
        expected_status: str | tuple[str, ...],
        allow_source_invalidated: bool = False,
    ) -> Mapping[str, Any]:
        if self._recovery_journal is not None:
            key = self._revision_key_for_lease(lease)
            if self._recovery_journal.is_revoked(key, lease.build_lease_sha256):
                raise PropertyCatalogCoordinatorError(
                    "revision reservation is closed by supersession"
                )
            intent = self._recovery_journal.load(key)
            if intent is not None and not intent["complete"]:
                raise PropertyCatalogCoordinatorError(
                    "reservation has a pending supersession"
                )
        if not allow_source_invalidated and self.source_snapshot_invalid(lease):
            raise PropertyCatalogCoordinatorError(
                "revision source snapshot was invalidated"
            )
        row = self._read_stream(
            lease=lease,
            source_adapter=SourceAdapter.SYSTEM_MANIFEST,
            producer_stream_id=lease.build_token,
        )
        statuses = (
            (expected_status,) if isinstance(expected_status, str) else expected_status
        )
        if (
            row is None
            or _uint(row.get("envelope_version"), "envelope_version") != 0
            or _text(row.get("status")) not in statuses
            or _text(row.get("build_plan_json")) != lease.build_plan_json
            or _text(row.get("build_lease_sha256")) != lease.build_lease_sha256
        ):
            raise PropertyCatalogCoordinatorError(
                "revision reservation is missing, conflicting, or closed"
            )
        return row

    def _assert_inventory(
        self,
        lease: RevisionLease,
        *,
        require_all: bool,
        expected_status: str | None,
    ) -> Mapping[tuple[SourceAdapter, str], Mapping[str, Any]]:
        row_cap = (len(lease.build_plan.streams) + 1) * _MAX_STATE_VARIANTS
        rows = self._query(
            f"SELECT {', '.join(_SOURCE_STREAM_COLUMNS)} "
            f"FROM {_qualified(self._database, _SOURCE_STREAM_TABLE)} "
            "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
            "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s "
            "AND build_token=%(build_token)s ORDER BY source_adapter, producer_stream_id, "
            "_version DESC LIMIT %(row_limit)s",
            {
                "organization_id": lease.organization_id,
                "workspace_id": lease.workspace_id,
                "catalog_epoch": lease.catalog_epoch,
                "catalog_revision": lease.catalog_revision,
                "build_token": lease.build_token,
                "row_limit": row_cap + 1,
            },
        )
        _require_below_row_cap(rows, cap=row_cap, label="source stream inventory")
        grouped: dict[tuple[SourceAdapter, str], list[Mapping[str, Any]]] = {}
        for row in rows:
            try:
                adapter = SourceAdapter(_text(row.get("source_adapter")))
            except ValueError as exc:
                raise PropertyCatalogCoordinatorError(
                    "source stream inventory contains an unknown adapter"
                ) from exc
            stream_id = _text(row.get("producer_stream_id"))
            _require_stream_row_matches_lease(
                row,
                lease=lease,
                source_adapter=adapter,
                producer_stream_id=stream_id,
            )
            key = (adapter, stream_id)
            grouped.setdefault(key, []).append(row)
        latest = {key: _latest(values) for key, values in grouped.items()}
        reservation_key = (SourceAdapter.SYSTEM_MANIFEST, lease.build_token)
        reservation = latest.pop(reservation_key, None)
        if reservation is None:
            raise PropertyCatalogCoordinatorError("revision reservation is missing")
        planned = {stream.key for stream in lease.build_plan.streams}
        if set(latest) - planned or (require_all and set(latest) != planned):
            raise PropertyCatalogCoordinatorError(
                "physical stream inventory differs from immutable build plan"
            )
        result: dict[tuple[SourceAdapter, str], Mapping[str, Any]] = {}
        for key, row in latest.items():
            assert row is not None
            if (
                _uint(row.get("envelope_version"), "envelope_version") != 1
                or _text(row.get("build_plan_json")) != lease.build_plan_json
                or _text(row.get("build_lease_sha256")) != lease.build_lease_sha256
                or (
                    expected_status is not None
                    and _text(row.get("status")) != expected_status
                )
            ):
                raise PropertyCatalogCoordinatorError(
                    "physical stream state conflicts with immutable build plan"
                )
            result[key] = row
        return result

    def _append_stream_state(
        self, row: Mapping[str, Any], *, allow_transition: bool = False
    ) -> None:
        lease = _row_lease(row)
        adapter = SourceAdapter(_text(row.get("source_adapter")))
        stream_id = _text(row.get("producer_stream_id"))
        existing = self._read_stream(
            lease=lease,
            source_adapter=adapter,
            producer_stream_id=stream_id,
        )
        if existing is not None:
            if _row_identity(existing) == _row_identity(row):
                return
            if not allow_transition:
                raise PropertyCatalogCoordinatorError(
                    "source stream already has another immutable state"
                )
        self._insert(
            _SOURCE_STREAM_TABLE,
            row,
            deduplication_token=(
                "property-catalog-source-stream-v1:"
                f"{lease.build_token}:{adapter}:{stream_id}:{row['_version']}:"
                f"{row['build_lease_sha256']}:{row['status']}"
            ),
        )
        verified = self._read_stream(
            lease=lease,
            source_adapter=adapter,
            producer_stream_id=stream_id,
        )
        if verified is None or _row_identity(verified) != _row_identity(row):
            raise PropertyCatalogCoordinatorError(
                "source stream append was not preserved as unique latest state"
            )

    def _read_stream(
        self,
        *,
        lease: RevisionLease,
        source_adapter: SourceAdapter,
        producer_stream_id: str,
    ) -> Mapping[str, Any] | None:
        rows = self._query(
            f"SELECT {', '.join(_SOURCE_STREAM_COLUMNS)} "
            f"FROM {_qualified(self._database, _SOURCE_STREAM_TABLE)} "
            "WHERE organization_id=%(organization_id)s AND workspace_id=%(workspace_id)s "
            "AND catalog_epoch=%(catalog_epoch)s AND catalog_revision=%(catalog_revision)s "
            "AND build_token=%(build_token)s AND source_adapter=%(source_adapter)s "
            "AND producer_stream_id=%(producer_stream_id)s "
            "ORDER BY _version DESC LIMIT %(row_limit)s",
            {
                "organization_id": lease.organization_id,
                "workspace_id": lease.workspace_id,
                "catalog_epoch": lease.catalog_epoch,
                "catalog_revision": lease.catalog_revision,
                "build_token": lease.build_token,
                "source_adapter": str(source_adapter),
                "producer_stream_id": producer_stream_id,
                "row_limit": _MAX_STATE_VARIANTS + 1,
            },
        )
        _require_below_row_cap(rows, cap=_MAX_STATE_VARIANTS, label="source stream")
        for row in rows:
            _require_stream_row_matches_lease(
                row,
                lease=lease,
                source_adapter=source_adapter,
                producer_stream_id=producer_stream_id,
            )
        return _latest(rows)

    def _require_live_lease(self, lease: RevisionLease) -> None:
        now = self._now()
        _require_utc(now, "coordinator now")
        if now >= lease.expires_at:
            raise PropertyCatalogCoordinatorError("revision lease is expired")

    def _validate_planned_streams(
        self, planned_streams: tuple[BuildPlanStream, ...]
    ) -> None:
        # RevisionBuildPlan performs the complete adapter/role validation after
        # the monotonic revision is known. These checks reject collision with
        # the synthetic reservation and bind the one configured hot producer.
        if not isinstance(planned_streams, tuple) or any(
            not isinstance(stream, BuildPlanStream) for stream in planned_streams
        ):
            raise TypeError("planned_streams must be BuildPlanStream tuples")
        if any(
            stream.producer_stream_id == "00000000-0000-0000-0000-000000000000"
            for stream in planned_streams
        ):
            raise PropertyCatalogCoordinatorError("planned stream id cannot be zero")
        hot = tuple(
            stream for stream in planned_streams if stream.role.value == "hot_values"
        )
        if len(hot) != 1 or hot[0].producer_stream_id != self._hot_stream_id:
            raise PropertyCatalogCoordinatorError(
                "build plan hot stream does not match configured producer"
            )

    def _revision_key(
        self, *, organization_id: str, workspace_id: str, catalog_epoch: int
    ) -> str:
        return (
            f"revision:{self._database}:{organization_id}:{workspace_id}:"
            f"{catalog_epoch}"
        )

    def _revision_key_for_lease(self, lease: RevisionLease) -> str:
        return self._revision_key(
            organization_id=lease.organization_id,
            workspace_id=lease.workspace_id,
            catalog_epoch=lease.catalog_epoch,
        )

    def _query(
        self, sql: str, params: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], ...]:
        self._validate_target()
        return tuple(
            self._client.query(
                sql,
                params,
                timeout_ms=self._deadline.remaining_ms(cap_ms=self._timeout_ms),
            )
        )

    def _insert(
        self,
        table: str,
        row: Mapping[str, Any],
        *,
        deduplication_token: str,
    ) -> None:
        self._validate_target()
        self._client.insert(
            _qualified(self._database, table),
            (row,),
            columns=tuple(row),
            timeout_ms=self._deadline.remaining_ms(cap_ms=self._timeout_ms),
            deduplication_token=deduplication_token,
        )

    def _validate_target(self) -> None:
        require_catalog_database(self._database)
        if getattr(self._client, "catalog_database", None) != self._database:
            raise PropertyCatalogCoordinatorError(
                "coordinator client database identity changed"
            )


def _building_assignment(lease: RevisionLease) -> ProducerRevisionAssignment:
    return ProducerRevisionAssignment(
        organization_id=lease.organization_id,
        workspace_id=lease.workspace_id,
        catalog_epoch=lease.catalog_epoch,
        catalog_revision=lease.catalog_revision,
        projection_version=lease.projection_version,
        build_lease_sha256=lease.build_lease_sha256,
        build_token=lease.build_token,
        project_ids=lease.build_plan.source_scope.project_ids,
        span_since_us=lease.build_plan.source_scope.span_since_us,
        span_until_us=lease.build_plan.source_scope.span_until_us,
        issued_at=lease.issued_at,
        expires_at=lease.expires_at,
        drain_deadline=None,
        fenced_sequence=0,
        status="building",
    )


def _draining_assignment(
    lease: RevisionLease, *, drain_deadline: datetime, fenced_sequence: int
) -> ProducerRevisionAssignment:
    return ProducerRevisionAssignment(
        organization_id=lease.organization_id,
        workspace_id=lease.workspace_id,
        catalog_epoch=lease.catalog_epoch,
        catalog_revision=lease.catalog_revision,
        projection_version=lease.projection_version,
        build_lease_sha256=lease.build_lease_sha256,
        build_token=lease.build_token,
        project_ids=lease.build_plan.source_scope.project_ids,
        span_since_us=lease.build_plan.source_scope.span_since_us,
        span_until_us=lease.build_plan.source_scope.span_until_us,
        issued_at=lease.issued_at,
        expires_at=lease.expires_at,
        drain_deadline=drain_deadline,
        fenced_sequence=fenced_sequence,
        status="draining",
    )


def _stream_row(
    *,
    lease: RevisionLease,
    source_adapter: SourceAdapter,
    producer_stream_id: str,
    envelope_version: int,
    status: str,
    now: datetime,
    drain_deadline: datetime,
) -> dict[str, Any]:
    return {
        "organization_id": lease.organization_id,
        "workspace_id": lease.workspace_id,
        "catalog_epoch": lease.catalog_epoch,
        "catalog_revision": lease.catalog_revision,
        "build_token": lease.build_token,
        "projection_version": lease.projection_version,
        "source_adapter": str(source_adapter),
        "producer_stream_id": producer_stream_id,
        "envelope_version": envelope_version,
        "first_sequence": 0,
        "last_sequence": 0,
        "max_contiguous_sequence": 0,
        "last_issued_sequence": 0,
        "fenced_sequence": 0,
        "terminal_payload_sha256": _ZERO_SHA256,
        "build_plan_json": lease.build_plan_json,
        "build_lease_sha256": lease.build_lease_sha256,
        "status": status,
        "gap_count": 0,
        "gap_reasons": [],
        "kafka_partition": -1,
        "kafka_high_water_offset": -1,
        "started_at": now,
        "updated_at": now,
        "drain_deadline": drain_deadline,
        "fenced_at": None,
        "_version": 1,
    }


def _row_lease(row: Mapping[str, Any]) -> RevisionLease:
    deadline = _datetime(row.get("drain_deadline"), "drain_deadline")
    issued = _datetime(row.get("started_at"), "started_at")
    return RevisionLease(
        organization_id=_text(row.get("organization_id")),
        workspace_id=_text(row.get("workspace_id")),
        catalog_epoch=_uint(row.get("catalog_epoch"), "catalog_epoch"),
        catalog_revision=_uint(row.get("catalog_revision"), "catalog_revision"),
        projection_version=_uint(row.get("projection_version"), "projection_version"),
        build_token=_text(row.get("build_token")),
        build_plan_json=_text(row.get("build_plan_json")),
        build_lease_sha256=_text(row.get("build_lease_sha256")),
        issued_at=issued,
        expires_at=max(deadline, issued + timedelta(microseconds=1)),
    )


def _stream_transition_matches(
    row: Mapping[str, Any],
    *,
    lease: RevisionLease,
    first_sequence: int = 1,
    last_sequence: int,
    last_issued_sequence: int,
    fenced_sequence: int,
    terminal_payload_sha256: str,
    drain_deadline: datetime,
) -> bool:
    return (
        _uint(row.get("first_sequence"), "first_sequence") == first_sequence
        and _uint(row.get("last_sequence"), "last_sequence") == last_sequence
        and _uint(row.get("max_contiguous_sequence"), "max_contiguous_sequence")
        == last_sequence
        and _uint(row.get("last_issued_sequence"), "last_issued_sequence")
        == last_issued_sequence
        and _uint(row.get("fenced_sequence"), "fenced_sequence") == fenced_sequence
        and _text(row.get("terminal_payload_sha256")) == terminal_payload_sha256
        and _text(row.get("build_plan_json")) == lease.build_plan_json
        and _text(row.get("build_lease_sha256")) == lease.build_lease_sha256
        and _datetime(row.get("drain_deadline"), "drain_deadline") == drain_deadline
    )


def _require_below_row_cap(
    rows: Sequence[Mapping[str, Any]],
    *,
    cap: int,
    label: str,
) -> None:
    """Reject cap+1 evidence before any control-plane row is trusted."""

    if type(cap) is not int or cap < 1:
        raise ValueError("row cap must be a positive integer")
    if len(rows) > cap:
        raise PropertyCatalogCoordinatorError(
            f"{label} exceeded its conflict-proof row cap"
        )


def _require_stream_row_matches_lease(
    row: Mapping[str, Any],
    *,
    lease: RevisionLease,
    source_adapter: SourceAdapter,
    producer_stream_id: str,
) -> None:
    """Prove immutable scope and build-plan fields across the whole state history."""

    if (
        _text(row.get("organization_id")) != lease.organization_id
        or _text(row.get("workspace_id")) != lease.workspace_id
        or _uint(row.get("catalog_epoch"), "catalog_epoch") != lease.catalog_epoch
        or _uint(row.get("catalog_revision"), "catalog_revision")
        != lease.catalog_revision
        or _text(row.get("build_token")) != lease.build_token
        or _uint(row.get("projection_version"), "projection_version")
        != lease.projection_version
        or _text(row.get("source_adapter")) != str(source_adapter)
        or _text(row.get("producer_stream_id")) != producer_stream_id
        or _text(row.get("build_plan_json")) != lease.build_plan_json
        or _text(row.get("build_lease_sha256")) != lease.build_lease_sha256
    ):
        raise PropertyCatalogCoordinatorError(
            "source stream history changed immutable scope or build plan"
        )


def _latest(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    if not rows:
        return None
    maximum = max(_uint(row.get("_version"), "_version") for row in rows)
    latest = tuple(
        row for row in rows if _uint(row.get("_version"), "_version") == maximum
    )
    identities = {_row_identity(row) for row in latest}
    if len(identities) != 1:
        raise PropertyCatalogCoordinatorError(
            f"source stream contains conflicting states at version {maximum}"
        )
    return latest[0]


def _row_identity(row: Mapping[str, Any]) -> str:
    values = []
    for column in _SOURCE_STREAM_LOGICAL_COLUMNS:
        value = row.get(column)
        if isinstance(value, datetime):
            value = value.astimezone(UTC).isoformat(timespec="microseconds")
        elif isinstance(value, tuple):
            value = list(value)
        values.append(value)
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"), default=str)


def _qualified(database: str, table: str) -> str:
    require_catalog_database(database)
    if table not in PROPERTY_CATALOG_TABLES:
        raise PropertyCatalogCoordinatorError("coordinator table is not allowlisted")
    return f"`{database}`.`{table}`"


def _time_text(value: datetime) -> str:
    _require_utc(value, "producer assignment timestamp")
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _positive_uint(value: Any, bits: int, field: str) -> None:
    if type(value) is not int or not 1 <= value < (1 << bits):
        raise ValueError(f"{field} must be a positive UInt{bits}")


def _uint(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value < (1 << 64):
        raise PropertyCatalogCoordinatorError(f"{field} is not a UInt64")
    return value


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _datetime(value: Any, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PropertyCatalogCoordinatorError(
                f"{field} is not a timestamp"
            ) from exc
    if not isinstance(value, datetime):
        raise PropertyCatalogCoordinatorError(f"{field} is not a timestamp")
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "AtomicSingleTenantFenceFile",
    "CatalogCoordinatorClient",
    "ClickHouseRevisionCoordinator",
    "ProducerFenceSink",
    "ProducerRevisionAssignment",
    "PropertyCatalogCoordinatorError",
    "MAX_REVISION_LEASE_SECONDS",
    "REVISION_LEASE_SECONDS",
    "encode_producer_assignment",
    "producer_assignment_sha256",
]
