"""Pure revision/checkpoint qualification for catalog activation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .codec import canonical_uuid, framed_sha256, require_sha256
from .models import SourceAdapter


class CheckpointStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    GAP = "gap"
    FAILED = "failed"


@dataclass(slots=True)
class SourceAuditAccumulator:
    """Streaming, order-independent digest of immutable source observations.

    Both the hot writer and the independent CH source audit add the same
    per-observation SHA-256 identity. Count, XOR, modular sum, and modular
    square-sum retain multiplicity without depending on Kafka batch order.
    """

    count: int = 0
    xor: int = 0
    total: int = 0
    square_total: int = 0

    def add(self, observation_sha256: str) -> None:
        require_sha256(observation_sha256, field="observation_sha256")
        value = int(observation_sha256, 16)
        modulus = 1 << 256
        self.count += 1
        if self.count >= 1 << 64:
            raise ValueError("source audit count exceeds UInt64")
        self.xor ^= value
        self.total = (self.total + value) % modulus
        self.square_total = (self.square_total + value * value) % modulus

    @property
    def digest(self) -> str:
        return framed_sha256(
            "futureagi.property-catalog.source-audit-multiset.v1",
            self.count,
            f"{self.xor:064x}",
            f"{self.total:064x}",
            f"{self.square_total:064x}",
        )


@dataclass(frozen=True, slots=True)
class StreamRequirement:
    source_adapter: SourceAdapter
    producer_stream_id: str
    source_version_fence: int
    expected_source_count: int
    expected_definition_count: int
    expected_value_count: int
    expected_tombstone_count: int
    expected_source_digest: str
    expected_emitted_digest: str
    expected_first_sequence: int | None
    expected_last_sequence: int | None
    expected_terminal_payload_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_adapter, SourceAdapter):
            raise TypeError("source_adapter must be a SourceAdapter")
        _require_uint(
            self.source_version_fence,
            bits=64,
            field="source_version_fence",
            positive=True,
        )
        for field_name in (
            "expected_source_count",
            "expected_definition_count",
            "expected_value_count",
            "expected_tombstone_count",
        ):
            _require_uint64(getattr(self, field_name), field=field_name)
        require_sha256(self.expected_source_digest, field="expected_source_digest")
        require_sha256(self.expected_emitted_digest, field="expected_emitted_digest")
        require_sha256(
            self.expected_terminal_payload_sha256,
            field="expected_terminal_payload_sha256",
        )
        object.__setattr__(
            self,
            "producer_stream_id",
            canonical_uuid(self.producer_stream_id, field="producer_stream_id"),
        )
        _validate_sequence_range(
            self.expected_first_sequence,
            self.expected_last_sequence,
            field="expected sequence",
        )
        if self.expected_first_sequence is None:
            raise ValueError(
                "every source stream requires a terminal delivery sequence"
            )
        if self.expected_first_sequence != 1:
            raise ValueError("every source stream must start at sequence one")

    @property
    def key(self) -> tuple[SourceAdapter, str]:
        return self.source_adapter, self.producer_stream_id


@dataclass(frozen=True, slots=True)
class CatalogCheckpoint:
    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    source_adapter: SourceAdapter
    producer_stream_id: str
    source_version_fence: int
    status: CheckpointStatus
    terminal: bool
    source_count: int
    definition_count: int
    value_count: int
    tombstone_count: int
    gap_count: int
    poison_count: int
    conflict_count: int
    first_sequence: int | None
    last_sequence: int | None
    last_issued_sequence: int
    fenced_sequence: int
    terminal_payload_sha256: str
    delivery_count: int
    source_digest: str
    emitted_digest: str

    def __post_init__(self) -> None:
        _require_uint(self.catalog_epoch, bits=16, field="catalog_epoch", positive=True)
        _require_uint(
            self.catalog_revision,
            bits=64,
            field="catalog_revision",
            positive=True,
        )
        _require_uint(
            self.projection_version,
            bits=16,
            field="projection_version",
            positive=True,
        )
        if not isinstance(self.source_adapter, SourceAdapter):
            raise TypeError("source_adapter must be a SourceAdapter")
        _require_uint(
            self.source_version_fence,
            bits=64,
            field="source_version_fence",
            positive=True,
        )
        if not isinstance(self.status, CheckpointStatus):
            raise TypeError("status must be a CheckpointStatus")
        if type(self.terminal) is not bool:
            raise TypeError("terminal must be a bool")
        for field_name in (
            "source_count",
            "definition_count",
            "value_count",
            "tombstone_count",
            "gap_count",
            "poison_count",
            "conflict_count",
            "delivery_count",
        ):
            _require_uint64(getattr(self, field_name), field=field_name)
        require_sha256(self.source_digest, field="source_digest")
        require_sha256(self.emitted_digest, field="emitted_digest")
        require_sha256(
            self.terminal_payload_sha256,
            field="terminal_payload_sha256",
        )
        object.__setattr__(
            self,
            "organization_id",
            canonical_uuid(self.organization_id, field="organization_id"),
        )
        object.__setattr__(
            self,
            "workspace_id",
            canonical_uuid(self.workspace_id, field="workspace_id"),
        )
        object.__setattr__(
            self,
            "build_token",
            canonical_uuid(self.build_token, field="build_token"),
        )
        object.__setattr__(
            self,
            "producer_stream_id",
            canonical_uuid(self.producer_stream_id, field="producer_stream_id"),
        )
        _validate_sequence_range(
            self.first_sequence,
            self.last_sequence,
            field="checkpoint sequence",
        )
        _require_uint(
            self.last_issued_sequence,
            bits=64,
            field="last_issued_sequence",
        )
        _require_uint(
            self.fenced_sequence,
            bits=64,
            field="fenced_sequence",
        )
        if self.terminal and (
            self.last_sequence is None
            or self.last_issued_sequence != self.last_sequence
            or self.fenced_sequence != self.last_sequence
        ):
            raise ValueError(
                "terminal checkpoint requires an exactly fenced terminal delivery"
            )

    @property
    def key(self) -> tuple[SourceAdapter, str]:
        return self.source_adapter, self.producer_stream_id

    @property
    def state_sha256(self) -> str:
        return framed_sha256(
            "futureagi.property-catalog.checkpoint.v1",
            self.organization_id,
            self.workspace_id,
            self.catalog_epoch,
            self.catalog_revision,
            self.build_token,
            self.projection_version,
            self.source_adapter,
            self.producer_stream_id,
            self.source_version_fence,
            self.status,
            self.terminal,
            self.source_count,
            self.definition_count,
            self.value_count,
            self.tombstone_count,
            self.gap_count,
            self.poison_count,
            self.conflict_count,
            self.first_sequence,
            self.last_sequence,
            self.last_issued_sequence,
            self.fenced_sequence,
            self.terminal_payload_sha256,
            self.delivery_count,
            self.source_digest,
            self.emitted_digest,
        )


@dataclass(frozen=True, slots=True)
class RevisionRequirement:
    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    streams: tuple[StreamRequirement, ...]

    def __post_init__(self) -> None:
        _require_uint(self.catalog_epoch, bits=16, field="catalog_epoch", positive=True)
        _require_uint(
            self.catalog_revision,
            bits=64,
            field="catalog_revision",
            positive=True,
        )
        _require_uint(
            self.projection_version,
            bits=16,
            field="projection_version",
            positive=True,
        )
        if not isinstance(self.streams, tuple):
            raise TypeError("streams must be a tuple")
        keys = [stream.key for stream in self.streams]
        if len(keys) != len(set(keys)):
            raise ValueError("revision requirements contain duplicate streams")
        if not self.streams:
            raise ValueError("revision qualification requires at least one stream")
        object.__setattr__(
            self,
            "organization_id",
            canonical_uuid(self.organization_id, field="organization_id"),
        )
        object.__setattr__(
            self,
            "workspace_id",
            canonical_uuid(self.workspace_id, field="workspace_id"),
        )
        object.__setattr__(
            self,
            "build_token",
            canonical_uuid(self.build_token, field="build_token"),
        )


@dataclass(frozen=True, slots=True)
class RevisionQualification:
    qualified: bool
    issues: tuple[str, ...]
    activation_sha256: str | None


def qualify_revision(
    requirement: RevisionRequirement,
    checkpoints: tuple[CatalogCheckpoint, ...] | list[CatalogCheckpoint],
) -> RevisionQualification:
    """Require an exact, terminal, contiguous checkpoint set."""

    issues: list[str] = []
    actual: dict[tuple[SourceAdapter, str], CatalogCheckpoint] = {}
    duplicates: set[tuple[SourceAdapter, str]] = set()
    for checkpoint in checkpoints:
        if checkpoint.key in actual:
            duplicates.add(checkpoint.key)
        else:
            actual[checkpoint.key] = checkpoint
    for key in sorted(duplicates, key=_key_text):
        issues.append(f"duplicate_checkpoint:{_key_text(key)}")

    expected = {stream.key: stream for stream in requirement.streams}
    for key in sorted(set(actual) - set(expected), key=_key_text):
        issues.append(f"unexpected_stream:{_key_text(key)}")

    accepted: list[CatalogCheckpoint] = []
    for key in sorted(expected, key=_key_text):
        stream = expected[key]
        checkpoint = actual.get(key)
        prefix = _key_text(key)
        if checkpoint is None:
            issues.append(f"missing_stream:{prefix}")
            continue
        if (
            checkpoint.organization_id != requirement.organization_id
            or checkpoint.workspace_id != requirement.workspace_id
            or checkpoint.catalog_epoch != requirement.catalog_epoch
            or checkpoint.catalog_revision != requirement.catalog_revision
            or checkpoint.build_token != requirement.build_token
            or checkpoint.projection_version != requirement.projection_version
        ):
            issues.append(f"scope_mismatch:{prefix}")
        if checkpoint.source_version_fence != stream.source_version_fence:
            issues.append(f"source_version_fence_mismatch:{prefix}")
        if checkpoint.status is not CheckpointStatus.COMPLETE:
            issues.append(f"not_complete:{prefix}")
        if not checkpoint.terminal:
            issues.append(f"not_terminal:{prefix}")
        if (
            checkpoint.last_sequence is None
            or checkpoint.last_issued_sequence != checkpoint.last_sequence
            or checkpoint.fenced_sequence != checkpoint.last_sequence
        ):
            issues.append(f"not_fenced_at_terminal:{prefix}")
        if checkpoint.gap_count:
            issues.append(f"gaps:{prefix}")
        if checkpoint.poison_count:
            issues.append(f"poison:{prefix}")
        if checkpoint.conflict_count:
            issues.append(f"conflicts:{prefix}")
        _compare(
            checkpoint.source_count,
            stream.expected_source_count,
            "source_count",
            prefix,
            issues,
        )
        _compare(
            checkpoint.definition_count,
            stream.expected_definition_count,
            "definition_count",
            prefix,
            issues,
        )
        _compare(
            checkpoint.value_count,
            stream.expected_value_count,
            "value_count",
            prefix,
            issues,
        )
        _compare(
            checkpoint.tombstone_count,
            stream.expected_tombstone_count,
            "tombstone_count",
            prefix,
            issues,
        )
        if checkpoint.source_digest != stream.expected_source_digest:
            issues.append(f"source_digest_mismatch:{prefix}")
        if checkpoint.emitted_digest != stream.expected_emitted_digest:
            issues.append(f"emitted_digest_mismatch:{prefix}")
        if (
            checkpoint.terminal_payload_sha256
            != stream.expected_terminal_payload_sha256
        ):
            issues.append(f"terminal_payload_mismatch:{prefix}")
        if (
            checkpoint.first_sequence != stream.expected_first_sequence
            or checkpoint.last_sequence != stream.expected_last_sequence
        ):
            issues.append(f"sequence_fence_mismatch:{prefix}")
        expected_deliveries = _sequence_count(
            checkpoint.first_sequence,
            checkpoint.last_sequence,
        )
        if checkpoint.delivery_count != expected_deliveries:
            issues.append(f"non_contiguous_delivery:{prefix}")
        accepted.append(checkpoint)

    if issues:
        return RevisionQualification(False, tuple(issues), None)
    activation_sha256 = framed_sha256(
        "futureagi.property-catalog.activation.v1",
        requirement.organization_id,
        requirement.workspace_id,
        requirement.catalog_epoch,
        requirement.catalog_revision,
        requirement.build_token,
        requirement.projection_version,
        *(checkpoint.state_sha256 for checkpoint in accepted),
    )
    return RevisionQualification(True, (), activation_sha256)


def _compare(
    actual: int, expected: int, name: str, prefix: str, issues: list[str]
) -> None:
    if actual != expected:
        issues.append(f"{name}_mismatch:{prefix}")


def _key_text(key: tuple[SourceAdapter, str]) -> str:
    return f"{key[0]}:{key[1]}"


def _validate_sequence_range(
    first: int | None,
    last: int | None,
    *,
    field: str,
) -> None:
    if (first is None) != (last is None):
        raise ValueError(f"{field} bounds must both be set or both be absent")
    if first is not None:
        _require_uint(first, bits=64, field=f"{field} first", positive=True)
        assert last is not None
        _require_uint(last, bits=64, field=f"{field} last", positive=True)
        if last < first:
            raise ValueError(f"{field} bounds are invalid")


def _sequence_count(first: int | None, last: int | None) -> int:
    if first is None or last is None:
        return 0
    return last - first + 1


def _require_uint64(value: int, *, field: str) -> None:
    _require_uint(value, bits=64, field=field)


def _require_uint(
    value: int,
    *,
    bits: int,
    field: str,
    positive: bool = False,
) -> None:
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value <= (1 << bits) - 1:
        qualifier = "positive " if positive else ""
        raise ValueError(f"{field} must be a {qualifier}UInt{bits}")
