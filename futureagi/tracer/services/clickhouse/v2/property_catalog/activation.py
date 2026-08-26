"""Append-only qualification, fencing, and activation contracts."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from .codec import (
    canonical_json,
    canonical_json_sha256,
    canonical_uuid,
    framed_sha256,
    require_sha256,
)
from .models import SourceAdapter
from .qualification import (
    CatalogCheckpoint,
    RevisionQualification,
    RevisionRequirement,
    StreamRequirement,
    qualify_revision,
)

EXPECTED_SOURCE_ADAPTERS = frozenset(SourceAdapter)
_SOURCE_CUTOFF_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ManifestStreamRole(StrEnum):
    DEFINITIONS = "definitions"
    # Revision-pinned canonical-span scan that writes the complete value set.
    VALUES = "values"
    # Collector/Kafka acceleration proof; never a completeness substitute.
    HOT_VALUES = "hot_values"
    # Independent second canonical-span scan with no value writes.
    SOURCE_AUDIT = "source_audit"


@dataclass(frozen=True, slots=True)
class BuildPlanStream:
    """One immutable stream admission declared before the first catalog write."""

    source_adapter: SourceAdapter
    role: ManifestStreamRole
    producer_stream_id: str
    source_cutoff_label: str
    source_version_fence: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_adapter, SourceAdapter):
            raise TypeError("source_adapter must be a SourceAdapter")
        if not isinstance(self.role, ManifestStreamRole):
            raise TypeError("role must be a ManifestStreamRole")
        object.__setattr__(
            self,
            "producer_stream_id",
            canonical_uuid(self.producer_stream_id, field="producer_stream_id"),
        )
        if (
            not isinstance(self.source_cutoff_label, str)
            or _SOURCE_CUTOFF_LABEL_RE.fullmatch(self.source_cutoff_label) is None
        ):
            raise ValueError("source_cutoff_label must be a canonical lowercase label")
        if type(
            self.source_version_fence
        ) is not int or not 1 <= self.source_version_fence < (1 << 64):
            raise ValueError("source_version_fence must be a positive UInt64")
        if (
            self.source_adapter is not SourceAdapter.SPAN_ATTRIBUTE
            and self.role is not ManifestStreamRole.DEFINITIONS
        ):
            raise ValueError("only span_attribute has native value/audit streams")

    @property
    def key(self) -> tuple[SourceAdapter, str]:
        return self.source_adapter, self.producer_stream_id


@dataclass(frozen=True, slots=True)
class BuildPlanSourceScope:
    """Exact tenant/project/time admission bound into one build lease."""

    project_ids: tuple[str, ...]
    span_since_us: int
    span_until_us: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.project_ids, tuple)
            or not 1 <= len(self.project_ids) <= 256
        ):
            raise ValueError("build source scope requires 1..256 project_ids")
        projects = tuple(
            sorted(
                canonical_uuid(project_id, field="source_scope project_id")
                for project_id in self.project_ids
            )
        )
        if len(set(projects)) != len(projects):
            raise ValueError("build source scope contains duplicate project_ids")
        object.__setattr__(self, "project_ids", projects)
        for field_name in ("span_since_us", "span_until_us"):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value < (1 << 64):
                raise ValueError(f"{field_name} must be a positive UInt64")
        if self.span_since_us >= self.span_until_us:
            raise ValueError("build source scope requires a non-empty half-open window")


@dataclass(frozen=True, slots=True)
class RevisionBuildPlan:
    """Canonical pre-write plan hashed into every reservation and stream row."""

    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    source_scope: BuildPlanSourceScope
    streams: tuple[BuildPlanStream, ...]

    def __post_init__(self) -> None:
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
        for field_name, bits in (
            ("catalog_epoch", 16),
            ("catalog_revision", 64),
            ("projection_version", 16),
        ):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value < (1 << bits):
                raise ValueError(f"{field_name} must be a positive UInt{bits}")
        if not isinstance(self.source_scope, BuildPlanSourceScope):
            raise TypeError("build plan source_scope must be BuildPlanSourceScope")
        if not isinstance(self.streams, tuple) or any(
            not isinstance(stream, BuildPlanStream) for stream in self.streams
        ):
            raise TypeError("build plan streams must be BuildPlanStream tuples")
        if len({stream.key for stream in self.streams}) != len(self.streams):
            raise ValueError("build plan contains duplicate stream keys")
        _validate_role_inventory(
            tuple((stream.source_adapter, stream.role) for stream in self.streams),
            label="build plan",
        )

    @property
    def canonical_json(self) -> str:
        return canonical_json(
            {
                "build_token": self.build_token,
                "catalog_epoch": self.catalog_epoch,
                "catalog_revision": self.catalog_revision,
                "format": "futureagi.property-catalog-build-plan",
                "organization_id": self.organization_id,
                "projection_version": self.projection_version,
                "source_scope": {
                    "project_ids": list(self.source_scope.project_ids),
                    "span_since_us": self.source_scope.span_since_us,
                    "span_until_us": self.source_scope.span_until_us,
                },
                "streams": [
                    {
                        "producer_stream_id": stream.producer_stream_id,
                        "role": stream.role,
                        "source_adapter": stream.source_adapter,
                        "source_cutoff": {
                            "label": stream.source_cutoff_label,
                            "value": stream.source_version_fence,
                        },
                    }
                    for stream in sorted(
                        self.streams,
                        key=lambda value: (
                            value.source_adapter,
                            value.role,
                            value.producer_stream_id,
                        ),
                    )
                ],
                "version": 2,
                "workspace_id": self.workspace_id,
            }
        )

    @property
    def sha256(self) -> str:
        return canonical_json_sha256(self.canonical_json)

    @classmethod
    def from_json(cls, value: str) -> RevisionBuildPlan:
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("build_plan_json is invalid") from exc
        if not isinstance(decoded, dict) or set(decoded) != {
            "build_token",
            "catalog_epoch",
            "catalog_revision",
            "format",
            "organization_id",
            "projection_version",
            "source_scope",
            "streams",
            "version",
            "workspace_id",
        }:
            raise ValueError("build_plan_json has unsupported fields")
        if (
            decoded["format"] != "futureagi.property-catalog-build-plan"
            or decoded["version"] != 2
            or canonical_json(decoded) != value
            or not isinstance(decoded["streams"], list)
        ):
            raise ValueError("build_plan_json is not canonical v2")
        raw_scope = decoded["source_scope"]
        if not isinstance(raw_scope, dict) or set(raw_scope) != {
            "project_ids",
            "span_since_us",
            "span_until_us",
        }:
            raise ValueError("build_plan_json source_scope is invalid")
        if not isinstance(raw_scope["project_ids"], list):
            raise ValueError("build_plan_json source_scope project_ids are invalid")
        streams: list[BuildPlanStream] = []
        for raw in decoded["streams"]:
            if not isinstance(raw, dict) or set(raw) != {
                "producer_stream_id",
                "role",
                "source_adapter",
                "source_cutoff",
            }:
                raise ValueError("build_plan_json stream has unsupported fields")
            cutoff = raw["source_cutoff"]
            if not isinstance(cutoff, dict) or set(cutoff) != {"label", "value"}:
                raise ValueError("build_plan_json source cutoff is invalid")
            streams.append(
                BuildPlanStream(
                    source_adapter=SourceAdapter(raw["source_adapter"]),
                    role=ManifestStreamRole(raw["role"]),
                    producer_stream_id=raw["producer_stream_id"],
                    source_cutoff_label=cutoff["label"],
                    source_version_fence=cutoff["value"],
                )
            )
        result = cls(
            organization_id=decoded["organization_id"],
            workspace_id=decoded["workspace_id"],
            catalog_epoch=decoded["catalog_epoch"],
            catalog_revision=decoded["catalog_revision"],
            build_token=decoded["build_token"],
            projection_version=decoded["projection_version"],
            source_scope=BuildPlanSourceScope(
                project_ids=tuple(raw_scope["project_ids"]),
                span_since_us=raw_scope["span_since_us"],
                span_until_us=raw_scope["span_until_us"],
            ),
            streams=tuple(streams),
        )
        if result.canonical_json != value:
            raise ValueError("build_plan_json is not in canonical v2 order")
        return result

    def matches_manifest(self, manifest: ActivationManifest) -> bool:
        return (
            self.organization_id == manifest.organization_id
            and self.workspace_id == manifest.workspace_id
            and self.catalog_epoch == manifest.catalog_epoch
            and self.catalog_revision == manifest.catalog_revision
            and self.build_token == manifest.build_token
            and self.projection_version == manifest.projection_version
            and {
                (
                    stream.source_adapter,
                    stream.role,
                    stream.producer_stream_id,
                    stream.source_version_fence,
                )
                for stream in self.streams
            }
            == {
                (
                    stream.requirement.source_adapter,
                    stream.role,
                    stream.requirement.producer_stream_id,
                    stream.requirement.source_version_fence,
                )
                for stream in manifest.streams
            }
        )


class ActivationStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class CatalogLifecycleMode(StrEnum):
    INITIAL_BACKFILL = "initial_backfill"
    INCREMENTAL = "incremental"
    FULL_REPAIR = "full_repair"


class RevisionFenceStatus(StrEnum):
    FENCED = "fenced"


class PropertyCatalogActivationError(RuntimeError):
    """A revision is not safe to expose."""


class ActivationRejected(PropertyCatalogActivationError):
    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            "property catalog activation rejected: " + ", ".join(self.issues)
        )


@dataclass(frozen=True, slots=True)
class ManifestStream:
    requirement: StreamRequirement
    role: ManifestStreamRole

    def __post_init__(self) -> None:
        if not isinstance(self.requirement, StreamRequirement):
            raise TypeError("requirement must be a StreamRequirement")
        if not isinstance(self.role, ManifestStreamRole):
            raise TypeError("role must be a ManifestStreamRole")
        if (
            self.requirement.source_adapter is not SourceAdapter.SPAN_ATTRIBUTE
            and self.role is not ManifestStreamRole.DEFINITIONS
        ):
            raise ValueError("only span_attribute has native value/audit streams")
        _validate_role_counts(self.role, self.requirement)

    @property
    def key(self) -> tuple[SourceAdapter, str]:
        return self.requirement.key


@dataclass(frozen=True, slots=True)
class ActivationManifest:
    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    lifecycle_mode: CatalogLifecycleMode
    lineage_anchor_revision: int
    streams: tuple[ManifestStream, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.streams, tuple):
            raise TypeError("streams must be a tuple")
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
        if type(self.catalog_epoch) is not int or not 1 <= self.catalog_epoch <= 65_535:
            raise ValueError("catalog_epoch must be a positive UInt16")
        if type(self.catalog_revision) is not int or not 1 <= self.catalog_revision < (
            1 << 64
        ):
            raise ValueError("catalog_revision must be a positive UInt64")
        if (
            type(self.projection_version) is not int
            or not 1 <= self.projection_version <= 65_535
        ):
            raise ValueError("projection_version must be a positive UInt16")
        if not isinstance(self.lifecycle_mode, CatalogLifecycleMode):
            raise TypeError("lifecycle_mode must be a CatalogLifecycleMode")
        if type(self.lineage_anchor_revision) is not int or not (
            1 <= self.lineage_anchor_revision <= self.catalog_revision
        ):
            raise ValueError(
                "lineage_anchor_revision must be a positive revision at or before catalog_revision"
            )
        if self.lifecycle_mode in {
            CatalogLifecycleMode.INITIAL_BACKFILL,
            CatalogLifecycleMode.FULL_REPAIR,
        }:
            if self.lineage_anchor_revision != self.catalog_revision:
                raise ValueError(
                    "initial/full lifecycle must anchor at its own revision"
                )
        elif self.lineage_anchor_revision >= self.catalog_revision:
            raise ValueError("incremental lifecycle must inherit an earlier anchor")
        if len({stream.key for stream in self.streams}) != len(self.streams):
            raise ValueError("activation manifest contains duplicate stream keys")
        if {stream.requirement.source_adapter for stream in self.streams} != (
            EXPECTED_SOURCE_ADAPTERS
        ):
            raise ValueError("activation manifest must contain all seven adapters")
        span_streams = tuple(
            stream
            for stream in self.streams
            if stream.requirement.source_adapter is SourceAdapter.SPAN_ATTRIBUTE
        )
        span_roles = tuple(stream.role for stream in span_streams)
        required_span_roles = {
            ManifestStreamRole.DEFINITIONS,
            ManifestStreamRole.VALUES,
            ManifestStreamRole.HOT_VALUES,
            ManifestStreamRole.SOURCE_AUDIT,
        }
        if len(span_streams) != 4 or any(
            span_roles.count(role) != 1 for role in required_span_roles
        ):
            raise ValueError(
                "span_attribute manifest requires definition, authoritative-value, "
                "hot-value, and source-audit streams"
            )
        for adapter in EXPECTED_SOURCE_ADAPTERS - {SourceAdapter.SPAN_ATTRIBUTE}:
            adapter_streams = tuple(
                stream
                for stream in self.streams
                if stream.requirement.source_adapter is adapter
            )
            if (
                len(adapter_streams) != 1
                or adapter_streams[0].role is not ManifestStreamRole.DEFINITIONS
            ):
                raise ValueError(
                    f"{adapter} manifest requires exactly one definitions stream"
                )
        span_by_role = {
            stream.role: stream.requirement
            for stream in self.streams
            if stream.requirement.source_adapter is SourceAdapter.SPAN_ATTRIBUTE
        }
        authoritative_values = span_by_role[ManifestStreamRole.VALUES]
        source_audit = span_by_role[ManifestStreamRole.SOURCE_AUDIT]
        if (
            authoritative_values.source_version_fence
            != source_audit.source_version_fence
            or authoritative_values.expected_source_count
            != source_audit.expected_source_count
            or authoritative_values.expected_source_digest
            != source_audit.expected_source_digest
        ):
            raise ValueError(
                "authoritative value scan and independent source audit must prove "
                "the same source audit generation, count, and digest"
            )
        # The hot Kafka stream remains independently terminal/contiguous, but
        # its batch-dependent count and digest are intentionally not compared
        # with the authoritative scan.
        _ = span_by_role[ManifestStreamRole.HOT_VALUES]
        for stream in self.streams:
            requirement = stream.requirement
            # Zero-row sources are represented by a terminal stream with zero
            # counts and a real delivery fence; they are never omitted.
            if requirement.source_adapter is SourceAdapter.SPAN_ATTRIBUTE:
                continue
            if stream.role is not ManifestStreamRole.DEFINITIONS:
                raise ValueError("PG/system streams must project definitions")

    @property
    def revision_requirement(self) -> RevisionRequirement:
        return RevisionRequirement(
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            catalog_epoch=self.catalog_epoch,
            catalog_revision=self.catalog_revision,
            build_token=self.build_token,
            projection_version=self.projection_version,
            streams=tuple(stream.requirement for stream in self.streams),
        )

    @property
    def canonical_json(self) -> str:
        return canonical_json(
            {
                "catalog_epoch": self.catalog_epoch,
                "catalog_revision": self.catalog_revision,
                "build_token": self.build_token,
                "lifecycle_mode": self.lifecycle_mode,
                "lineage_anchor_revision": self.lineage_anchor_revision,
                "organization_id": self.organization_id,
                "projection_version": self.projection_version,
                "streams": [
                    {
                        "expected_definition_count": item.requirement.expected_definition_count,
                        "expected_emitted_digest": item.requirement.expected_emitted_digest,
                        "expected_first_sequence": item.requirement.expected_first_sequence,
                        "expected_last_sequence": item.requirement.expected_last_sequence,
                        "expected_terminal_payload_sha256": (
                            item.requirement.expected_terminal_payload_sha256
                        ),
                        "source_version_fence": item.requirement.source_version_fence,
                        "expected_source_count": item.requirement.expected_source_count,
                        "expected_source_digest": item.requirement.expected_source_digest,
                        "expected_tombstone_count": item.requirement.expected_tombstone_count,
                        "expected_value_count": item.requirement.expected_value_count,
                        "producer_stream_id": item.requirement.producer_stream_id,
                        "role": item.role,
                        "source_adapter": item.requirement.source_adapter,
                    }
                    for item in sorted(
                        self.streams,
                        key=lambda value: (
                            value.requirement.source_adapter,
                            value.role,
                            value.requirement.producer_stream_id,
                        ),
                    )
                ],
                "workspace_id": self.workspace_id,
            }
        )

    @property
    def sha256(self) -> str:
        return canonical_json_sha256(self.canonical_json)


@dataclass(frozen=True, slots=True)
class RevisionLease:
    """Atomically allocated, expiring ownership of one workspace revision."""

    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    projection_version: int
    build_token: str
    build_plan_json: str
    build_lease_sha256: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
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
        require_sha256(self.build_lease_sha256, field="build_lease_sha256")
        for field_name, bits in (
            ("catalog_epoch", 16),
            ("catalog_revision", 64),
            ("projection_version", 16),
        ):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value < (1 << bits):
                raise ValueError(f"{field_name} must be a positive UInt{bits}")
        _require_utc(self.issued_at, "issued_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("revision lease must expire after it is issued")
        plan = RevisionBuildPlan.from_json(self.build_plan_json)
        if (
            plan.organization_id != self.organization_id
            or plan.workspace_id != self.workspace_id
            or plan.catalog_epoch != self.catalog_epoch
            or plan.catalog_revision != self.catalog_revision
            or plan.build_token != self.build_token
            or plan.projection_version != self.projection_version
            or plan.sha256 != self.build_lease_sha256
        ):
            raise ValueError("revision lease does not match its canonical build plan")

    @property
    def build_plan(self) -> RevisionBuildPlan:
        return RevisionBuildPlan.from_json(self.build_plan_json)


@dataclass(frozen=True, slots=True)
class StreamDrainProof:
    source_adapter: SourceAdapter
    producer_stream_id: str
    last_issued_sequence: int
    fenced_sequence: int
    terminal_sequence: int
    terminal_payload_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_adapter, SourceAdapter):
            raise TypeError("source_adapter must be a SourceAdapter")
        object.__setattr__(
            self,
            "producer_stream_id",
            canonical_uuid(self.producer_stream_id, field="producer_stream_id"),
        )
        for field_name in (
            "last_issued_sequence",
            "fenced_sequence",
            "terminal_sequence",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value < (1 << 64):
                raise ValueError(f"{field_name} must be a positive UInt64")
        if not (
            self.last_issued_sequence == self.fenced_sequence == self.terminal_sequence
        ):
            raise ValueError(
                "stream drain proof is not fenced at its terminal delivery"
            )
        require_sha256(
            self.terminal_payload_sha256,
            field="terminal_payload_sha256",
        )

    @property
    def key(self) -> tuple[SourceAdapter, str]:
        return self.source_adapter, self.producer_stream_id


@dataclass(frozen=True, slots=True)
class RevisionFence:
    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    build_plan_json: str
    build_lease_sha256: str
    manifest_sha256: str
    status: RevisionFenceStatus
    stream_proofs: tuple[StreamDrainProof, ...]
    checkpoint_state_sha256s: tuple[str, ...]
    drain_deadline: datetime
    fenced_at: datetime
    fence_sha256: str

    def __post_init__(self) -> None:
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
        if self.status is not RevisionFenceStatus.FENCED:
            raise ValueError("only a fully fenced revision may be activated")
        require_sha256(self.build_lease_sha256, field="build_lease_sha256")
        require_sha256(self.manifest_sha256, field="manifest_sha256")
        require_sha256(self.fence_sha256, field="fence_sha256")
        if not self.stream_proofs:
            raise ValueError("revision fence requires stream drain proofs")
        if len({proof.key for proof in self.stream_proofs}) != len(self.stream_proofs):
            raise ValueError("revision fence contains duplicate stream proofs")
        if not self.checkpoint_state_sha256s:
            raise ValueError("revision fence requires checkpoint states")
        for value in self.checkpoint_state_sha256s:
            require_sha256(value, field="checkpoint_state_sha256")
        _require_utc(self.drain_deadline, "drain_deadline")
        _require_utc(self.fenced_at, "fenced_at")
        if self.fenced_at > self.drain_deadline:
            raise ValueError("revision was not fenced before its drain deadline")
        plan = RevisionBuildPlan.from_json(self.build_plan_json)
        if (
            plan.organization_id != self.organization_id
            or plan.workspace_id != self.workspace_id
            or plan.catalog_epoch != self.catalog_epoch
            or plan.catalog_revision != self.catalog_revision
            or plan.build_token != self.build_token
            or plan.projection_version != self.projection_version
            or plan.sha256 != self.build_lease_sha256
        ):
            raise ValueError("revision fence does not match its build plan")
        expected = framed_sha256(
            "futureagi.property-catalog.revision-fence.v3",
            self.organization_id,
            self.workspace_id,
            self.catalog_epoch,
            self.catalog_revision,
            self.build_token,
            self.projection_version,
            self.build_lease_sha256,
            self.manifest_sha256,
            self.status,
            self.drain_deadline.isoformat(timespec="microseconds"),
            *(
                component
                for proof in self.stream_proofs
                for component in (
                    proof.source_adapter,
                    proof.producer_stream_id,
                    proof.last_issued_sequence,
                    proof.fenced_sequence,
                    proof.terminal_sequence,
                    proof.terminal_payload_sha256,
                )
            ),
            *self.checkpoint_state_sha256s,
        )
        if self.fence_sha256 != expected:
            raise ValueError("fence_sha256 does not match fence fields")


@dataclass(frozen=True, slots=True)
class ActivationInventory:
    live_definition_rows: int
    tombstone_rows: int
    value_rows: int

    def __post_init__(self) -> None:
        for field_name in (
            "live_definition_rows",
            "tombstone_rows",
            "value_rows",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or not 0 <= value < (1 << 64):
                raise ValueError(f"{field_name} must be a UInt64")


@dataclass(frozen=True, slots=True)
class ActivationRecord:
    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    lifecycle_mode: CatalogLifecycleMode
    lineage_anchor_revision: int
    activation_sequence: int
    source_manifest_json: str
    source_manifest_sha256: str
    revision_fence_sha256: str
    activation_sha256: str
    status: ActivationStatus
    live_definition_rows: int
    tombstone_rows: int
    value_rows: int
    qualified_at: datetime
    updated_at: datetime
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.status, ActivationStatus):
            raise TypeError("status must be an ActivationStatus")
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
        for field_name, bits in (
            ("catalog_epoch", 16),
            ("catalog_revision", 64),
            ("projection_version", 16),
            ("activation_sequence", 64),
            ("version", 64),
        ):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value < (1 << bits):
                raise ValueError(f"{field_name} must be a positive UInt{bits}")
        if not isinstance(self.lifecycle_mode, CatalogLifecycleMode):
            raise TypeError("lifecycle_mode must be a CatalogLifecycleMode")
        if type(self.lineage_anchor_revision) is not int or not (
            1 <= self.lineage_anchor_revision <= self.catalog_revision
        ):
            raise ValueError("activation lineage anchor is outside its revision")
        if self.lifecycle_mode in {
            CatalogLifecycleMode.INITIAL_BACKFILL,
            CatalogLifecycleMode.FULL_REPAIR,
        }:
            if self.lineage_anchor_revision != self.catalog_revision:
                raise ValueError("initial/full activation must anchor at itself")
        elif self.lineage_anchor_revision >= self.catalog_revision:
            raise ValueError("incremental activation must inherit an earlier anchor")
        require_sha256(self.source_manifest_sha256, field="source_manifest_sha256")
        require_sha256(self.revision_fence_sha256, field="revision_fence_sha256")
        require_sha256(self.activation_sha256, field="activation_sha256")
        try:
            decoded = json.loads(self.source_manifest_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("source_manifest_json is invalid") from exc
        if canonical_json(decoded) != self.source_manifest_json:
            raise ValueError("source_manifest_json is not canonical")
        if (
            canonical_json_sha256(self.source_manifest_json)
            != self.source_manifest_sha256
        ):
            raise ValueError("source manifest digest mismatch")
        if (
            decoded.get("lifecycle_mode") != self.lifecycle_mode
            or decoded.get("lineage_anchor_revision") != self.lineage_anchor_revision
        ):
            raise ValueError(
                "source manifest lifecycle lineage differs from activation"
            )
        for field_name in (
            "live_definition_rows",
            "tombstone_rows",
            "value_rows",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or not 0 <= value < (1 << 64):
                raise ValueError(f"{field_name} must be a UInt64")
        _require_utc(self.qualified_at, "qualified_at")
        _require_utc(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class ActivationResult:
    record: ActivationRecord
    qualification: RevisionQualification
    idempotent: bool


@dataclass(frozen=True, slots=True)
class ActivationTarget:
    """Exact qualified build named by an explicit control action."""

    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    expected_activation_sha256: str

    def __post_init__(self) -> None:
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
        if type(self.catalog_epoch) is not int or not (
            1 <= self.catalog_epoch < (1 << 16)
        ):
            raise ValueError("catalog_epoch must be a positive UInt16")
        if type(self.catalog_revision) is not int or not (
            1 <= self.catalog_revision < (1 << 64)
        ):
            raise ValueError("catalog_revision must be a positive UInt64")
        require_sha256(
            self.expected_activation_sha256,
            field="expected_activation_sha256",
        )

    def matches(self, record: ActivationRecord) -> bool:
        return (
            self.organization_id == record.organization_id
            and self.workspace_id == record.workspace_id
            and self.catalog_epoch == record.catalog_epoch
            and self.catalog_revision == record.catalog_revision
            and self.build_token == record.build_token
            and self.expected_activation_sha256 == record.activation_sha256
        )


@dataclass(frozen=True, slots=True)
class ActivationHead:
    """Optimistic-concurrency precondition for one workspace activation ledger."""

    activation_sequence: int
    catalog_revision: int
    build_token: str
    activation_sha256: str
    status: ActivationStatus

    def __post_init__(self) -> None:
        for field_name in ("activation_sequence", "catalog_revision"):
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value < (1 << 64):
                raise ValueError(f"{field_name} must be a positive UInt64")
        object.__setattr__(
            self,
            "build_token",
            canonical_uuid(self.build_token, field="build_token"),
        )
        require_sha256(self.activation_sha256, field="activation_sha256")
        if not isinstance(self.status, ActivationStatus):
            raise TypeError("status must be an ActivationStatus")

    @classmethod
    def from_record(cls, record: ActivationRecord) -> ActivationHead:
        return cls(
            activation_sequence=record.activation_sequence,
            catalog_revision=record.catalog_revision,
            build_token=record.build_token,
            activation_sha256=record.activation_sha256,
            status=record.status,
        )

    def matches(self, record: ActivationRecord) -> bool:
        return (
            self.activation_sequence == record.activation_sequence
            and self.catalog_revision == record.catalog_revision
            and self.build_token == record.build_token
            and self.activation_sha256 == record.activation_sha256
            and self.status is record.status
        )


@dataclass(frozen=True, slots=True)
class ActivationControlRequest:
    """One explicit action plus the exact ledger state it was authorized from."""

    target: ActivationTarget
    expected_head: ActivationHead | None

    def __post_init__(self) -> None:
        if not isinstance(self.target, ActivationTarget):
            raise TypeError("target must be an ActivationTarget")
        if self.expected_head is not None and not isinstance(
            self.expected_head, ActivationHead
        ):
            raise TypeError("expected_head must be an ActivationHead or None")


@dataclass(frozen=True, slots=True)
class ActivationControlResult:
    records: tuple[ActivationRecord, ...]
    active: ActivationRecord | None
    qualification: RevisionQualification | None
    idempotent: bool


class RevisionCoordinator(Protocol):
    """Atomically serialize builds per workspace and fence every writer.

    ``allocate`` must lock the organization/workspace, choose a revision above
    every active or leased revision, and return one expiring lease without
    publishing a building fence. The building assignment is published only
    after every planned stream exists. Drain is an intent/prepared-boundary
    handshake; Python never guesses the Go hot-stream high-water. ``fence``
    revokes all producer writes before returning its immutable proof.
    """

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
    ) -> RevisionLease: ...

    def open_stream(
        self,
        *,
        lease: RevisionLease,
        source_adapter: SourceAdapter,
        producer_stream_id: str,
    ) -> Any: ...

    def publish_building_assignment(self, *, lease: RevisionLease) -> Any: ...

    def begin_drain_intent(
        self,
        *,
        lease: RevisionLease,
        completed_stream_proofs: tuple[StreamDrainProof, ...],
        drain_deadline: datetime,
        now: datetime,
    ) -> Any: ...

    def bind_hot_drain_boundary(
        self,
        *,
        lease: RevisionLease,
        prepared_proof: Any,
        drain_deadline: datetime,
        now: datetime,
    ) -> Any: ...

    def fence(
        self,
        *,
        lease: RevisionLease,
        stream_proofs: tuple[StreamDrainProof, ...],
        checkpoint_state_sha256s: tuple[str, ...],
        final_manifest_sha256: str,
        drain_deadline: datetime,
        now: datetime,
    ) -> RevisionFence: ...


class ActivationStore(Protocol):
    def audit_build_plan(
        self,
        *,
        build_plan: RevisionBuildPlan,
        manifest: ActivationManifest,
    ) -> None: ...

    def load_checkpoints(
        self,
        requirement: RevisionRequirement,
    ) -> Sequence[CatalogCheckpoint]: ...

    def list_activations(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
    ) -> Sequence[ActivationRecord]: ...

    def append_active(
        self,
        record: ActivationRecord,
        *,
        fence_sha256: str,
        checkpoint_state_sha256s: tuple[str, ...],
    ) -> ActivationRecord: ...


class ActivationControlStore(Protocol):
    """Append-only store required by explicit production control actions.

    ``list_activation_events`` must return physical event history, including
    superseded ACTIVE and DISABLED rows. ``append_activation_events`` must
    serialize per workspace, compare the exact current head with
    ``expected_head``, and append the whole batch or none of it. Implementations
    must never UPDATE or DELETE activation rows.
    """

    def audit_build_plan(
        self,
        *,
        build_plan: RevisionBuildPlan,
        manifest: ActivationManifest,
    ) -> None: ...

    def load_checkpoints(
        self,
        requirement: RevisionRequirement,
    ) -> Sequence[CatalogCheckpoint]: ...

    def list_activation_events(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
    ) -> Sequence[ActivationRecord]: ...

    def append_activation_events(
        self,
        records: tuple[ActivationRecord, ...],
        *,
        expected_head: ActivationHead | None,
    ) -> Sequence[ActivationRecord]: ...


class PropertyCatalogActivator:
    def __init__(self, store: ActivationStore) -> None:
        self._store = store

    def activate(
        self,
        *,
        manifest: ActivationManifest,
        fence: RevisionFence,
        inventory: ActivationInventory,
        now: datetime,
    ) -> ActivationResult:
        _require_utc(now, "now")
        self._store.audit_build_plan(
            build_plan=RevisionBuildPlan.from_json(fence.build_plan_json),
            manifest=manifest,
        )
        requirement = manifest.revision_requirement
        checkpoints = tuple(self._store.load_checkpoints(requirement))
        qualification = qualify_revision(requirement, checkpoints)
        if not qualification.qualified or qualification.activation_sha256 is None:
            raise ActivationRejected(qualification.issues)
        checkpoint_states = tuple(
            checkpoint.state_sha256
            for checkpoint in sorted(
                checkpoints,
                key=lambda value: (value.source_adapter, value.producer_stream_id),
            )
        )
        _validate_fence(
            fence,
            manifest=manifest,
            checkpoints=checkpoints,
            checkpoint_states=checkpoint_states,
        )

        activations = tuple(
            self._store.list_activations(
                organization_id=manifest.organization_id,
                workspace_id=manifest.workspace_id,
                catalog_epoch=manifest.catalog_epoch,
            )
        )
        for existing in activations:
            if existing.catalog_revision != manifest.catalog_revision:
                continue
            if (
                existing.build_token == manifest.build_token
                and existing.source_manifest_sha256 == manifest.sha256
                and existing.revision_fence_sha256 == fence.fence_sha256
                and existing.activation_sha256 == qualification.activation_sha256
            ):
                return ActivationResult(existing, qualification, True)
            raise ActivationRejected(("revision_already_activated_with_other_state",))

        latest = max(
            activations, key=lambda value: value.activation_sequence, default=None
        )
        if latest is not None and latest.catalog_revision >= manifest.catalog_revision:
            raise ActivationRejected(("activation_revision_not_monotonic",))
        if latest is None:
            if manifest.lifecycle_mode is not CatalogLifecycleMode.INITIAL_BACKFILL:
                raise ActivationRejected(
                    ("first_activation_requires_initial_backfill",)
                )
        elif manifest.lifecycle_mode is CatalogLifecycleMode.INITIAL_BACKFILL:
            raise ActivationRejected(("initial_backfill_requires_empty_lineage",))
        elif (
            manifest.lifecycle_mode is CatalogLifecycleMode.INCREMENTAL
            and manifest.lineage_anchor_revision != latest.lineage_anchor_revision
        ):
            raise ActivationRejected(("incremental_lineage_anchor_changed",))
        activation_sequence = 1 if latest is None else latest.activation_sequence + 1
        record = ActivationRecord(
            organization_id=manifest.organization_id,
            workspace_id=manifest.workspace_id,
            catalog_epoch=manifest.catalog_epoch,
            catalog_revision=manifest.catalog_revision,
            build_token=manifest.build_token,
            projection_version=manifest.projection_version,
            lifecycle_mode=manifest.lifecycle_mode,
            lineage_anchor_revision=manifest.lineage_anchor_revision,
            activation_sequence=activation_sequence,
            source_manifest_json=manifest.canonical_json,
            source_manifest_sha256=manifest.sha256,
            revision_fence_sha256=fence.fence_sha256,
            activation_sha256=qualification.activation_sha256,
            status=ActivationStatus.ACTIVE,
            live_definition_rows=inventory.live_definition_rows,
            tombstone_rows=inventory.tombstone_rows,
            value_rows=inventory.value_rows,
            qualified_at=now,
            updated_at=now,
            version=activation_sequence,
        )
        appended = self._store.append_active(
            record,
            fence_sha256=fence.fence_sha256,
            checkpoint_state_sha256s=checkpoint_states,
        )
        if appended != record:
            raise PropertyCatalogActivationError(
                "activation store did not preserve the qualified append"
            )
        # Existing active records are deliberately retained: readers with an
        # issued signed cursor continue to pin their exact epoch/revision.
        return ActivationResult(record, qualification, False)


class PropertyCatalogActivationControlPlane:
    """Explicit append-only activation, disable, and rollback actions."""

    def __init__(self, store: ActivationControlStore) -> None:
        self._store = store

    def qualify(
        self,
        *,
        manifest: ActivationManifest,
        fence: RevisionFence,
    ) -> RevisionQualification:
        """Return qualification evidence without changing activation state."""

        return _qualify_control_activation(
            self._store,
            manifest=manifest,
            fence=fence,
        )

    def activate(
        self,
        *,
        request: ActivationControlRequest,
        manifest: ActivationManifest,
        fence: RevisionFence,
        inventory: ActivationInventory,
        now: datetime,
    ) -> ActivationControlResult:
        """Activate only the exact qualified build named by the request."""

        _require_utc(now, "now")
        _require_target_manifest(request.target, manifest)
        qualification = self.qualify(
            manifest=manifest,
            fence=fence,
        )
        if qualification.activation_sha256 != (
            request.target.expected_activation_sha256
        ):
            raise ActivationRejected(("activation_digest_mismatch",))

        events = self._events(request.target)
        prefix, tail = _control_action_window(events, request.expected_head)
        qualified = _qualified_builds(prefix)
        target_key = _activation_key_from_target(request.target)
        if target_key in qualified:
            raise ActivationRejected(("activation_target_already_qualified",))
        if any(_activation_key(event) == target_key for event in prefix):
            raise ActivationRejected(("activation_target_has_unqualified_state",))

        latest_qualified_revision = max(
            (record.catalog_revision for record in qualified.values()),
            default=0,
        )
        if manifest.catalog_revision <= latest_qualified_revision:
            raise ActivationRejected(("activation_revision_not_monotonic",))
        current = _current_active_from_events(prefix)
        if not qualified:
            if manifest.lifecycle_mode is not CatalogLifecycleMode.INITIAL_BACKFILL:
                raise ActivationRejected(
                    ("first_activation_requires_initial_backfill",)
                )
        elif manifest.lifecycle_mode is CatalogLifecycleMode.INITIAL_BACKFILL:
            raise ActivationRejected(("initial_backfill_requires_empty_lineage",))
        elif manifest.lifecycle_mode is CatalogLifecycleMode.INCREMENTAL:
            if current is None:
                raise ActivationRejected(("incremental_requires_active_lineage",))
            if (
                manifest.lineage_anchor_revision != current.lineage_anchor_revision
                or manifest.projection_version != current.projection_version
            ):
                raise ActivationRejected(("incremental_lineage_anchor_changed",))

        next_sequence = _next_activation_sequence(prefix)
        record = ActivationRecord(
            organization_id=manifest.organization_id,
            workspace_id=manifest.workspace_id,
            catalog_epoch=manifest.catalog_epoch,
            catalog_revision=manifest.catalog_revision,
            build_token=manifest.build_token,
            projection_version=manifest.projection_version,
            lifecycle_mode=manifest.lifecycle_mode,
            lineage_anchor_revision=manifest.lineage_anchor_revision,
            activation_sequence=next_sequence,
            source_manifest_json=manifest.canonical_json,
            source_manifest_sha256=manifest.sha256,
            revision_fence_sha256=fence.fence_sha256,
            activation_sha256=qualification.activation_sha256,
            status=ActivationStatus.ACTIVE,
            live_definition_rows=inventory.live_definition_rows,
            tombstone_rows=inventory.tombstone_rows,
            value_rows=inventory.value_rows,
            qualified_at=now,
            updated_at=now,
            version=next_sequence,
        )
        return self._append_plan(
            request=request,
            events=events,
            prefix=prefix,
            tail=tail,
            transitions=((record, ActivationStatus.ACTIVE),),
            qualification=qualification,
            now=now,
        )

    def disable(
        self,
        *,
        request: ActivationControlRequest,
        now: datetime,
    ) -> ActivationControlResult:
        """Disable every active lineage row so readers cannot fall back."""

        _require_utc(now, "now")
        if request.expected_head is None:
            raise ActivationRejected(("disable_requires_active_head",))
        events = self._events(request.target)
        prefix, tail = _control_action_window(events, request.expected_head)
        current = _current_active_from_events(prefix)
        if current is None or not request.target.matches(current):
            raise ActivationRejected(("disable_target_is_not_current",))
        if not request.expected_head.matches(current):
            raise ActivationRejected(("disable_head_is_not_current",))

        active = tuple(
            sorted(
                _active_states(prefix),
                key=lambda record: (
                    record.catalog_revision,
                    record.activation_sequence,
                    record.build_token,
                ),
            )
        )
        transitions = tuple(
            (record, ActivationStatus.DISABLED)
            for record in (*tuple(item for item in active if item != current), current)
        )
        result = self._append_plan(
            request=request,
            events=events,
            prefix=prefix,
            tail=tail,
            transitions=transitions,
            qualification=None,
            now=now,
        )
        if result.active is not None:
            raise PropertyCatalogActivationError(
                "disable append left an active catalog build"
            )
        return result

    def rollback(
        self,
        *,
        request: ActivationControlRequest,
        now: datetime,
    ) -> ActivationControlResult:
        """Append a control transition back to a prior qualified lineage."""

        _require_utc(now, "now")
        if request.expected_head is None:
            raise ActivationRejected(("rollback_requires_existing_head",))
        events = self._events(request.target)
        prefix, tail = _control_action_window(events, request.expected_head)
        target_key = _activation_key_from_target(request.target)
        target_history = tuple(
            event for event in prefix if _activation_key(event) == target_key
        )
        if not target_history:
            if any(
                event.catalog_revision == request.target.catalog_revision
                for event in prefix
            ):
                raise ActivationRejected(("rollback_target_conflict",))
            raise ActivationRejected(("rollback_target_unknown",))
        target_active_history = tuple(
            event for event in target_history if event.status is ActivationStatus.ACTIVE
        )
        if not target_active_history:
            raise ActivationRejected(("rollback_target_not_qualified",))
        target = max(
            target_active_history,
            key=lambda record: record.activation_sequence,
        )
        if not request.target.matches(target):
            raise ActivationRejected(("rollback_target_digest_mismatch",))
        if target.activation_sequence >= request.expected_head.activation_sequence:
            raise ActivationRejected(("rollback_target_not_prior",))

        lineage = _qualified_lineage(prefix, target)
        desired_keys = {_activation_key(record) for record in lineage}
        active_by_key = {
            _activation_key(record): record for record in _active_states(prefix)
        }
        current = _current_active_from_events(prefix)
        if (
            current is not None
            and _activation_key(current) == target_key
            and set(active_by_key) == desired_keys
        ):
            raise ActivationRejected(("rollback_target_already_active",))

        disable = tuple(
            (record, ActivationStatus.DISABLED)
            for key, record in sorted(
                active_by_key.items(),
                key=lambda item: (
                    item[1].catalog_revision,
                    item[1].activation_sequence,
                    item[1].build_token,
                ),
            )
            if key not in desired_keys
        )
        reactivate = tuple(
            (record, ActivationStatus.ACTIVE)
            for record in lineage
            if _activation_key(record) != target_key
            and (
                _activation_key(record) not in active_by_key
                or active_by_key[_activation_key(record)].status
                is not ActivationStatus.ACTIVE
            )
        )
        transitions = (
            *disable,
            *reactivate,
            (target, ActivationStatus.ACTIVE),
        )
        result = self._append_plan(
            request=request,
            events=events,
            prefix=prefix,
            tail=tail,
            transitions=transitions,
            qualification=None,
            now=now,
        )
        if result.active is None or not request.target.matches(result.active):
            raise PropertyCatalogActivationError(
                "rollback append did not select the requested qualified build"
            )
        return result

    def _events(self, target: ActivationTarget) -> tuple[ActivationRecord, ...]:
        return _canonical_control_events(
            self._store.list_activation_events(
                organization_id=target.organization_id,
                workspace_id=target.workspace_id,
                catalog_epoch=target.catalog_epoch,
            ),
            organization_id=target.organization_id,
            workspace_id=target.workspace_id,
            catalog_epoch=target.catalog_epoch,
        )

    def _append_plan(
        self,
        *,
        request: ActivationControlRequest,
        events: tuple[ActivationRecord, ...],
        prefix: tuple[ActivationRecord, ...],
        tail: tuple[ActivationRecord, ...],
        transitions: tuple[tuple[ActivationRecord, ActivationStatus], ...],
        qualification: RevisionQualification | None,
        now: datetime,
    ) -> ActivationControlResult:
        start_sequence = _next_activation_sequence(prefix)
        if _tail_matches_transitions(
            tail,
            transitions=transitions,
            start_sequence=start_sequence,
        ):
            return ActivationControlResult(
                records=tail,
                active=_current_active_from_events(events),
                qualification=qualification,
                idempotent=True,
            )
        if tail:
            raise ActivationRejected(("activation_control_stale",))

        records = tuple(
            _transition_record(
                source,
                status=status,
                activation_sequence=start_sequence + index,
                now=now,
            )
            for index, (source, status) in enumerate(transitions)
        )
        appended = tuple(
            self._store.append_activation_events(
                records,
                expected_head=request.expected_head,
            )
        )
        if appended != records:
            raise PropertyCatalogActivationError(
                "activation control store did not preserve the atomic append"
            )
        post_events = _canonical_control_events(
            (*events, *appended),
            organization_id=request.target.organization_id,
            workspace_id=request.target.workspace_id,
            catalog_epoch=request.target.catalog_epoch,
        )
        return ActivationControlResult(
            records=records,
            active=_current_active_from_events(post_events),
            qualification=qualification,
            idempotent=False,
        )


def current_active_activation(
    events: Sequence[ActivationRecord],
) -> ActivationRecord | None:
    """Resolve the build current readers should select from physical events."""

    return _current_active_from_events(_canonical_control_events(events))


def _qualify_control_activation(
    store: ActivationControlStore,
    *,
    manifest: ActivationManifest,
    fence: RevisionFence,
) -> RevisionQualification:
    store.audit_build_plan(
        build_plan=RevisionBuildPlan.from_json(fence.build_plan_json),
        manifest=manifest,
    )
    requirement = manifest.revision_requirement
    checkpoints = tuple(store.load_checkpoints(requirement))
    qualification = qualify_revision(requirement, checkpoints)
    if not qualification.qualified or qualification.activation_sha256 is None:
        raise ActivationRejected(qualification.issues)
    checkpoint_states = tuple(
        checkpoint.state_sha256
        for checkpoint in sorted(
            checkpoints,
            key=lambda value: (value.source_adapter, value.producer_stream_id),
        )
    )
    _validate_fence(
        fence,
        manifest=manifest,
        checkpoints=checkpoints,
        checkpoint_states=checkpoint_states,
    )
    return qualification


def _require_target_manifest(
    target: ActivationTarget,
    manifest: ActivationManifest,
) -> None:
    if (
        target.organization_id != manifest.organization_id
        or target.workspace_id != manifest.workspace_id
        or target.catalog_epoch != manifest.catalog_epoch
        or target.catalog_revision != manifest.catalog_revision
        or target.build_token != manifest.build_token
    ):
        raise ActivationRejected(("activation_target_mismatch",))


def _canonical_control_events(
    events: Sequence[ActivationRecord],
    *,
    organization_id: str | None = None,
    workspace_id: str | None = None,
    catalog_epoch: int | None = None,
) -> tuple[ActivationRecord, ...]:
    by_sequence: dict[int, ActivationRecord] = {}
    by_key_version: dict[tuple[int, str, int], ActivationRecord] = {}
    build_evidence: dict[tuple[int, str], tuple[Any, ...]] = {}
    qualified_tokens_by_revision: dict[int, set[str]] = {}
    latest_version_by_key: dict[tuple[int, str], int] = {}
    for record in events:
        if not isinstance(record, ActivationRecord):
            raise TypeError("activation control history must contain ActivationRecord")
        if organization_id is None:
            organization_id = record.organization_id
            workspace_id = record.workspace_id
            catalog_epoch = record.catalog_epoch
        if (
            record.organization_id != organization_id
            or record.workspace_id != workspace_id
            or record.catalog_epoch != catalog_epoch
        ):
            raise ActivationRejected(("activation_control_scope_conflict",))
        previous_sequence = by_sequence.get(record.activation_sequence)
        if previous_sequence is not None:
            if previous_sequence != record:
                raise ActivationRejected(("activation_sequence_conflict",))
            continue
        by_sequence[record.activation_sequence] = record
        key = _activation_key(record)
        key_version = (*key, record.version)
        previous_version = by_key_version.get(key_version)
        if previous_version is not None and previous_version != record:
            raise ActivationRejected(("activation_state_conflict",))
        by_key_version[key_version] = record
        evidence = _immutable_build_evidence(record)
        previous_evidence = build_evidence.setdefault(key, evidence)
        if previous_evidence != evidence:
            raise ActivationRejected(("activation_build_conflict",))
        if record.status is ActivationStatus.ACTIVE:
            qualified_tokens_by_revision.setdefault(record.catalog_revision, set()).add(
                record.build_token
            )

    ordered = tuple(by_sequence[sequence] for sequence in sorted(by_sequence))
    if any(
        record.activation_sequence != expected
        for expected, record in enumerate(ordered, start=1)
    ):
        raise ActivationRejected(("activation_sequence_gap",))
    for record in ordered:
        key = _activation_key(record)
        previous_version = latest_version_by_key.get(key)
        if previous_version is not None and record.version <= previous_version:
            raise ActivationRejected(("activation_version_not_monotonic",))
        latest_version_by_key[key] = record.version
    if any(len(tokens) != 1 for tokens in qualified_tokens_by_revision.values()):
        raise ActivationRejected(("activation_revision_conflict",))
    return ordered


def _control_action_window(
    events: tuple[ActivationRecord, ...],
    expected_head: ActivationHead | None,
) -> tuple[tuple[ActivationRecord, ...], tuple[ActivationRecord, ...]]:
    if expected_head is None:
        return (), events
    matching = tuple(
        index
        for index, event in enumerate(events)
        if event.activation_sequence == expected_head.activation_sequence
    )
    if len(matching) != 1 or not expected_head.matches(events[matching[0]]):
        raise ActivationRejected(("activation_control_precondition_mismatch",))
    index = matching[0]
    return events[: index + 1], events[index + 1 :]


def _activation_key(record: ActivationRecord) -> tuple[int, str]:
    return record.catalog_revision, record.build_token


def _activation_key_from_target(target: ActivationTarget) -> tuple[int, str]:
    return target.catalog_revision, target.build_token


def _immutable_build_evidence(record: ActivationRecord) -> tuple[Any, ...]:
    return (
        record.organization_id,
        record.workspace_id,
        record.catalog_epoch,
        record.catalog_revision,
        record.build_token,
        record.projection_version,
        record.lifecycle_mode,
        record.lineage_anchor_revision,
        record.source_manifest_json,
        record.source_manifest_sha256,
        record.revision_fence_sha256,
        record.activation_sha256,
        record.live_definition_rows,
        record.tombstone_rows,
        record.value_rows,
        record.qualified_at,
    )


def _transition_evidence(record: ActivationRecord) -> tuple[Any, ...]:
    return _immutable_build_evidence(record)[:-1]


def _latest_states(
    events: tuple[ActivationRecord, ...],
) -> dict[tuple[int, str], ActivationRecord]:
    states: dict[tuple[int, str], ActivationRecord] = {}
    for event in events:
        states[_activation_key(event)] = event
    return states


def _active_states(
    events: tuple[ActivationRecord, ...],
) -> tuple[ActivationRecord, ...]:
    return tuple(
        record
        for record in _latest_states(events).values()
        if record.status is ActivationStatus.ACTIVE
    )


def _current_active_from_events(
    events: tuple[ActivationRecord, ...],
) -> ActivationRecord | None:
    active = _active_states(events)
    if not active:
        return None
    builds_by_revision: dict[int, set[str]] = {}
    for record in active:
        builds_by_revision.setdefault(record.catalog_revision, set()).add(
            record.build_token
        )
    if any(len(tokens) != 1 for tokens in builds_by_revision.values()):
        raise ActivationRejected(("activation_revision_conflict",))
    return max(
        active,
        key=lambda record: (
            record.catalog_revision,
            record.activation_sequence,
            record.build_token,
        ),
    )


def _qualified_builds(
    events: tuple[ActivationRecord, ...],
) -> dict[tuple[int, str], ActivationRecord]:
    qualified: dict[tuple[int, str], ActivationRecord] = {}
    for event in events:
        if event.status is ActivationStatus.ACTIVE:
            qualified[_activation_key(event)] = event
    return qualified


def _qualified_lineage(
    events: tuple[ActivationRecord, ...],
    target: ActivationRecord,
) -> tuple[ActivationRecord, ...]:
    qualified = tuple(_qualified_builds(events).values())
    by_revision = {
        record.catalog_revision: record
        for record in qualified
        if record.projection_version == target.projection_version
        and record.lineage_anchor_revision == target.lineage_anchor_revision
        and target.lineage_anchor_revision
        <= record.catalog_revision
        <= target.catalog_revision
    }
    anchor = by_revision.get(target.lineage_anchor_revision)
    if (
        anchor is None
        or anchor.lifecycle_mode
        not in {
            CatalogLifecycleMode.INITIAL_BACKFILL,
            CatalogLifecycleMode.FULL_REPAIR,
        }
        or _activation_key(target)
        != _activation_key(by_revision.get(target.catalog_revision, anchor))
    ):
        raise ActivationRejected(("rollback_lineage_incomplete",))
    return tuple(by_revision[revision] for revision in sorted(by_revision))


def _next_activation_sequence(events: tuple[ActivationRecord, ...]) -> int:
    return 1 if not events else events[-1].activation_sequence + 1


def _transition_record(
    source: ActivationRecord,
    *,
    status: ActivationStatus,
    activation_sequence: int,
    now: datetime,
) -> ActivationRecord:
    if now < source.qualified_at or now < source.updated_at:
        raise ActivationRejected(("activation_control_time_not_monotonic",))
    return replace(
        source,
        activation_sequence=activation_sequence,
        status=status,
        updated_at=now,
        version=activation_sequence,
    )


def _tail_matches_transitions(
    tail: tuple[ActivationRecord, ...],
    *,
    transitions: tuple[tuple[ActivationRecord, ActivationStatus], ...],
    start_sequence: int,
) -> bool:
    if len(tail) != len(transitions):
        return False
    return all(
        event.activation_sequence == start_sequence + index
        and event.version == start_sequence + index
        and event.status is status
        and _transition_evidence(event) == _transition_evidence(source)
        for index, (event, (source, status)) in enumerate(
            zip(tail, transitions, strict=True)
        )
    )


def make_revision_fence(
    *,
    manifest: ActivationManifest,
    build_plan: RevisionBuildPlan,
    checkpoints: Sequence[CatalogCheckpoint],
    drain_deadline: datetime,
    fenced_at: datetime,
) -> RevisionFence:
    ordered_checkpoints = tuple(
        sorted(
            checkpoints,
            key=lambda value: (value.source_adapter, value.producer_stream_id),
        )
    )
    checkpoint_states = tuple(
        sorted(checkpoint.state_sha256 for checkpoint in ordered_checkpoints)
    )
    stream_proofs = tuple(
        StreamDrainProof(
            source_adapter=checkpoint.source_adapter,
            producer_stream_id=checkpoint.producer_stream_id,
            last_issued_sequence=checkpoint.last_issued_sequence,
            fenced_sequence=checkpoint.fenced_sequence,
            terminal_sequence=checkpoint.last_sequence or 0,
            terminal_payload_sha256=checkpoint.terminal_payload_sha256,
        )
        for checkpoint in ordered_checkpoints
    )
    fence_sha256 = _revision_fence_sha256(
        manifest=manifest,
        build_plan=build_plan,
        stream_proofs=stream_proofs,
        checkpoint_states=checkpoint_states,
        drain_deadline=drain_deadline,
    )
    return RevisionFence(
        organization_id=manifest.organization_id,
        workspace_id=manifest.workspace_id,
        catalog_epoch=manifest.catalog_epoch,
        catalog_revision=manifest.catalog_revision,
        build_token=manifest.build_token,
        projection_version=manifest.projection_version,
        build_plan_json=build_plan.canonical_json,
        build_lease_sha256=build_plan.sha256,
        manifest_sha256=manifest.sha256,
        status=RevisionFenceStatus.FENCED,
        stream_proofs=stream_proofs,
        checkpoint_state_sha256s=checkpoint_states,
        drain_deadline=drain_deadline,
        fenced_at=fenced_at,
        fence_sha256=fence_sha256,
    )


def _validate_fence(
    fence: RevisionFence,
    *,
    manifest: ActivationManifest,
    checkpoints: tuple[CatalogCheckpoint, ...],
    checkpoint_states: tuple[str, ...],
) -> None:
    checkpoint_proofs = {
        checkpoint.key: (
            checkpoint.last_issued_sequence,
            checkpoint.fenced_sequence,
            checkpoint.last_sequence,
            checkpoint.terminal_payload_sha256,
        )
        for checkpoint in checkpoints
    }
    fence_proofs = {
        proof.key: (
            proof.last_issued_sequence,
            proof.fenced_sequence,
            proof.terminal_sequence,
            proof.terminal_payload_sha256,
        )
        for proof in fence.stream_proofs
    }
    if (
        fence.organization_id != manifest.organization_id
        or fence.workspace_id != manifest.workspace_id
        or fence.catalog_epoch != manifest.catalog_epoch
        or fence.catalog_revision != manifest.catalog_revision
        or fence.build_token != manifest.build_token
        or fence.projection_version != manifest.projection_version
        or not RevisionBuildPlan.from_json(fence.build_plan_json).matches_manifest(
            manifest
        )
        or fence.manifest_sha256 != manifest.sha256
        or fence.status is not RevisionFenceStatus.FENCED
        or fence_proofs != checkpoint_proofs
        or tuple(sorted(fence.checkpoint_state_sha256s))
        != tuple(sorted(checkpoint_states))
    ):
        raise ActivationRejected(("revision_fence_mismatch",))


def _revision_fence_sha256(
    *,
    manifest: ActivationManifest,
    build_plan: RevisionBuildPlan,
    stream_proofs: tuple[StreamDrainProof, ...],
    checkpoint_states: tuple[str, ...],
    drain_deadline: datetime,
) -> str:
    return framed_sha256(
        "futureagi.property-catalog.revision-fence.v3",
        manifest.organization_id,
        manifest.workspace_id,
        manifest.catalog_epoch,
        manifest.catalog_revision,
        manifest.build_token,
        manifest.projection_version,
        build_plan.sha256,
        manifest.sha256,
        RevisionFenceStatus.FENCED,
        drain_deadline.isoformat(timespec="microseconds"),
        *(
            component
            for proof in stream_proofs
            for component in (
                proof.source_adapter,
                proof.producer_stream_id,
                proof.last_issued_sequence,
                proof.fenced_sequence,
                proof.terminal_sequence,
                proof.terminal_payload_sha256,
            )
        ),
        *checkpoint_states,
    )


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _validate_role_inventory(
    inventory: tuple[tuple[SourceAdapter, ManifestStreamRole], ...], *, label: str
) -> None:
    if {adapter for adapter, _role in inventory} != EXPECTED_SOURCE_ADAPTERS:
        raise ValueError(f"{label} must contain all seven adapters")
    span_roles = tuple(
        role for adapter, role in inventory if adapter is SourceAdapter.SPAN_ATTRIBUTE
    )
    required = (
        ManifestStreamRole.DEFINITIONS,
        ManifestStreamRole.VALUES,
        ManifestStreamRole.HOT_VALUES,
        ManifestStreamRole.SOURCE_AUDIT,
    )
    if len(span_roles) != len(required) or any(
        span_roles.count(role) != 1 for role in required
    ):
        raise ValueError(f"{label} has an invalid span stream role inventory")
    for adapter in EXPECTED_SOURCE_ADAPTERS - {SourceAdapter.SPAN_ATTRIBUTE}:
        roles = tuple(role for source, role in inventory if source is adapter)
        if roles != (ManifestStreamRole.DEFINITIONS,):
            raise ValueError(f"{label} requires one definitions stream for {adapter}")


def _validate_role_counts(
    role: ManifestStreamRole, requirement: StreamRequirement
) -> None:
    if role is ManifestStreamRole.DEFINITIONS:
        valid = requirement.expected_value_count == 0
    elif role in {ManifestStreamRole.VALUES, ManifestStreamRole.HOT_VALUES}:
        valid = (
            requirement.expected_definition_count == 0
            and requirement.expected_tombstone_count == 0
        )
    else:
        valid = (
            role is ManifestStreamRole.SOURCE_AUDIT
            and requirement.expected_definition_count == 0
            and requirement.expected_value_count == 0
            and requirement.expected_tombstone_count == 0
        )
    if not valid:
        raise ValueError("manifest stream counts violate the declared stream role")


__all__ = [
    "ActivationControlRequest",
    "ActivationControlResult",
    "ActivationControlStore",
    "ActivationHead",
    "ActivationInventory",
    "ActivationManifest",
    "ActivationRecord",
    "ActivationRejected",
    "ActivationResult",
    "ActivationStatus",
    "ActivationStore",
    "ActivationTarget",
    "BuildPlanSourceScope",
    "BuildPlanStream",
    "CatalogLifecycleMode",
    "EXPECTED_SOURCE_ADAPTERS",
    "ManifestStream",
    "ManifestStreamRole",
    "PropertyCatalogActivationError",
    "PropertyCatalogActivationControlPlane",
    "PropertyCatalogActivator",
    "RevisionCoordinator",
    "RevisionBuildPlan",
    "RevisionFence",
    "RevisionFenceStatus",
    "RevisionLease",
    "StreamDrainProof",
    "current_active_activation",
    "make_revision_fence",
]
