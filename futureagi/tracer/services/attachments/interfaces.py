"""Internal attachment values and structural contracts, frozen interface v1.

No database, configuration, storage or authority work happens here. Constructing
one of these values does NOT authorize an operation or prove a lease is current.
Callers must still perform the frozen locked ownership and publication checks.
All validation errors are content-free; opaque identities are never normalized.

Integer bounds match PostgreSQL bigint/integer/smallint, not deployment quotas.
Persisted text bounds match the frozen schema. The freeze does not specify a
length ceiling for provider pagination cursors or internal erase reason codes;
these remain opaque rather than inventing a provider-incompatible ceiling.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import TracebackType
from typing import TYPE_CHECKING, BinaryIO, Literal, Protocol, TypeAlias
from uuid import UUID

if TYPE_CHECKING:
    from tracer.services.attachments.occurrences import SourceState

INT64_MAX = 9_223_372_036_854_775_807
INT32_MAX = 2_147_483_647
INT16_MAX = 32_767

StoragePurpose: TypeAlias = Literal["quarantine", "serving"]
StorageOperation: TypeAlias = Literal["admit", "write", "read", "erase"]
RecordPutResult: TypeAlias = Literal["recorded", "already_owned", "stale_unowned"]
ParentAuthorizationBatch: TypeAlias = Mapping[tuple[UUID, str], "SourceState | None"]
# Every batch entry incorporates committed session deletion/remap checks.
# None is denial, never permission to select a different source.


def _invalid() -> None:
    raise ValueError("invalid attachment value")


def _integer(value: int, maximum: int, *, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid()


def _uuid(value: UUID) -> None:
    if not isinstance(value, UUID):
        _invalid()


def _text(value: str, maximum: int | None = None) -> None:
    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or (maximum is not None and len(value) > maximum)
    ):
        _invalid()


def _choice(value: str, choices: tuple[str, ...]) -> None:
    if type(value) is not str or value not in choices:
        _invalid()


def _revision(value: str, maximum: int | None = 255) -> None:
    _text(value, maximum)
    if value == "null":
        _invalid()


def _utc(value: datetime) -> None:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        _invalid()


def _hash(value: str) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class ObjectLocator:
    binding_id: UUID
    purpose: StoragePurpose
    key: str

    def __post_init__(self) -> None:
        _uuid(self.binding_id)
        _choice(self.purpose, ("quarantine", "serving"))
        _text(self.key, 255)


@dataclass(frozen=True, slots=True, repr=False)
class ObjectIdentity:
    binding_id: UUID
    purpose: StoragePurpose
    key: str
    version: str

    def __post_init__(self) -> None:
        ObjectLocator(self.binding_id, self.purpose, self.key)
        _revision(self.version)


@dataclass(frozen=True, slots=True, repr=False)
class StoredObject:
    identity: ObjectIdentity
    byte_length: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ObjectIdentity):
            _invalid()
        _integer(self.byte_length, INT64_MAX)
        _hash(self.sha256)


@dataclass(frozen=True, slots=True, repr=False)
class VersionPage:
    versions: tuple[ObjectIdentity, ...]
    delete_markers: tuple[ObjectIdentity, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        if type(self.versions) is not tuple or type(self.delete_markers) is not tuple:
            _invalid()
        if len(self.versions) + len(self.delete_markers) > 100:
            _invalid()
        if any(
            not isinstance(item, ObjectIdentity)
            for item in self.versions + self.delete_markers
        ):
            _invalid()
        if self.next_cursor is not None:
            # A provider cursor is not an object revision; keep it opaque.
            _text(self.next_cursor)


@dataclass(frozen=True, slots=True, repr=False)
class EraseResult:
    """Empty describes one bounded enumeration, NOT stopped writes or settlement."""

    empty: bool
    retryable: bool
    reason: str | None
    checked_at: datetime

    def __post_init__(self) -> None:
        if type(self.empty) is not bool or type(self.retryable) is not bool:
            _invalid()
        if self.reason is not None:
            _text(self.reason)
        _utc(self.checked_at)


@dataclass(frozen=True, slots=True, repr=False)
class LeaseFence:
    owner_kind: Literal["import", "derivative"]
    owner_id: UUID
    token: UUID
    epoch: int
    expires_at: datetime

    def __post_init__(self) -> None:
        _choice(self.owner_kind, ("import", "derivative"))
        _uuid(self.owner_id)
        _uuid(self.token)
        _integer(self.epoch, INT64_MAX)
        _utc(self.expires_at)


@dataclass(frozen=True, slots=True, repr=False)
class PutPermit:
    """Server-only value; not a wire capability or proof of current authority.

    The owner persists the fresh attempt, locator and horizon before issuance.
    The adapter checks the deadline immediately before dispatch. Construction
    cannot enforce the 30-second worker window without the issuance timestamp;
    prepare_put and the transport/supervisor must enforce that window.
    """

    allocation_kind: Literal["upload", "segment", "derivative"]
    allocation_id: UUID
    locator: ObjectLocator
    fence: LeaseFence | None
    write_attempt: int
    deadline_at: datetime

    def __post_init__(self) -> None:
        _choice(self.allocation_kind, ("upload", "segment", "derivative"))
        _uuid(self.allocation_id)
        if not isinstance(self.locator, ObjectLocator):
            _invalid()
        _integer(self.write_attempt, 3, minimum=1)
        _utc(self.deadline_at)
        if self.fence is None:
            if self.allocation_kind != "upload":
                _invalid()
            return
        if not isinstance(self.fence, LeaseFence):
            _invalid()
        expected = "derivative" if self.allocation_kind == "derivative" else "import"
        if (
            self.fence.owner_kind != expected
            or self.deadline_at > self.fence.expires_at
        ):
            _invalid()
        if (
            self.allocation_kind == "derivative"
            and self.allocation_id != self.fence.owner_id
        ):
            _invalid()


@dataclass(frozen=True, slots=True)
class ResourceVector:
    """In-flight units: bytes, pipelines, jobs, timelines, allocations, links.

    Bytes include scratch and potentially unreconciled physical versions;
    objects include named scratch/file/object allocations. This is NOT retained
    storage billing. Scalar range checks do not establish capacity headroom.
    """

    bytes: int
    transfers: int
    validation_jobs: int
    recordings: int
    objects: int
    links: int

    def __post_init__(self) -> None:
        _integer(self.bytes, INT64_MAX)
        for value in (
            self.transfers,
            self.validation_jobs,
            self.recordings,
            self.objects,
            self.links,
        ):
            _integer(value, INT32_MAX)

    def __add__(self, other: ResourceVector) -> ResourceVector:
        if not isinstance(other, ResourceVector):
            _invalid()
        return ResourceVector(
            self.bytes + other.bytes,
            self.transfers + other.transfers,
            self.validation_jobs + other.validation_jobs,
            self.recordings + other.recordings,
            self.objects + other.objects,
            self.links + other.links,
        )

    def __sub__(self, other: ResourceVector) -> ResourceVector:
        if not isinstance(other, ResourceVector):
            _invalid()
        # Construction rejects underflow in ANY component; never clamp to zero.
        return ResourceVector(
            self.bytes - other.bytes,
            self.transfers - other.transfers,
            self.validation_jobs - other.validation_jobs,
            self.recordings - other.recordings,
            self.objects - other.objects,
            self.links - other.links,
        )


@dataclass(frozen=True, slots=True, repr=False)
class ReservationOwner:
    kind: Literal["upload", "import", "derivative"]
    id: UUID
    scope_id: UUID

    def __post_init__(self) -> None:
        _choice(self.kind, ("upload", "import", "derivative"))
        _uuid(self.id)
        _uuid(self.scope_id)


class CapacityLocks(Protocol):
    """Opaque transaction-scoped handle, constructed ONLY by lock_capacity.

    The implementation privately retains ordered locked Budget rows, matching
    scope identity and transaction lifetime proof. This protocol cannot be
    instantiated and is not a runtime lock validator. Callers cannot construct
    or reuse a handle after transaction exit; the accounting owner must enforce
    this. No concrete lock handle or Budget access is supplied by this module.
    """

    @property
    def scope_id(self) -> UUID: ...


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedAssetFacts:
    object: StoredObject
    mime_type: str
    validated_at: datetime
    sample_rate: int | None
    channels: int | None
    frame_count: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.object, StoredObject):
            _invalid()
        _text(self.mime_type, 64)
        _utc(self.validated_at)
        audio = self.mime_type.startswith("audio/")
        for value, maximum in (
            (self.sample_rate, INT32_MAX),
            (self.channels, INT16_MAX),
            (self.frame_count, INT64_MAX),
        ):
            if value is None:
                if audio:
                    _invalid()
            else:
                _integer(value, maximum, minimum=1)
        # Non-audio probe facts remain caller-owned; do not fabricate audio facts.


@dataclass(frozen=True, slots=True, repr=False)
class SourceRef:
    kind: Literal["RemoteUrlEnvelope", "ProviderFile"]
    id: UUID

    def __post_init__(self) -> None:
        _choice(self.kind, ("RemoteUrlEnvelope", "ProviderFile"))
        _uuid(self.id)


@dataclass(frozen=True, slots=True, repr=False)
class SourceFacts:
    """Trusted pinned facts only: no locator, credential or signed query.

    This value is not authorization. Both expiry clocks for an envelope, the
    creator/session/connection and generation must be rechecked by the source
    owner. Whole-audio admission additionally requires a positive duration.
    """

    source_kind: Literal["RemoteUrlEnvelope", "ProviderFile"]
    source_id: UUID
    creator_kind: Literal["user", "api_key"]
    creator_id: UUID
    generation_kind: Literal["s3_version", "gcs_generation", "http_strong_etag"]
    generation: str
    byte_length: int
    mime_type: str
    duration_ms: int | None
    expires_at: datetime
    authority_generation: int
    parent_trace_id: UUID
    parent_span_id: str
    direction: Literal["input", "output", "mixed"]
    connection_id: UUID | None

    def __post_init__(self) -> None:
        SourceRef(self.source_kind, self.source_id)
        _choice(self.creator_kind, ("user", "api_key"))
        _uuid(self.creator_id)
        _choice(
            self.generation_kind, ("s3_version", "gcs_generation", "http_strong_etag")
        )
        _revision(self.generation)
        if (
            self.generation_kind == "http_strong_etag"
            and re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]*"', self.generation) is None
        ):
            _invalid()
        _integer(self.byte_length, INT64_MAX, minimum=1)
        _text(self.mime_type, 64)
        if self.duration_ms is not None:
            _integer(self.duration_ms, INT32_MAX, minimum=1)
        _utc(self.expires_at)
        _integer(self.authority_generation, INT64_MAX)
        _uuid(self.parent_trace_id)
        if (
            type(self.parent_span_id) is not str
            or re.fullmatch(r"[0-9a-f]{16}", self.parent_span_id) is None
            or self.parent_span_id == "0000000000000000"
        ):
            _invalid()
        _choice(self.direction, ("input", "output", "mixed"))
        if self.connection_id is not None:
            _uuid(self.connection_id)


@dataclass(frozen=True, slots=True, repr=False)
class RepresentationSelection:
    """Internal selector; no wire encoding or permission is introduced here."""

    representation: Literal["original", "playable", "channel-0", "channel-1", "peaks"]
    identity: ObjectIdentity
    byte_length: int
    mime_type: str
    sha256: str
    derivative_id: UUID | None

    def __post_init__(self) -> None:
        _choice(
            self.representation,
            ("original", "playable", "channel-0", "channel-1", "peaks"),
        )
        StoredObject(self.identity, self.byte_length, self.sha256)
        if self.identity.purpose != "serving":
            _invalid()
        _text(self.mime_type, 64)
        if self.derivative_id is not None:
            _uuid(self.derivative_id)
        if (
            self.representation in ("channel-0", "channel-1", "peaks")
            and self.derivative_id is None
        ):
            _invalid()
        if self.representation == "original" and self.derivative_id is not None:
            _invalid()


@dataclass(frozen=True, slots=True, repr=False)
class DecoderRequirements:
    operation: Literal["validate", "normalize", "segment", "derive"]
    mime_type: str
    scratch_bytes: int

    def __post_init__(self) -> None:
        _choice(self.operation, ("validate", "normalize", "segment", "derive"))
        _text(self.mime_type, 64)
        _integer(self.scratch_bytes, INT64_MAX)


class SourceReader(Protocol):
    """Context-managed, exact-generation reader; no arbitrary URL/header API.

    Range requests are inclusive and <=8 MiB; transport buffers <=256 KiB.
    Implementations recheck pinned generation/length and authority before and
    after reads. A weak ETag, redirect escape or changed source fails closed.
    """

    @property
    def facts(self) -> SourceFacts: ...

    def read_range(self, *, start: int, end: int) -> bytes: ...

    def close(self) -> None: ...

    def __enter__(self) -> SourceReader: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class PrivateMediaStorage(Protocol):
    """Structural contract only; the concrete adapter belongs to storage.py.

    Exact binding/purpose/version checks, bounded transport, privacy/readiness
    verification and cleanup fencing remain implementation obligations. An
    erase result alone never authorizes capacity release.
    """

    def verify_identity(self, *, operation: StorageOperation) -> None: ...
    def verify_readiness(self) -> None: ...
    def put_quarantine(
        self, permit: PutPermit, stream: BinaryIO, length: int
    ) -> ObjectIdentity: ...
    def snapshot(
        self, identity: ObjectIdentity, output: BinaryIO, *, expected_length: int
    ) -> str: ...
    def promote(
        self,
        snapshot: BinaryIO,
        *,
        permit: PutPermit,
        expected_length: int,
        expected_sha256: str,
    ) -> ObjectIdentity: ...
    def open_range(
        self, identity: ObjectIdentity, *, start: int, end: int, expected_length: int
    ) -> BinaryIO: ...
    def list_versions(
        self, locator: ObjectLocator, *, cursor: str | None = None, limit: int = 100
    ) -> VersionPage: ...
    def erase_version(self, identity: ObjectIdentity) -> None: ...
    def erase_all_versions(self, locator: ObjectLocator) -> EraseResult: ...
    def erase_upload(self, upload_id: UUID) -> EraseResult:
        """Legacy proven-key wrapper on this binding; NEVER for stale workers."""
        ...


class CapacityOperations(Protocol):
    """Frozen accounting signatures; declarations, NOT an implementation.

    lock_capacity / *_locked require transaction.atomic and the frozen full
    owner lock order. Only the accounting implementation may mutate Budget.
    reserve/resize check all three levels atomically. release subtracts once
    using the owner's settlement stamp, only after cleanup/termination proof.

    Single import: reserve U+J once on Job, then transfer U to Upload in the
    SAME transaction, with identical scope/pool and neither owner settled.
    transfer_locked changes owner vectors only, NEVER Budget counters. Retry
    must prove committed creation/transfer, not blindly move U again.
    """

    def lock_capacity(
        self, *, scope_id: UUID, pool_keys: tuple[str, ...]
    ) -> CapacityLocks: ...
    def reserve_locked(
        self,
        locks: CapacityLocks,
        *,
        owner: ReservationOwner,
        amount: ResourceVector,
        expires_at: datetime,
    ) -> None: ...
    def resize_locked(
        self, locks: CapacityLocks, *, owner: ReservationOwner, amount: ResourceVector
    ) -> None: ...
    def transfer_locked(
        self,
        locks: CapacityLocks,
        *,
        from_owner: ReservationOwner,
        to_owner: ReservationOwner,
        amount: ResourceVector,
    ) -> None: ...
    def release_locked(
        self,
        locks: CapacityLocks,
        *,
        owner: ReservationOwner,
        cleanup_complete: bool,
        now: datetime,
    ) -> bool: ...
    def count_attempt(
        self, *, scope_id: UUID, pool_key: str, now: datetime
    ) -> bool: ...
    def limits_for(
        self, *, scope_id: UUID, pool_key: str
    ) -> tuple[ResourceVector, ResourceVector, ResourceVector]: ...
