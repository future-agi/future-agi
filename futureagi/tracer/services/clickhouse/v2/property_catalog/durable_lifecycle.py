"""Durable, restart-safe workspace lifecycle for the unified property catalog.

This module deliberately owns no staged process-local revision state.  It
prepares a revision exclusively from append-only control-plane evidence, makes
the complete source window recoverable from the immutable build plan, and
requires final lifecycle evidence to be loaded back from durable storage.

The build plan has ten streams and only one unsigned cutoff per stream.  The
cutoffs are therefore assigned as a compact, versioned lifecycle document:

* system definitions: epoch plus prior active revision (or the epoch no-prior marker),
* five relational definition streams: the frozen PostgreSQL upper cutoff,
* span definitions: the frozen span lower bound,
* span hot values: the frozen span upper bound,
* span values and source audit: the same ClickHouse audit generation.

The label on every cutoff includes the lifecycle mode.  A restarted worker can
thus recover the exact build token, mode, source bounds, audit generation, and
prior-active lineage from ``property_catalog_source_streams.build_plan_json``;
it never asks the wall clock or source database to recreate an open build.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from .activation import (
    BuildPlanSourceScope,
    BuildPlanStream,
    CatalogLifecycleMode,
    ManifestStreamRole,
    RevisionBuildPlan,
    RevisionCoordinator,
    RevisionLease,
    StreamDrainProof,
)
from .codec import (
    ZERO_UUID,
    canonical_json,
    canonical_json_sha256,
    canonical_uuid,
    require_sha256,
)
from .models import SourceAdapter
from .publisher import SharedCatalogDeadline, require_catalog_database
from .qualification import CheckpointStatus
from .reconciler import CheckpointWrite, ReconcileMode
from .runtime_limits import RUNTIME_LIMITS
from .source_adapters import SourceKeysetCursor

_SOURCE_STREAM_TABLE = "property_catalog_source_streams"
_ACTIVATION_TABLE = "property_catalog_activations"
_STREAM_NAMESPACE = uuid.UUID("52d4b43e-f3a1-5e2c-84c5-8c99adf4be57")
_MAX_SOURCE_WINDOW = timedelta(days=366)
_RELATIONAL_ADAPTERS = frozenset(
    {
        SourceAdapter.EVAL_TEMPLATE,
        SourceAdapter.EVAL_CONFIG,
        SourceAdapter.SIMULATION_EVAL_CONFIG,
        SourceAdapter.ANNOTATION_LABEL,
        SourceAdapter.DATASET_COLUMN,
    }
)
_EXPECTED_ROLE_INVENTORY = frozenset(
    {
        (SourceAdapter.SYSTEM_MANIFEST, ManifestStreamRole.DEFINITIONS),
        (SourceAdapter.EVAL_TEMPLATE, ManifestStreamRole.DEFINITIONS),
        (SourceAdapter.EVAL_CONFIG, ManifestStreamRole.DEFINITIONS),
        (
            SourceAdapter.SIMULATION_EVAL_CONFIG,
            ManifestStreamRole.DEFINITIONS,
        ),
        (SourceAdapter.ANNOTATION_LABEL, ManifestStreamRole.DEFINITIONS),
        (SourceAdapter.DATASET_COLUMN, ManifestStreamRole.DEFINITIONS),
        (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.DEFINITIONS),
        (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.VALUES),
        (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.HOT_VALUES),
        (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.SOURCE_AUDIT),
    }
)
_RESERVATION_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "producer_stream_id",
    "envelope_version",
    "build_plan_json",
    "build_lease_sha256",
    "status",
    "started_at",
    "drain_deadline",
    "fenced_at",
    "_version",
)
_RESERVATION_LOGICAL_COLUMNS = tuple(
    value for value in _RESERVATION_COLUMNS if value != "_version"
)
_ACTIVATION_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "catalog_revision",
    "build_token",
    "projection_version",
    "lifecycle_mode",
    "lineage_anchor_revision",
    "activation_sequence",
    "source_manifest_json",
    "source_manifest_sha256",
    "revision_fence_sha256",
    "activation_sha256",
    "status",
    "live_definition_rows",
    "tombstone_rows",
    "value_rows",
    "qualified_at",
    "updated_at",
    "_version",
)
_ACTIVATION_LOGICAL_COLUMNS = tuple(
    value for value in _ACTIVATION_COLUMNS if value != "_version"
)
MAX_ACTIVE_REVISIONS_SINCE_ANCHOR = RUNTIME_LIMITS.max_lineage_revisions
MAX_LINEAGE_ANCHOR_AGE_SECONDS = RUNTIME_LIMITS.lineage_anchor_max_age_seconds
FULL_REPAIR_INTERVAL_SECONDS = RUNTIME_LIMITS.full_repair_interval_seconds
_MAX_NONTERMINAL_RESERVATION_ROWS = RUNTIME_LIMITS.max_nonterminal_reservations


class DurableLifecycleError(RuntimeError):
    """Persisted lifecycle evidence is missing, ambiguous, or contradictory."""


class LifecycleRunMode(StrEnum):
    AUTO = "auto"
    INITIAL_BACKFILL = "initial_backfill"
    INCREMENTAL = "incremental"
    FULL_REPAIR = "full_repair"

    @property
    def reconcile_mode(self) -> ReconcileMode:
        if self is LifecycleRunMode.AUTO:
            raise DurableLifecycleError(
                "auto lifecycle mode must be resolved before reconciliation"
            )
        if self is LifecycleRunMode.INCREMENTAL:
            return ReconcileMode.INCREMENTAL
        return ReconcileMode.FULL_REPAIR


class ReservationStatus(StrEnum):
    OPEN = "open"
    DRAINING = "draining"
    FENCED = "fenced"


@dataclass(frozen=True, slots=True)
class WorkspaceCatalogScope:
    organization_id: str
    workspace_id: str
    catalog_epoch: int
    projection_version: int
    project_ids: tuple[str, ...]

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
        _positive_uint(self.catalog_epoch, 16, "catalog_epoch")
        _positive_uint(self.projection_version, 16, "projection_version")
        if not isinstance(self.project_ids, tuple):
            raise TypeError("project_ids must be a tuple")
        projects = tuple(
            sorted(
                {
                    canonical_uuid(project_id, field="project_id")
                    for project_id in self.project_ids
                }
            )
        )
        if not 1 <= len(projects) <= 256 or len(projects) != len(self.project_ids):
            raise ValueError("project_ids must contain 1..256 unique canonical UUIDs")
        object.__setattr__(self, "project_ids", projects)


@dataclass(frozen=True, slots=True)
class SourceWindow:
    since: datetime
    until: datetime

    def __post_init__(self) -> None:
        _require_utc(self.since, "since")
        _require_utc(self.until, "until")
        if self.since >= self.until:
            raise ValueError("source window since must precede until")
        if self.until - self.since > _MAX_SOURCE_WINDOW:
            raise ValueError("source window cannot exceed 366 days")


@dataclass(frozen=True, slots=True)
class ConfiguredSourceBounds:
    """Explicit origin and initial frozen upper bound.

    The management-command initial backfill uses both values.  Scheduled full
    repairs use ``origin`` as their earliest retained bound and derive a fresh,
    rolling upper bound. Incremental runs derive their lower bound from the
    prior active build plan.
    """

    origin: datetime
    initial_until: datetime

    def __post_init__(self) -> None:
        SourceWindow(self.origin, self.initial_until)

    @property
    def initial_window(self) -> SourceWindow:
        return SourceWindow(self.origin, self.initial_until)


@dataclass(frozen=True, slots=True)
class FrozenLifecycleCutoffs:
    """One source snapshot frozen before reservation and persisted in its plan."""

    snapshot_upper: datetime
    span_window: SourceWindow
    span_audit_generation: int

    def __post_init__(self) -> None:
        _require_utc(self.snapshot_upper, "snapshot_upper")
        if not isinstance(self.span_window, SourceWindow):
            raise TypeError("span_window must be a SourceWindow")
        if self.snapshot_upper != self.span_window.until:
            raise ValueError("snapshot upper must equal the frozen span upper bound")
        _positive_uint(
            self.span_audit_generation,
            64,
            "span_audit_generation",
        )


@dataclass(frozen=True, slots=True)
class PersistedReservation:
    lease: RevisionLease
    status: ReservationStatus

    def __post_init__(self) -> None:
        if not isinstance(self.lease, RevisionLease):
            raise TypeError("lease must be a RevisionLease")
        if not isinstance(self.status, ReservationStatus):
            raise TypeError("status must be a ReservationStatus")


@dataclass(frozen=True, slots=True)
class ActiveStreamEvidence:
    source_adapter: SourceAdapter
    role: ManifestStreamRole
    producer_stream_id: str
    source_version_fence: int
    watermark: str
    checkpoint_state_sha256: str

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
        _positive_uint(self.source_version_fence, 64, "source_version_fence")
        if not isinstance(self.watermark, str):
            raise TypeError("watermark must be text")
        require_sha256(
            self.checkpoint_state_sha256,
            field="checkpoint_state_sha256",
        )

    @property
    def role_key(self) -> tuple[SourceAdapter, ManifestStreamRole]:
        return self.source_adapter, self.role


@dataclass(frozen=True, slots=True)
class LineageAnchorEvidence:
    catalog_revision: int
    build_token: str
    mode: LifecycleRunMode
    qualified_at: datetime
    activation_sequence: int
    activation_sha256: str
    active_revisions_since: int

    def __post_init__(self) -> None:
        _positive_uint(self.catalog_revision, 64, "anchor catalog_revision")
        object.__setattr__(
            self,
            "build_token",
            canonical_uuid(self.build_token, field="anchor build_token"),
        )
        if self.mode not in {
            LifecycleRunMode.INITIAL_BACKFILL,
            LifecycleRunMode.FULL_REPAIR,
        }:
            raise ValueError("lineage anchor must be an initial or full-repair build")
        _require_utc(self.qualified_at, "anchor qualified_at")
        _positive_uint(self.activation_sequence, 64, "anchor activation_sequence")
        require_sha256(
            self.activation_sha256,
            field="anchor activation_sha256",
        )
        if type(self.active_revisions_since) is not int or not (
            0 <= self.active_revisions_since <= MAX_ACTIVE_REVISIONS_SINCE_ANCHOR
        ):
            raise ValueError("lineage anchor depth exceeds its safe bound")


@dataclass(frozen=True, slots=True)
class PriorActiveEvidence:
    catalog_revision: int
    build_token: str
    projection_version: int
    lifecycle_mode: CatalogLifecycleMode
    activation_sequence: int
    activation_sha256: str
    source_manifest_sha256: str
    build_plan: RevisionBuildPlan
    streams: tuple[ActiveStreamEvidence, ...]
    qualified_at: datetime
    lineage_anchor: LineageAnchorEvidence

    def __post_init__(self) -> None:
        _positive_uint(self.catalog_revision, 64, "catalog_revision")
        _positive_uint(self.projection_version, 16, "projection_version")
        _positive_uint(self.activation_sequence, 64, "activation_sequence")
        if not isinstance(self.lifecycle_mode, CatalogLifecycleMode):
            raise TypeError("lifecycle_mode must be a CatalogLifecycleMode")
        object.__setattr__(
            self,
            "build_token",
            canonical_uuid(self.build_token, field="build_token"),
        )
        require_sha256(self.activation_sha256, field="activation_sha256")
        require_sha256(
            self.source_manifest_sha256,
            field="source_manifest_sha256",
        )
        _require_utc(self.qualified_at, "qualified_at")
        if not isinstance(self.lineage_anchor, LineageAnchorEvidence):
            raise TypeError("lineage_anchor must be LineageAnchorEvidence")
        if self.lineage_anchor.catalog_revision > self.catalog_revision:
            raise ValueError("lineage anchor is newer than active revision")
        if (
            self.activation_sequence - self.lineage_anchor.activation_sequence
            != self.lineage_anchor.active_revisions_since
        ):
            raise ValueError("lineage anchor depth disagrees with activation sequence")
        if self.lineage_anchor.active_revisions_since == 0 and (
            self.lineage_anchor.catalog_revision != self.catalog_revision
            or self.lineage_anchor.build_token != self.build_token
        ):
            raise ValueError("zero-depth lineage anchor is not the active revision")
        if not isinstance(self.build_plan, RevisionBuildPlan):
            raise TypeError("build_plan must be a RevisionBuildPlan")
        if CatalogLifecycleMode(_decode_plan_scope(self.build_plan).mode.value) is not (
            self.lifecycle_mode
        ):
            raise ValueError("active lifecycle mode differs from its build plan")
        if not isinstance(self.streams, tuple) or any(
            not isinstance(value, ActiveStreamEvidence) for value in self.streams
        ):
            raise TypeError("streams must be ActiveStreamEvidence tuples")
        if self.catalog_revision != self.build_plan.catalog_revision or (
            self.build_token != self.build_plan.build_token
            or self.projection_version != self.build_plan.projection_version
        ):
            raise ValueError("active evidence does not match its build plan")
        _require_exact_role_inventory(
            tuple(value.role_key for value in self.streams),
            label="active checkpoints",
        )
        if len({value.role_key for value in self.streams}) != len(self.streams):
            raise ValueError("active evidence contains duplicate roles")
        planned = {
            (value.source_adapter, value.role): (
                value.producer_stream_id,
                value.source_version_fence,
            )
            for value in self.build_plan.streams
        }
        observed = {
            value.role_key: (value.producer_stream_id, value.source_version_fence)
            for value in self.streams
        }
        if observed != planned:
            raise ValueError("active checkpoints do not match the active build plan")

    def stream(
        self,
        source_adapter: SourceAdapter,
        role: ManifestStreamRole,
    ) -> ActiveStreamEvidence:
        values = tuple(
            value for value in self.streams if value.role_key == (source_adapter, role)
        )
        if len(values) != 1:
            raise DurableLifecycleError("active stream evidence is not unique")
        return values[0]


@dataclass(frozen=True, slots=True)
class StreamStart:
    source_adapter: SourceAdapter
    role: ManifestStreamRole
    producer_stream_id: str
    lower_watermark: str
    resume: CheckpointWrite | None

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
        if not isinstance(self.lower_watermark, str):
            raise TypeError("lower_watermark must be text")
        if self.resume is not None and not isinstance(self.resume, CheckpointWrite):
            raise TypeError("resume must be a CheckpointWrite")

    @property
    def role_key(self) -> tuple[SourceAdapter, ManifestStreamRole]:
        return self.source_adapter, self.role


@dataclass(frozen=True, slots=True)
class PreparedLifecycleRevision:
    scope: WorkspaceCatalogScope
    mode: LifecycleRunMode
    lease: RevisionLease
    cutoffs: FrozenLifecycleCutoffs
    prior_active: PriorActiveEvidence | None
    streams: tuple[StreamStart, ...]
    reservation_status: ReservationStatus
    resumed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.scope, WorkspaceCatalogScope):
            raise TypeError("scope must be a WorkspaceCatalogScope")
        if not isinstance(self.mode, LifecycleRunMode):
            raise TypeError("mode must be a LifecycleRunMode")
        if self.mode is LifecycleRunMode.AUTO:
            raise ValueError("prepared lifecycle mode must be concrete")
        if not isinstance(self.lease, RevisionLease):
            raise TypeError("lease must be a RevisionLease")
        if not isinstance(self.cutoffs, FrozenLifecycleCutoffs):
            raise TypeError("cutoffs must be FrozenLifecycleCutoffs")
        if self.prior_active is not None and not isinstance(
            self.prior_active, PriorActiveEvidence
        ):
            raise TypeError("prior_active must be PriorActiveEvidence")
        if not isinstance(self.reservation_status, ReservationStatus):
            raise TypeError("reservation_status must be a ReservationStatus")
        if type(self.resumed) is not bool:
            raise TypeError("resumed must be a bool")
        if not self.resumed and self.reservation_status is not ReservationStatus.OPEN:
            raise ValueError("a new reservation must be open")
        if not isinstance(self.streams, tuple) or any(
            not isinstance(value, StreamStart) for value in self.streams
        ):
            raise TypeError("streams must be StreamStart tuples")
        if (
            self.lease.organization_id != self.scope.organization_id
            or self.lease.workspace_id != self.scope.workspace_id
            or self.lease.catalog_epoch != self.scope.catalog_epoch
            or self.lease.projection_version != self.scope.projection_version
        ):
            raise ValueError("prepared lease does not match workspace scope")
        source_scope = self.lease.build_plan.source_scope
        if source_scope.project_ids != self.scope.project_ids or (
            source_scope.span_since_us
            != _datetime_to_micros(self.cutoffs.span_window.since)
            or source_scope.span_until_us
            != _datetime_to_micros(self.cutoffs.span_window.until)
        ):
            raise ValueError(
                "prepared source project/window scope differs from the build lease"
            )
        _require_exact_role_inventory(
            tuple(value.role_key for value in self.streams),
            label="prepared streams",
        )
        planned = {
            (value.source_adapter, value.role): value.producer_stream_id
            for value in self.lease.build_plan.streams
        }
        starts = {value.role_key: value.producer_stream_id for value in self.streams}
        if starts != planned:
            raise ValueError("prepared streams do not match the persisted build plan")

    @property
    def reconcile_mode(self) -> ReconcileMode:
        return self.mode.reconcile_mode

    @property
    def build_plan(self) -> RevisionBuildPlan:
        return self.lease.build_plan

    @property
    def lifecycle_mode(self) -> CatalogLifecycleMode:
        """Exact mode that must be copied into the activation row."""

        return CatalogLifecycleMode(self.mode.value)

    @property
    def lineage_anchor_revision(self) -> int:
        """Self for snapshot builds; inherited for incremental revisions."""

        if self.mode in {
            LifecycleRunMode.INITIAL_BACKFILL,
            LifecycleRunMode.FULL_REPAIR,
        }:
            return self.lease.catalog_revision
        if self.prior_active is None:
            raise DurableLifecycleError(
                "incremental revision has no persisted lineage anchor"
            )
        return self.prior_active.lineage_anchor.catalog_revision

    def stream(
        self,
        source_adapter: SourceAdapter,
        role: ManifestStreamRole,
    ) -> StreamStart:
        values = tuple(
            value for value in self.streams if value.role_key == (source_adapter, role)
        )
        if len(values) != 1:
            raise DurableLifecycleError("prepared stream is not unique")
        return values[0]


@dataclass(frozen=True, slots=True)
class PersistedCheckpointEvidence:
    source_adapter: SourceAdapter
    producer_stream_id: str
    state_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_adapter, SourceAdapter):
            raise TypeError("source_adapter must be a SourceAdapter")
        object.__setattr__(
            self,
            "producer_stream_id",
            canonical_uuid(self.producer_stream_id, field="producer_stream_id"),
        )
        require_sha256(self.state_sha256, field="checkpoint state_sha256")

    @property
    def key(self) -> tuple[SourceAdapter, str]:
        return self.source_adapter, self.producer_stream_id


@dataclass(frozen=True, slots=True)
class LifecycleCompletionEvidence:
    """Evidence reloaded after execution from the six durable catalog tables."""

    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    lifecycle_mode: CatalogLifecycleMode
    lineage_anchor_revision: int
    opened_streams: tuple[tuple[SourceAdapter, str], ...]
    building_assignment_sha256: str
    stream_drain_proofs: tuple[StreamDrainProof, ...]
    hot_drain_proof_sha256: str
    checkpoints: tuple[PersistedCheckpointEvidence, ...]
    manifest_sha256: str
    fence_sha256: str
    qualification_sha256: str
    activation_sha256: str
    absence_tombstone_pass_completed: bool

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
        _positive_uint(self.catalog_epoch, 16, "catalog_epoch")
        _positive_uint(self.catalog_revision, 64, "catalog_revision")
        _positive_uint(self.projection_version, 16, "projection_version")
        if not isinstance(self.lifecycle_mode, CatalogLifecycleMode):
            raise TypeError("lifecycle_mode must be a CatalogLifecycleMode")
        _positive_uint(
            self.lineage_anchor_revision,
            64,
            "lineage_anchor_revision",
        )
        for field_name in (
            "building_assignment_sha256",
            "hot_drain_proof_sha256",
            "manifest_sha256",
            "fence_sha256",
            "qualification_sha256",
            "activation_sha256",
        ):
            require_sha256(getattr(self, field_name), field=field_name)
        if not isinstance(self.opened_streams, tuple):
            raise TypeError("opened_streams must be a tuple")
        normalized = tuple(
            (
                adapter,
                canonical_uuid(stream_id, field="producer_stream_id"),
            )
            for adapter, stream_id in self.opened_streams
        )
        if any(not isinstance(adapter, SourceAdapter) for adapter, _ in normalized):
            raise TypeError("opened stream adapters must be SourceAdapter values")
        object.__setattr__(self, "opened_streams", normalized)
        if not isinstance(self.stream_drain_proofs, tuple) or any(
            not isinstance(value, StreamDrainProof)
            for value in self.stream_drain_proofs
        ):
            raise TypeError("stream_drain_proofs must be StreamDrainProof tuples")
        if not isinstance(self.checkpoints, tuple) or any(
            not isinstance(value, PersistedCheckpointEvidence)
            for value in self.checkpoints
        ):
            raise TypeError("checkpoints must be PersistedCheckpointEvidence tuples")
        if type(self.absence_tombstone_pass_completed) is not bool:
            raise TypeError("absence_tombstone_pass_completed must be a bool")

    def validate_for(self, prepared: PreparedLifecycleRevision) -> None:
        lease = prepared.lease
        if (
            self.organization_id != lease.organization_id
            or self.workspace_id != lease.workspace_id
            or self.catalog_epoch != lease.catalog_epoch
            or self.catalog_revision != lease.catalog_revision
            or self.build_token != lease.build_token
            or self.projection_version != lease.projection_version
            or self.lifecycle_mode is not prepared.lifecycle_mode
            or self.lineage_anchor_revision != prepared.lineage_anchor_revision
        ):
            raise DurableLifecycleError(
                "completion evidence does not match the prepared revision"
            )
        expected = {value.key for value in lease.build_plan.streams}
        opened = set(self.opened_streams)
        drained = {value.key for value in self.stream_drain_proofs}
        checkpoints = {value.key for value in self.checkpoints}
        if (
            len(self.opened_streams) != 10
            or len(self.stream_drain_proofs) != 10
            or len(self.checkpoints) != 10
            or opened != expected
            or drained != expected
            or checkpoints != expected
        ):
            raise DurableLifecycleError(
                "completion lacks the exact ten opened, drained, checkpointed streams"
            )
        hot = tuple(
            value
            for value in self.stream_drain_proofs
            if value.source_adapter is SourceAdapter.SPAN_ATTRIBUTE
            and _role_for_stream(lease.build_plan, value.key)
            is ManifestStreamRole.HOT_VALUES
        )
        if len(hot) != 1:
            raise DurableLifecycleError("completion has no unique hot drain proof")
        if (
            prepared.mode is LifecycleRunMode.FULL_REPAIR
            and not self.absence_tombstone_pass_completed
        ):
            raise DurableLifecycleError(
                "full repair did not persist its absence-tombstone pass"
            )


class LifecycleCheckpointStore(Protocol):
    def load_checkpoint_write(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        catalog_revision: int,
        build_token: str,
        source_adapter: SourceAdapter,
        producer_stream_id: str,
    ) -> CheckpointWrite | None: ...


class LifecycleStateReader(Protocol):
    def load_nonterminal(
        self,
        scope: WorkspaceCatalogScope,
    ) -> PersistedReservation | None: ...

    def load_latest_active(
        self,
        scope: WorkspaceCatalogScope,
    ) -> PriorActiveEvidence | None: ...

    def load_resumes(self, lease: RevisionLease) -> Sequence[CheckpointWrite]: ...


class LifecycleCutoffFreezer(Protocol):
    def __call__(
        self,
        *,
        scope: WorkspaceCatalogScope,
        mode: LifecycleRunMode,
        span_since: datetime,
        configured_until: datetime | None,
        prior_active: PriorActiveEvidence | None,
    ) -> FrozenLifecycleCutoffs: ...


class FreshSpanLifecycleCutoffFreezer:
    """Freeze one fresh upper cutoff and one canonical-span audit generation.

    Initial backfill honors its explicitly configured frozen upper bound.
    Every scheduled mode ignores static configuration and samples ``now`` once.
    The returned source object is checked byte-for-byte before its scope is
    admitted to the durable build plan.
    """

    def __init__(
        self,
        span_reader: Any,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not callable(getattr(span_reader, "freeze", None)):
            raise TypeError("span_reader must implement freeze")
        if not callable(now):
            raise TypeError("cutoff clock must be callable")
        self._span_reader = span_reader
        self._now = now

    def __call__(
        self,
        *,
        scope: WorkspaceCatalogScope,
        mode: LifecycleRunMode,
        span_since: datetime,
        configured_until: datetime | None,
        prior_active: PriorActiveEvidence | None,
    ) -> FrozenLifecycleCutoffs:
        _ = prior_active
        if mode is LifecycleRunMode.INITIAL_BACKFILL:
            if configured_until is None:
                raise DurableLifecycleError(
                    "initial backfill requires its configured frozen upper cutoff"
                )
            until = configured_until
        else:
            if configured_until is not None:
                raise DurableLifecycleError(
                    "scheduled revision cannot use a configured static upper cutoff"
                )
            until = self._now()
            if mode is LifecycleRunMode.FULL_REPAIR:
                span_since = max(span_since, until - _MAX_SOURCE_WINDOW)
        window = SourceWindow(span_since, until)
        frozen = self._span_reader.freeze(
            project_ids=scope.project_ids,
            since=window.since,
            until=window.until,
        )
        try:
            frozen_projects = tuple(frozen.project_ids)
            frozen_since = frozen.since
            frozen_until = frozen.until
            audit_generation = frozen.audit_generation
        except AttributeError as exc:
            raise DurableLifecycleError(
                "canonical span freezer returned incomplete evidence"
            ) from exc
        if (
            frozen_projects != scope.project_ids
            or frozen_since != window.since
            or frozen_until != window.until
        ):
            raise DurableLifecycleError(
                "canonical span freezer changed projects or half-open window"
            )
        return FrozenLifecycleCutoffs(
            snapshot_upper=until,
            span_window=window,
            span_audit_generation=audit_generation,
        )


class LifecycleExecutor(Protocol):
    def execute(self, prepared: PreparedLifecycleRevision) -> None: ...


class LifecycleCompletionReader(Protocol):
    def load_completion(
        self,
        prepared: PreparedLifecycleRevision,
    ) -> LifecycleCompletionEvidence | None: ...


@dataclass(frozen=True, slots=True)
class LifecycleRunResult:
    prepared: PreparedLifecycleRevision
    completion: LifecycleCompletionEvidence


class DurableWorkspaceCatalogLifecycle:
    """Stateless prepare/run coordinator over durable revision evidence."""

    def __init__(
        self,
        *,
        state_reader: LifecycleStateReader,
        coordinator: RevisionCoordinator,
        cutoff_freezer: LifecycleCutoffFreezer,
        hot_producer_stream_id: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_build_token: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        for value, label in (
            (state_reader, "state_reader"),
            (coordinator, "coordinator"),
        ):
            if value is None:
                raise TypeError(f"{label} is required")
        if not callable(cutoff_freezer):
            raise TypeError("cutoff_freezer must be callable")
        if not callable(now) or not callable(new_build_token):
            raise TypeError("lifecycle clocks and token sources must be callable")
        self._state_reader = state_reader
        self._coordinator = coordinator
        self._cutoff_freezer = cutoff_freezer
        self._hot_stream_id = canonical_uuid(
            hot_producer_stream_id,
            field="hot_producer_stream_id",
        )
        self._now = now
        self._new_build_token = new_build_token

    def prepare(
        self,
        *,
        scope: WorkspaceCatalogScope,
        mode: LifecycleRunMode,
        configured_bounds: ConfiguredSourceBounds,
        allow_expired_repair: bool = False,
    ) -> PreparedLifecycleRevision:
        if not isinstance(scope, WorkspaceCatalogScope):
            raise TypeError("scope must be a WorkspaceCatalogScope")
        if not isinstance(mode, LifecycleRunMode):
            raise TypeError("mode must be a LifecycleRunMode")
        if not isinstance(configured_bounds, ConfiguredSourceBounds):
            raise TypeError("configured_bounds must be ConfiguredSourceBounds")
        if type(allow_expired_repair) is not bool:
            raise TypeError("allow_expired_repair must be a bool")
        observed_at = self._now()
        _require_utc(observed_at, "now")
        active = self._state_reader.load_latest_active(scope)
        open_reservation = self._state_reader.load_nonterminal(scope)
        if open_reservation is not None and active is not None:
            open_lease = open_reservation.lease
            if open_lease.catalog_revision == active.catalog_revision:
                if open_reservation.status is not ReservationStatus.FENCED:
                    raise DurableLifecycleError(
                        "active revision still has a non-fenced reservation"
                    )
                if open_lease.build_token != active.build_token:
                    raise DurableLifecycleError(
                        "active revision conflicts with the newest fenced build"
                    )
                open_reservation = None
            elif open_lease.catalog_revision < active.catalog_revision:
                if (
                    open_reservation.status is not ReservationStatus.FENCED
                    and open_lease.expires_at > observed_at
                ):
                    raise DurableLifecycleError(
                        "active lineage advanced past a live older reservation"
                    )
                open_reservation = None
        if open_reservation is not None:
            return self._recover(
                scope=scope,
                requested_mode=mode,
                configured_bounds=configured_bounds,
                active=active,
                reservation=open_reservation,
                observed_at=observed_at,
                allow_expired_repair=allow_expired_repair,
            )
        return self._reserve(
            scope=scope,
            mode=mode,
            configured_bounds=configured_bounds,
            active=active,
            observed_at=observed_at,
        )

    def run(
        self,
        *,
        scope: WorkspaceCatalogScope,
        mode: LifecycleRunMode,
        configured_bounds: ConfiguredSourceBounds,
        executor: LifecycleExecutor,
        completion_reader: LifecycleCompletionReader,
    ) -> LifecycleRunResult:
        if not callable(getattr(executor, "execute", None)):
            raise TypeError("executor must implement execute")
        if not callable(getattr(completion_reader, "load_completion", None)):
            raise TypeError("completion_reader must implement load_completion")
        prepared = self.prepare(
            scope=scope,
            mode=mode,
            configured_bounds=configured_bounds,
        )
        executor.execute(prepared)
        completion = completion_reader.load_completion(prepared)
        if completion is None:
            raise DurableLifecycleError(
                "executor returned without durable completion evidence"
            )
        completion.validate_for(prepared)
        return LifecycleRunResult(prepared=prepared, completion=completion)

    def _recover(
        self,
        *,
        scope: WorkspaceCatalogScope,
        requested_mode: LifecycleRunMode,
        configured_bounds: ConfiguredSourceBounds,
        active: PriorActiveEvidence | None,
        reservation: PersistedReservation,
        observed_at: datetime,
        allow_expired_repair: bool,
    ) -> PreparedLifecycleRevision:
        lease = reservation.lease
        _require_lease_scope(lease, scope)
        decoded = _decode_plan_scope(lease.build_plan)
        _validate_prior_marker(decoded.prior_active_revision, active)
        if active is not None and active.catalog_revision >= lease.catalog_revision:
            raise DurableLifecycleError(
                "active lineage advanced into or beyond the incomplete revision"
            )
        if (
            reservation.status is not ReservationStatus.FENCED
            and lease.expires_at <= observed_at
        ):
            if not allow_expired_repair:
                raise DurableLifecycleError(
                    "workspace has an expired incomplete revision; explicit repair is required"
                )
            if active is None:
                if (
                    requested_mode is not LifecycleRunMode.INITIAL_BACKFILL
                    or decoded.mode is not LifecycleRunMode.INITIAL_BACKFILL
                ):
                    raise DurableLifecycleError(
                        "expired no-active repair requires explicit initial-backfill mode"
                    )
                repair_mode = LifecycleRunMode.INITIAL_BACKFILL
            else:
                if requested_mode is LifecycleRunMode.INITIAL_BACKFILL:
                    raise DurableLifecycleError(
                        "expired active repair cannot use initial-backfill mode"
                    )
                # The explicit repair flag authorizes abandoning only the
                # expired, never-activated build. Re-resolve AUTO against the
                # last qualified active revision instead of inheriting the
                # failed build's mode. This lets a bounded incremental run
                # catch up from the active upper watermark after a large daily
                # full repair exhausts its wall, while stale anchors still
                # resolve back to FULL_REPAIR and remain fail-closed.
                repair_mode = _resolve_requested_mode(
                    requested=requested_mode,
                    active=active,
                    observed_at=observed_at,
                )
            return self._reserve(
                scope=scope,
                mode=repair_mode,
                configured_bounds=configured_bounds,
                active=active,
                observed_at=observed_at,
            )
        # A live or fenced reservation remains immutable. An expired OPEN or
        # DRAINING reservation may reach the explicit repair branch above and
        # reserve a fresh revision against the current project inventory.
        if lease.build_plan.source_scope.project_ids != scope.project_ids:
            raise DurableLifecycleError(
                "open revision project inventory differs from the workspace scope"
            )
        # Persisted mode always wins.  In particular, the next two-minute
        # incremental tick may safely finish a crashed full repair, but can
        # never reinterpret its empty lower-watermark contract as incremental.
        effective_mode = decoded.mode
        _ = requested_mode
        # The persisted source window is authoritative on recovery. Configuration
        # is consulted only before a new reservation; a restart never recomputes
        # or widens the already-admitted half-open plan.
        _ = configured_bounds
        if active is not None:
            prior_cutoffs = _decode_plan_scope(active.build_plan).cutoffs
            if decoded.cutoffs.snapshot_upper <= prior_cutoffs.snapshot_upper:
                raise DurableLifecycleError(
                    "open scheduled build does not advance the active upper cutoff"
                )
            if effective_mode is LifecycleRunMode.INCREMENTAL and (
                decoded.cutoffs.span_window.since != prior_cutoffs.span_window.until
            ):
                raise DurableLifecycleError(
                    "open incremental build does not continue the active span window"
                )
        if reservation.status is not ReservationStatus.FENCED:
            # ``allocate`` is the serialized read-after-write verifier. Supplying
            # persisted bytes makes OPEN/DRAINING recovery idempotent.
            recovered = self._coordinator.allocate(
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                catalog_epoch=scope.catalog_epoch,
                projection_version=scope.projection_version,
                build_token=lease.build_token,
                source_scope=lease.build_plan.source_scope,
                planned_streams=lease.build_plan.streams,
                now=observed_at,
            )
            if recovered != lease:
                raise DurableLifecycleError(
                    "coordinator changed the persisted open lease"
                )
        resumes = tuple(self._state_reader.load_resumes(lease))
        if reservation.status is ReservationStatus.FENCED:
            _require_fenced_resumes(lease, resumes)
        return _prepared(
            scope=scope,
            mode=effective_mode,
            lease=lease,
            cutoffs=decoded.cutoffs,
            active=active,
            resumes=resumes,
            reservation_status=reservation.status,
            resumed=True,
        )

    def _reserve(
        self,
        *,
        scope: WorkspaceCatalogScope,
        mode: LifecycleRunMode,
        configured_bounds: ConfiguredSourceBounds,
        active: PriorActiveEvidence | None,
        observed_at: datetime,
    ) -> PreparedLifecycleRevision:
        mode = _resolve_requested_mode(
            requested=mode,
            active=active,
            observed_at=observed_at,
        )
        if active is not None and mode is LifecycleRunMode.INCREMENTAL:
            if active.build_plan.source_scope.project_ids != scope.project_ids:
                mode = LifecycleRunMode.FULL_REPAIR
        if mode is LifecycleRunMode.INITIAL_BACKFILL:
            if active is not None:
                raise DurableLifecycleError(
                    "initial backfill requires a workspace with no active catalog"
                )
            span_since = configured_bounds.origin
            configured_until: datetime | None = configured_bounds.initial_until
        else:
            if active is None:
                raise DurableLifecycleError(
                    f"{mode} requires prior active checkpoint evidence"
                )
            active_scope = _decode_plan_scope(active.build_plan)
            span_since = (
                active_scope.cutoffs.span_window.until
                if mode is LifecycleRunMode.INCREMENTAL
                else configured_bounds.origin
            )
            configured_until = None
            if mode is LifecycleRunMode.INCREMENTAL:
                _require_fresh_lineage_anchor(active, observed_at=observed_at)
        cutoffs = self._cutoff_freezer(
            scope=scope,
            mode=mode,
            span_since=span_since,
            configured_until=configured_until,
            prior_active=active,
        )
        frozen_at = self._now()
        _require_utc(frozen_at, "now")
        expected_span_since = (
            max(span_since, cutoffs.snapshot_upper - _MAX_SOURCE_WINDOW)
            if mode is LifecycleRunMode.FULL_REPAIR
            else span_since
        )
        if cutoffs.span_window.since != expected_span_since:
            raise DurableLifecycleError(
                "cutoff freezer changed the required lower bound"
            )
        if configured_until is not None and (
            cutoffs.snapshot_upper != configured_until
        ):
            raise DurableLifecycleError(
                "initial cutoff freezer changed the configured frozen upper bound"
            )
        if mode is not LifecycleRunMode.INITIAL_BACKFILL and not (
            observed_at - timedelta(seconds=1)
            <= cutoffs.snapshot_upper
            <= frozen_at + timedelta(seconds=1)
        ):
            raise DurableLifecycleError(
                "scheduled source upper cutoff was not freshly frozen in UTC"
            )
        if active is not None:
            previous = _decode_plan_scope(active.build_plan).cutoffs
            if cutoffs.snapshot_upper <= previous.snapshot_upper:
                raise DurableLifecycleError(
                    "scheduled source upper cutoff did not strictly advance"
                )
        build_token = canonical_uuid(
            self._new_build_token(),
            field="build_token",
        )
        planned_streams = _planned_streams(
            scope=scope,
            mode=mode,
            build_token=build_token,
            hot_producer_stream_id=self._hot_stream_id,
            cutoffs=cutoffs,
            prior_active_revision=(
                active.catalog_revision if active is not None else None
            ),
        )
        source_scope = BuildPlanSourceScope(
            project_ids=scope.project_ids,
            span_since_us=_datetime_to_micros(cutoffs.span_window.since),
            span_until_us=_datetime_to_micros(cutoffs.span_window.until),
        )
        lease = self._coordinator.allocate(
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            catalog_epoch=scope.catalog_epoch,
            projection_version=scope.projection_version,
            build_token=build_token,
            source_scope=source_scope,
            planned_streams=planned_streams,
            now=observed_at,
        )
        persisted = self._state_reader.load_nonterminal(scope)
        if persisted is None or persisted.lease != lease:
            raise DurableLifecycleError(
                "new reservation was not durably readable with identical bytes"
            )
        decoded = _decode_plan_scope(persisted.lease.build_plan)
        if (
            decoded.mode is not mode
            or decoded.cutoffs != cutoffs
            or decoded.prior_active_revision
            != (active.catalog_revision if active is not None else None)
        ):
            raise DurableLifecycleError("persisted reservation changed frozen scope")
        resumes = tuple(self._state_reader.load_resumes(lease))
        if resumes:
            raise DurableLifecycleError("new reservation already has checkpoint state")
        return _prepared(
            scope=scope,
            mode=mode,
            lease=lease,
            cutoffs=cutoffs,
            active=active,
            resumes=(),
            reservation_status=persisted.status,
            resumed=False,
        )


@dataclass(frozen=True, slots=True)
class _DecodedPlanScope:
    mode: LifecycleRunMode
    cutoffs: FrozenLifecycleCutoffs
    prior_active_revision: int | None


@dataclass(frozen=True, slots=True)
class _ActivationState:
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
    activation_sha256: str
    status: str
    qualified_at: datetime


class _CatalogStateClient(Protocol):
    catalog_database: str

    def query(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Sequence[Mapping[str, Any]]: ...


class ClickHouseLifecycleStateReader:
    """SELECT-only reader for persisted reservation, activation, and resume state."""

    def __init__(
        self,
        client: _CatalogStateClient,
        *,
        database: str,
        checkpoint_store: LifecycleCheckpointStore,
        deadline: SharedCatalogDeadline | None = None,
        timeout_ms: int = 8_500,
    ) -> None:
        require_catalog_database(database)
        if getattr(client, "catalog_database", None) != database:
            raise ValueError("lifecycle client database identity mismatch")
        if not callable(getattr(checkpoint_store, "load_checkpoint_write", None)):
            raise TypeError("checkpoint_store lacks durable state methods")
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 8_500:
            raise ValueError("lifecycle timeout_ms must be in [1, 8500]")
        self._client = client
        self._database = database
        self._store = checkpoint_store
        self._deadline = deadline
        self._timeout_ms = timeout_ms

    def load_nonterminal(
        self,
        scope: WorkspaceCatalogScope,
    ) -> PersistedReservation | None:
        candidates = list(self._reservations(scope))
        fenced = self._latest_fenced(scope)
        if fenced is not None:
            candidates.append(fenced)
        if not candidates:
            return None
        newest_revision = max(value.lease.catalog_revision for value in candidates)
        newest = tuple(
            value
            for value in candidates
            if value.lease.catalog_revision == newest_revision
        )
        if len(newest) != 1:
            raise DurableLifecycleError(
                "newest recoverable revision has multiple build tokens"
            )
        selected = newest[0]
        for stale in candidates:
            if stale is selected or stale.status is ReservationStatus.FENCED:
                continue
            if stale.lease.expires_at > selected.lease.issued_at:
                raise DurableLifecycleError(
                    "workspace has overlapping nonterminal revision reservations"
                )
        return selected

    def load_latest_active(
        self,
        scope: WorkspaceCatalogScope,
    ) -> PriorActiveEvidence | None:
        records = self._activation_states(scope)
        if not records:
            return None
        ordered = tuple(sorted(records, key=lambda value: value.activation_sequence))
        if any(
            record.organization_id != scope.organization_id
            or record.workspace_id != scope.workspace_id
            or record.catalog_epoch != scope.catalog_epoch
            or record.projection_version != scope.projection_version
            for record in ordered
        ):
            raise DurableLifecycleError("activation history changed workspace scope")
        active = ordered[-1]
        anchor = self._lineage_anchor(ordered)
        lineage = tuple(
            value
            for value in ordered
            if value.activation_sequence >= anchor.activation_sequence
        )
        sequences = tuple(value.activation_sequence for value in lineage)
        if sequences != tuple(range(sequences[0], sequences[-1] + 1)) or any(
            left.catalog_revision >= right.catalog_revision
            for left, right in zip(lineage, lineage[1:], strict=False)
        ):
            raise DurableLifecycleError(
                "activation lineage is not uniquely monotonic from its snapshot anchor"
            )
        reservation = self._reservation_for(
            scope,
            catalog_revision=active.catalog_revision,
            build_token=active.build_token,
            require_fenced=True,
        )
        if reservation is None:
            raise DurableLifecycleError("active revision has no persisted reservation")
        if reservation.lease.projection_version != active.projection_version:
            raise DurableLifecycleError("active reservation projection changed")
        decoded_plan = _decode_plan_scope(reservation.lease.build_plan)
        if CatalogLifecycleMode(decoded_plan.mode.value) is not active.lifecycle_mode:
            raise DurableLifecycleError("active reservation changed its lifecycle mode")
        manifest_streams = _manifest_streams(active.source_manifest_json)
        planned = {
            (value.source_adapter, value.role): (
                value.producer_stream_id,
                value.source_version_fence,
            )
            for value in reservation.lease.build_plan.streams
        }
        if manifest_streams != planned:
            raise DurableLifecycleError(
                "active manifest does not match its persisted build plan"
            )
        streams: list[ActiveStreamEvidence] = []
        for plan_stream in reservation.lease.build_plan.streams:
            checkpoint = self._store.load_checkpoint_write(
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                catalog_epoch=scope.catalog_epoch,
                catalog_revision=active.catalog_revision,
                build_token=active.build_token,
                source_adapter=plan_stream.source_adapter,
                producer_stream_id=plan_stream.producer_stream_id,
            )
            if checkpoint is None:
                raise DurableLifecycleError(
                    "active build is missing a persisted stream checkpoint"
                )
            _validate_checkpoint_for_plan(checkpoint, reservation.lease, plan_stream)
            if (
                checkpoint.checkpoint.status is not CheckpointStatus.COMPLETE
                or not checkpoint.checkpoint.terminal
                or checkpoint.checkpoint.gap_count
                or checkpoint.checkpoint.poison_count
                or checkpoint.checkpoint.conflict_count
            ):
                raise DurableLifecycleError(
                    "active checkpoint is incomplete, poisoned, conflicted, or gapped"
                )
            streams.append(
                ActiveStreamEvidence(
                    source_adapter=plan_stream.source_adapter,
                    role=plan_stream.role,
                    producer_stream_id=plan_stream.producer_stream_id,
                    source_version_fence=plan_stream.source_version_fence,
                    watermark=_persisted_active_watermark(
                        checkpoint,
                        snapshot_cutoff=decoded_plan.cutoffs.snapshot_upper,
                    ),
                    checkpoint_state_sha256=checkpoint.checkpoint.state_sha256,
                )
            )
        return PriorActiveEvidence(
            catalog_revision=active.catalog_revision,
            build_token=active.build_token,
            projection_version=active.projection_version,
            lifecycle_mode=active.lifecycle_mode,
            activation_sequence=active.activation_sequence,
            activation_sha256=active.activation_sha256,
            source_manifest_sha256=active.source_manifest_sha256,
            build_plan=reservation.lease.build_plan,
            streams=tuple(streams),
            qualified_at=active.qualified_at,
            lineage_anchor=anchor,
        )

    def _activation_states(
        self,
        scope: WorkspaceCatalogScope,
    ) -> tuple[_ActivationState, ...]:
        selected = ", ".join(f"s.{value} AS {value}" for value in _ACTIVATION_COLUMNS)
        logical_limit = MAX_ACTIVE_REVISIONS_SINCE_ANCHOR + 2
        physical_limit = logical_limit * 2 + 1
        rows = tuple(
            self._query(
                "WITH latest_versions AS ("
                "SELECT organization_id, workspace_id, catalog_epoch, "
                "catalog_revision, build_token, max(_version) AS latest_version "
                f"FROM `{self._database}`.`{_ACTIVATION_TABLE}` "
                "WHERE organization_id=%(organization_id)s "
                "AND workspace_id=%(workspace_id)s "
                "AND catalog_epoch=%(catalog_epoch)s "
                "GROUP BY organization_id, workspace_id, catalog_epoch, "
                "catalog_revision, build_token"
                "), active_keys AS ("
                "SELECT s.organization_id AS organization_id, "
                "s.workspace_id AS workspace_id, "
                "s.catalog_epoch AS catalog_epoch, "
                "s.catalog_revision AS catalog_revision, "
                "s.build_token AS build_token, "
                "max(s.activation_sequence) AS latest_activation_sequence "
                f"FROM `{self._database}`.`{_ACTIVATION_TABLE}` AS s "
                "INNER JOIN latest_versions AS latest "
                "ON s.organization_id=latest.organization_id "
                "AND s.workspace_id=latest.workspace_id "
                "AND s.catalog_epoch=latest.catalog_epoch "
                "AND s.catalog_revision=latest.catalog_revision "
                "AND s.build_token=latest.build_token "
                "AND s._version=latest.latest_version "
                "WHERE s.organization_id=%(organization_id)s "
                "AND s.workspace_id=%(workspace_id)s "
                "AND s.catalog_epoch=%(catalog_epoch)s AND s.status='active' "
                "GROUP BY s.organization_id, s.workspace_id, s.catalog_epoch, "
                "s.catalog_revision, s.build_token "
                "ORDER BY latest_activation_sequence DESC, "
                "s.catalog_revision DESC, s.build_token "
                "LIMIT %(logical_limit)s"
                ") "
                f"SELECT DISTINCT {selected} "
                f"FROM `{self._database}`.`{_ACTIVATION_TABLE}` AS s "
                "INNER JOIN latest_versions AS latest "
                "ON s.organization_id=latest.organization_id "
                "AND s.workspace_id=latest.workspace_id "
                "AND s.catalog_epoch=latest.catalog_epoch "
                "AND s.catalog_revision=latest.catalog_revision "
                "AND s.build_token=latest.build_token "
                "AND s._version=latest.latest_version "
                "INNER JOIN active_keys AS active "
                "ON s.organization_id=active.organization_id "
                "AND s.workspace_id=active.workspace_id "
                "AND s.catalog_epoch=active.catalog_epoch "
                "AND s.catalog_revision=active.catalog_revision "
                "AND s.build_token=active.build_token "
                "WHERE s.organization_id=%(organization_id)s "
                "AND s.workspace_id=%(workspace_id)s "
                "AND s.catalog_epoch=%(catalog_epoch)s "
                "ORDER BY s.activation_sequence DESC, s.catalog_revision DESC, "
                "s.build_token, s._version DESC "
                "LIMIT %(physical_limit)s",
                {
                    "organization_id": scope.organization_id,
                    "workspace_id": scope.workspace_id,
                    "catalog_epoch": scope.catalog_epoch,
                    "logical_limit": logical_limit,
                    "physical_limit": physical_limit,
                },
            )
        )
        if len(rows) >= physical_limit:
            raise DurableLifecycleError(
                "activation latest-state proof exceeded its variant ceiling"
            )
        grouped: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
        for row in rows:
            key = (
                _uint(row.get("catalog_revision"), "catalog_revision"),
                _text(row.get("build_token"), "build_token"),
            )
            grouped.setdefault(key, []).append(row)
        states = tuple(
            _activation_state(
                _unique_latest_columns(
                    values,
                    logical_columns=_ACTIVATION_LOGICAL_COLUMNS,
                    label=f"activation:{key}",
                ),
                scope=scope,
            )
            for key, values in sorted(grouped.items())
        )
        active = tuple(value for value in states if value.status == "active")
        if len({value.catalog_revision for value in active}) != len(active):
            raise DurableLifecycleError(
                "multiple active builds claim one catalog revision"
            )
        return active

    @staticmethod
    def _lineage_anchor(
        activations: Sequence[_ActivationState],
    ) -> LineageAnchorEvidence:
        active = activations[-1]
        anchors = tuple(
            value
            for value in activations
            if value.catalog_revision == active.lineage_anchor_revision
        )
        if len(anchors) != 1:
            raise DurableLifecycleError(
                "readable activation lineage has no unique persisted anchor"
            )
        anchor = anchors[0]
        if (
            anchor.lifecycle_mode
            not in {
                CatalogLifecycleMode.INITIAL_BACKFILL,
                CatalogLifecycleMode.FULL_REPAIR,
            }
            or anchor.lineage_anchor_revision != anchor.catalog_revision
        ):
            raise DurableLifecycleError(
                "persisted lineage anchor is not a snapshot activation"
            )
        depth = active.activation_sequence - anchor.activation_sequence
        if not 0 <= depth <= MAX_ACTIVE_REVISIONS_SINCE_ANCHOR:
            raise DurableLifecycleError(
                "latest full-repair lineage anchor is outside the safe depth"
            )
        return LineageAnchorEvidence(
            catalog_revision=anchor.catalog_revision,
            build_token=anchor.build_token,
            mode=LifecycleRunMode(anchor.lifecycle_mode.value),
            qualified_at=anchor.qualified_at,
            activation_sequence=anchor.activation_sequence,
            activation_sha256=anchor.activation_sha256,
            active_revisions_since=depth,
        )

    def load_resumes(self, lease: RevisionLease) -> Sequence[CheckpointWrite]:
        resumes: list[CheckpointWrite] = []
        for plan_stream in lease.build_plan.streams:
            checkpoint = self._store.load_checkpoint_write(
                organization_id=lease.organization_id,
                workspace_id=lease.workspace_id,
                catalog_epoch=lease.catalog_epoch,
                catalog_revision=lease.catalog_revision,
                build_token=lease.build_token,
                source_adapter=plan_stream.source_adapter,
                producer_stream_id=plan_stream.producer_stream_id,
            )
            if checkpoint is None:
                continue
            _validate_checkpoint_for_plan(checkpoint, lease, plan_stream)
            if checkpoint.checkpoint.status not in {
                CheckpointStatus.RUNNING,
                CheckpointStatus.FAILED,
                CheckpointStatus.COMPLETE,
            }:
                raise DurableLifecycleError(
                    "open revision checkpoint has an unsafe status"
                )
            if checkpoint.checkpoint.terminal != (
                checkpoint.checkpoint.status is CheckpointStatus.COMPLETE
            ):
                raise DurableLifecycleError(
                    "open revision checkpoint terminal state is inconsistent"
                )
            resumes.append(checkpoint)
        return tuple(resumes)

    def _reservations(
        self,
        scope: WorkspaceCatalogScope,
    ) -> tuple[PersistedReservation, ...]:
        selected = ", ".join(f"s.{value} AS {value}" for value in _RESERVATION_COLUMNS)
        rows = tuple(
            self._query(
                f"SELECT DISTINCT {selected} "
                f"FROM `{self._database}`.`{_SOURCE_STREAM_TABLE}` AS s "
                "INNER JOIN ("
                "SELECT organization_id, workspace_id, catalog_epoch, "
                "catalog_revision, build_token, max(_version) AS latest_version "
                f"FROM `{self._database}`.`{_SOURCE_STREAM_TABLE}` "
                "WHERE organization_id=%(organization_id)s "
                "AND workspace_id=%(workspace_id)s "
                "AND catalog_epoch=%(catalog_epoch)s AND envelope_version=0 "
                "AND producer_stream_id=build_token "
                "GROUP BY organization_id, workspace_id, catalog_epoch, "
                "catalog_revision, build_token"
                ") AS latest ON s.organization_id=latest.organization_id "
                "AND s.workspace_id=latest.workspace_id "
                "AND s.catalog_epoch=latest.catalog_epoch "
                "AND s.catalog_revision=latest.catalog_revision "
                "AND s.build_token=latest.build_token "
                "AND s._version=latest.latest_version "
                "WHERE s.organization_id=%(organization_id)s "
                "AND s.workspace_id=%(workspace_id)s "
                "AND s.catalog_epoch=%(catalog_epoch)s "
                "AND s.envelope_version=0 AND s.producer_stream_id=s.build_token "
                "AND s.status IN ('open', 'draining') "
                "ORDER BY s.catalog_revision DESC, s.build_token, s._version DESC "
                "LIMIT %(reservation_limit)s",
                {
                    "organization_id": scope.organization_id,
                    "workspace_id": scope.workspace_id,
                    "catalog_epoch": scope.catalog_epoch,
                    "reservation_limit": _MAX_NONTERMINAL_RESERVATION_ROWS + 1,
                },
            )
        )
        if len(rows) > _MAX_NONTERMINAL_RESERVATION_ROWS:
            raise DurableLifecycleError(
                "nonterminal reservation history exceeded its bounded row cap"
            )
        grouped: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
        for row in rows:
            key = (
                _uint(row.get("catalog_revision"), "catalog_revision"),
                _text(row.get("build_token"), "build_token"),
            )
            grouped.setdefault(key, []).append(row)
        result = []
        for key in sorted(grouped):
            _unique_latest(grouped[key], label=f"reservation-candidate:{key}")
            verified = self._reservation_for(
                scope,
                catalog_revision=key[0],
                build_token=key[1],
            )
            if verified is None or verified.status not in {
                ReservationStatus.OPEN,
                ReservationStatus.DRAINING,
            }:
                raise DurableLifecycleError(
                    "nonterminal reservation candidate changed during verification"
                )
            result.append(verified)
        return tuple(result)

    def _reservation_for(
        self,
        scope: WorkspaceCatalogScope,
        *,
        catalog_revision: int,
        build_token: str,
        require_fenced: bool = False,
    ) -> PersistedReservation | None:
        rows = tuple(
            self._query(
                f"SELECT DISTINCT {', '.join(_RESERVATION_COLUMNS)} "
                f"FROM `{self._database}`.`{_SOURCE_STREAM_TABLE}` "
                "WHERE organization_id=%(organization_id)s "
                "AND workspace_id=%(workspace_id)s "
                "AND catalog_epoch=%(catalog_epoch)s "
                "AND catalog_revision=%(catalog_revision)s "
                "AND build_token=%(build_token)s AND envelope_version=0 "
                "AND producer_stream_id=build_token "
                "ORDER BY _version DESC LIMIT 32",
                {
                    "organization_id": scope.organization_id,
                    "workspace_id": scope.workspace_id,
                    "catalog_epoch": scope.catalog_epoch,
                    "catalog_revision": catalog_revision,
                    "build_token": build_token,
                },
            )
        )
        if not rows:
            return None
        latest = _unique_latest(rows, label="active-reservation")
        raw_status = _text(latest.get("status"), "status")
        if raw_status not in set(ReservationStatus):
            raise DurableLifecycleError("reservation status is unsupported")
        status = ReservationStatus(raw_status)
        if require_fenced and status is not ReservationStatus.FENCED:
            raise DurableLifecycleError("active reservation is not durably fenced")
        return PersistedReservation(
            lease=_lease_from_reservation(latest, scope),
            status=status,
        )

    def _latest_fenced(
        self,
        scope: WorkspaceCatalogScope,
    ) -> PersistedReservation | None:
        selected = ", ".join(f"s.{value} AS {value}" for value in _RESERVATION_COLUMNS)
        rows = tuple(
            self._query(
                f"SELECT DISTINCT {selected} "
                f"FROM `{self._database}`.`{_SOURCE_STREAM_TABLE}` AS s "
                "INNER JOIN ("
                "SELECT organization_id, workspace_id, catalog_epoch, "
                "catalog_revision, build_token, max(_version) AS latest_version "
                f"FROM `{self._database}`.`{_SOURCE_STREAM_TABLE}` "
                "WHERE organization_id=%(organization_id)s "
                "AND workspace_id=%(workspace_id)s "
                "AND catalog_epoch=%(catalog_epoch)s AND envelope_version=0 "
                "AND producer_stream_id=build_token "
                "GROUP BY organization_id, workspace_id, catalog_epoch, "
                "catalog_revision, build_token"
                ") AS latest ON s.organization_id=latest.organization_id "
                "AND s.workspace_id=latest.workspace_id "
                "AND s.catalog_epoch=latest.catalog_epoch "
                "AND s.catalog_revision=latest.catalog_revision "
                "AND s.build_token=latest.build_token "
                "AND s._version=latest.latest_version "
                "WHERE s.organization_id=%(organization_id)s "
                "AND s.workspace_id=%(workspace_id)s "
                "AND s.catalog_epoch=%(catalog_epoch)s "
                "AND s.envelope_version=0 AND s.producer_stream_id=s.build_token "
                "AND s.status='fenced' "
                "ORDER BY s.catalog_revision DESC, s.build_token, s._version DESC "
                "LIMIT 3",
                {
                    "organization_id": scope.organization_id,
                    "workspace_id": scope.workspace_id,
                    "catalog_epoch": scope.catalog_epoch,
                },
            )
        )
        if not rows:
            return None
        keys = {
            (
                _uint(row.get("catalog_revision"), "catalog_revision"),
                _text(row.get("build_token"), "build_token"),
            )
            for row in rows
        }
        newest_revision = max(revision for revision, _token in keys)
        newest = tuple(key for key in keys if key[0] == newest_revision)
        if len(newest) != 1:
            raise DurableLifecycleError(
                "newest fenced revision has multiple build tokens"
            )
        revision, build_token = newest[0]
        return self._reservation_for(
            scope,
            catalog_revision=revision,
            build_token=build_token,
            require_fenced=True,
        )

    def _query(
        self,
        sql: str,
        params: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]:
        require_catalog_database(self._database)
        if getattr(self._client, "catalog_database", None) != self._database:
            raise DurableLifecycleError("lifecycle client database identity changed")
        timeout_ms = self._timeout_ms
        if self._deadline is not None:
            timeout_ms = self._deadline.remaining_ms(cap_ms=timeout_ms)
        return self._client.query(sql, params, timeout_ms=timeout_ms)


def _prepared(
    *,
    scope: WorkspaceCatalogScope,
    mode: LifecycleRunMode,
    lease: RevisionLease,
    cutoffs: FrozenLifecycleCutoffs,
    active: PriorActiveEvidence | None,
    resumes: tuple[CheckpointWrite, ...],
    reservation_status: ReservationStatus,
    resumed: bool,
) -> PreparedLifecycleRevision:
    by_key: dict[tuple[SourceAdapter, str], CheckpointWrite] = {}
    for checkpoint in resumes:
        key = (
            checkpoint.checkpoint.source_adapter,
            checkpoint.checkpoint.producer_stream_id,
        )
        if key in by_key:
            raise DurableLifecycleError("resume checkpoint inventory has duplicates")
        by_key[key] = checkpoint
    lower_by_role = _lower_watermarks(mode, active)
    starts: list[StreamStart] = []
    for stream in lease.build_plan.streams:
        resume = by_key.get(stream.key)
        starts.append(
            StreamStart(
                source_adapter=stream.source_adapter,
                role=stream.role,
                producer_stream_id=stream.producer_stream_id,
                # A persisted checkpoint already contains the exact starting
                # watermark. Passing the prior-active lower bound as well is
                # ambiguous and ReconcileRequest correctly rejects it.
                lower_watermark=(
                    ""
                    if resume is not None
                    else lower_by_role[(stream.source_adapter, stream.role)]
                ),
                resume=resume,
            )
        )
    if set(by_key) - {value.key for value in lease.build_plan.streams}:
        raise DurableLifecycleError(
            "checkpoint exists outside the immutable build plan"
        )
    return PreparedLifecycleRevision(
        scope=scope,
        mode=mode,
        lease=lease,
        cutoffs=cutoffs,
        prior_active=active,
        streams=tuple(starts),
        reservation_status=reservation_status,
        resumed=resumed,
    )


def _lower_watermarks(
    mode: LifecycleRunMode,
    active: PriorActiveEvidence | None,
) -> dict[tuple[SourceAdapter, ManifestStreamRole], str]:
    if mode is not LifecycleRunMode.INCREMENTAL:
        return dict.fromkeys(_EXPECTED_ROLE_INVENTORY, "")
    if active is None:
        raise DurableLifecycleError("incremental lower watermarks require active state")
    result = {value.role_key: value.watermark for value in active.streams}
    if set(result) != _EXPECTED_ROLE_INVENTORY:
        raise DurableLifecycleError("active lower-watermark inventory is incomplete")
    for adapter in _RELATIONAL_ADAPTERS:
        if not result[(adapter, ManifestStreamRole.DEFINITIONS)]:
            raise DurableLifecycleError(
                f"active {adapter} checkpoint has no incremental watermark"
            )
    return result


def _persisted_active_watermark(
    checkpoint: CheckpointWrite,
    *,
    snapshot_cutoff: datetime,
) -> str:
    """Normalize only a provably empty legacy relational checkpoint.

    Older reconciler builds could activate a terminal relational stream with
    an empty watermark when its frozen snapshot contained zero rows.  The
    immutable build plan retains the exact snapshot cutoff, so that one legacy
    shape can be recovered without mutating persisted control-plane evidence.
    Every other blank relational watermark remains corruption and fails closed.
    """

    if checkpoint.watermark:
        return checkpoint.watermark
    value = checkpoint.checkpoint
    if value.source_adapter not in _RELATIONAL_ADAPTERS:
        return ""
    if (
        value.status is CheckpointStatus.COMPLETE
        and value.terminal
        and value.source_count == 0
        and value.gap_count == 0
        and value.poison_count == 0
        and value.conflict_count == 0
    ):
        return SourceKeysetCursor(snapshot_cutoff, ZERO_UUID).encode()
    raise DurableLifecycleError(
        f"active {value.source_adapter} checkpoint has no incremental watermark"
    )


def _planned_streams(
    *,
    scope: WorkspaceCatalogScope,
    mode: LifecycleRunMode,
    build_token: str,
    hot_producer_stream_id: str,
    cutoffs: FrozenLifecycleCutoffs,
    prior_active_revision: int | None,
) -> tuple[BuildPlanStream, ...]:
    prefix = mode.value
    upper_us = _datetime_to_micros(cutoffs.snapshot_upper)

    def stream_id(adapter: SourceAdapter, role: ManifestStreamRole) -> str:
        if role is ManifestStreamRole.HOT_VALUES:
            return hot_producer_stream_id
        return str(
            uuid.uuid5(
                _STREAM_NAMESPACE,
                ":".join(
                    (
                        scope.organization_id,
                        scope.workspace_id,
                        str(scope.catalog_epoch),
                        build_token,
                        str(adapter),
                        str(role),
                    )
                ),
            )
        )

    values: list[BuildPlanStream] = []
    system_label = (
        f"{prefix}_no_prior_active"
        if prior_active_revision is None
        else f"{prefix}_prior_active_revision_plus_epoch"
    )
    system_value = scope.catalog_epoch
    if prior_active_revision is not None:
        # The system manifest has no database timestamp, so its source version
        # advances with catalog lineage. The previous encoding used the prior
        # revision directly, which regressed below the initial epoch (for
        # example epoch 5 -> prior revision 1) and made every unchanged system
        # binding look stale/conflicting on the first incremental run.
        system_value += prior_active_revision
        if system_value >= 1 << 64:
            raise DurableLifecycleError("system-manifest source version exceeds UInt64")
    values.append(
        BuildPlanStream(
            source_adapter=SourceAdapter.SYSTEM_MANIFEST,
            role=ManifestStreamRole.DEFINITIONS,
            producer_stream_id=stream_id(
                SourceAdapter.SYSTEM_MANIFEST,
                ManifestStreamRole.DEFINITIONS,
            ),
            source_cutoff_label=system_label,
            source_version_fence=system_value,
        )
    )
    for adapter in sorted(_RELATIONAL_ADAPTERS):
        values.append(
            BuildPlanStream(
                source_adapter=adapter,
                role=ManifestStreamRole.DEFINITIONS,
                producer_stream_id=stream_id(
                    adapter,
                    ManifestStreamRole.DEFINITIONS,
                ),
                source_cutoff_label=f"{prefix}_postgres_until_us",
                source_version_fence=upper_us,
            )
        )
    span_values = (
        (
            ManifestStreamRole.DEFINITIONS,
            f"{prefix}_span_definition_version_us",
            upper_us,
        ),
        (
            ManifestStreamRole.HOT_VALUES,
            f"{prefix}_span_until_us",
            upper_us,
        ),
        (
            ManifestStreamRole.VALUES,
            f"{prefix}_span_audit_generation",
            cutoffs.span_audit_generation,
        ),
        (
            ManifestStreamRole.SOURCE_AUDIT,
            f"{prefix}_span_audit_generation",
            cutoffs.span_audit_generation,
        ),
    )
    for role, label, cutoff in span_values:
        values.append(
            BuildPlanStream(
                source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
                role=role,
                producer_stream_id=stream_id(SourceAdapter.SPAN_ATTRIBUTE, role),
                source_cutoff_label=label,
                source_version_fence=cutoff,
            )
        )
    return tuple(
        sorted(
            values,
            key=lambda value: (
                value.source_adapter,
                value.role,
                value.producer_stream_id,
            ),
        )
    )


def _decode_plan_scope(plan: RevisionBuildPlan) -> _DecodedPlanScope:
    if not isinstance(plan, RevisionBuildPlan):
        raise TypeError("plan must be a RevisionBuildPlan")
    by_role = {(value.source_adapter, value.role): value for value in plan.streams}
    _require_exact_role_inventory(tuple(by_role), label="build plan")
    if len(by_role) != len(plan.streams):
        raise DurableLifecycleError("build plan contains duplicate lifecycle roles")
    prefixes = {
        value.source_cutoff_label[: -len(suffix)]
        for value in plan.streams
        for suffix in _label_suffixes(value)
        if value.source_cutoff_label.endswith(suffix)
    }
    try:
        mode = LifecycleRunMode(next(iter(prefixes)))
    except (StopIteration, ValueError) as exc:
        raise DurableLifecycleError(
            "build plan has no supported lifecycle mode"
        ) from exc
    if prefixes != {mode.value}:
        raise DurableLifecycleError("build plan mixes lifecycle modes")
    if mode is LifecycleRunMode.AUTO:
        raise DurableLifecycleError("persisted build plan has unresolved auto mode")
    expected_labels = _expected_labels(mode)
    for key, stream in by_role.items():
        expected = {expected_labels[key]}
        if (
            key
            == (
                SourceAdapter.SYSTEM_MANIFEST,
                ManifestStreamRole.DEFINITIONS,
            )
            and mode is not LifecycleRunMode.INITIAL_BACKFILL
        ):
            # Read old, already-persisted plans so an in-flight rollout can be
            # diagnosed/recovered after upgrading. New plans are emitted only
            # with the monotonic plus-epoch encoding.
            expected.add(f"{mode.value}_prior_active_revision")
        if key == (
            SourceAdapter.SPAN_ATTRIBUTE,
            ManifestStreamRole.DEFINITIONS,
        ):
            # Decode reservations persisted before span-definition versions
            # moved from the rolling window's lower bound to its monotonic
            # upper cutoff. New plans never emit this legacy label.
            expected.add(f"{mode.value}_span_since_us")
        if stream.source_cutoff_label not in expected:
            raise DurableLifecycleError(
                f"build plan cutoff label is invalid for {key[0]}/{key[1]}"
            )
    relational_upper = {
        by_role[(adapter, ManifestStreamRole.DEFINITIONS)].source_version_fence
        for adapter in _RELATIONAL_ADAPTERS
    }
    if len(relational_upper) != 1:
        raise DurableLifecycleError("relational streams do not share one upper cutoff")
    upper_us = next(iter(relational_upper))
    hot_upper = by_role[
        (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.HOT_VALUES)
    ].source_version_fence
    if hot_upper != upper_us:
        raise DurableLifecycleError("span and relational upper cutoffs differ")
    values_generation = by_role[
        (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.VALUES)
    ].source_version_fence
    audit_generation = by_role[
        (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.SOURCE_AUDIT)
    ].source_version_fence
    if values_generation != audit_generation:
        raise DurableLifecycleError("span value and audit generations differ")
    since_us = plan.source_scope.span_since_us
    span_definitions = by_role[
        (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.DEFINITIONS)
    ]
    if span_definitions.source_cutoff_label == f"{mode.value}_span_since_us":
        if span_definitions.source_version_fence != since_us:
            raise DurableLifecycleError(
                "legacy span definition fence differs from source_scope"
            )
    elif span_definitions.source_version_fence != upper_us:
        raise DurableLifecycleError(
            "span definition version does not match the frozen upper cutoff"
        )
    cutoffs = FrozenLifecycleCutoffs(
        snapshot_upper=_micros_to_datetime(upper_us),
        span_window=SourceWindow(
            _micros_to_datetime(since_us),
            _micros_to_datetime(upper_us),
        ),
        span_audit_generation=audit_generation,
    )
    if plan.source_scope.span_until_us != upper_us:
        raise DurableLifecycleError(
            "build-plan source_scope differs from its stream cutoff window"
        )
    system = by_role[(SourceAdapter.SYSTEM_MANIFEST, ManifestStreamRole.DEFINITIONS)]
    if system.source_cutoff_label == f"{mode.value}_no_prior_active":
        prior_revision = None
    elif system.source_cutoff_label == (
        f"{mode.value}_prior_active_revision_plus_epoch"
    ):
        prior_revision = system.source_version_fence - plan.catalog_epoch
        if prior_revision < 1:
            raise DurableLifecycleError(
                "scheduled build plan has an invalid encoded prior revision"
            )
    else:
        # Compatibility for plans persisted before the monotonic system-source
        # version fix. This path is decode-only; _planned_streams never emits it.
        prior_revision = system.source_version_fence
    if mode is LifecycleRunMode.INITIAL_BACKFILL and prior_revision is not None:
        raise DurableLifecycleError("initial build plan claims prior active state")
    if mode is not LifecycleRunMode.INITIAL_BACKFILL and prior_revision is None:
        raise DurableLifecycleError("scheduled build plan lacks prior active state")
    if prior_revision is None and system.source_version_fence != plan.catalog_epoch:
        raise DurableLifecycleError("initial build plan no-prior marker changed")
    return _DecodedPlanScope(mode, cutoffs, prior_revision)


def _expected_labels(
    mode: LifecycleRunMode,
) -> dict[tuple[SourceAdapter, ManifestStreamRole], str]:
    prefix = mode.value
    result = {
        (adapter, ManifestStreamRole.DEFINITIONS): f"{prefix}_postgres_until_us"
        for adapter in _RELATIONAL_ADAPTERS
    }
    result.update(
        {
            (SourceAdapter.SYSTEM_MANIFEST, ManifestStreamRole.DEFINITIONS): (
                f"{prefix}_no_prior_active"
                if mode is LifecycleRunMode.INITIAL_BACKFILL
                else f"{prefix}_prior_active_revision_plus_epoch"
            ),
            (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.DEFINITIONS): (
                f"{prefix}_span_definition_version_us"
            ),
            (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.HOT_VALUES): (
                f"{prefix}_span_until_us"
            ),
            (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.VALUES): (
                f"{prefix}_span_audit_generation"
            ),
            (SourceAdapter.SPAN_ATTRIBUTE, ManifestStreamRole.SOURCE_AUDIT): (
                f"{prefix}_span_audit_generation"
            ),
        }
    )
    return result


def _label_suffixes(stream: BuildPlanStream) -> tuple[str, ...]:
    key = (stream.source_adapter, stream.role)
    if key == (SourceAdapter.SYSTEM_MANIFEST, ManifestStreamRole.DEFINITIONS):
        return (
            "_no_prior_active",
            "_prior_active_revision_plus_epoch",
            "_prior_active_revision",
        )
    if stream.source_adapter in _RELATIONAL_ADAPTERS:
        return ("_postgres_until_us",)
    if stream.role is ManifestStreamRole.DEFINITIONS:
        return ("_span_definition_version_us", "_span_since_us")
    if stream.role is ManifestStreamRole.HOT_VALUES:
        return ("_span_until_us",)
    return ("_span_audit_generation",)


def _manifest_streams(
    source_manifest_json: str,
) -> dict[
    tuple[SourceAdapter, ManifestStreamRole],
    tuple[str, int],
]:
    try:
        decoded = json.loads(source_manifest_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DurableLifecycleError("active source manifest is invalid") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("streams"), list):
        raise DurableLifecycleError("active source manifest has no stream inventory")
    result: dict[
        tuple[SourceAdapter, ManifestStreamRole],
        tuple[str, int],
    ] = {}
    for raw in decoded["streams"]:
        if not isinstance(raw, dict):
            raise DurableLifecycleError("active manifest stream is invalid")
        try:
            adapter = SourceAdapter(raw["source_adapter"])
            role = ManifestStreamRole(raw["role"])
            stream_id = canonical_uuid(
                raw["producer_stream_id"],
                field="producer_stream_id",
            )
            source_fence = raw["source_version_fence"]
            _positive_uint(source_fence, 64, "source_version_fence")
        except (KeyError, TypeError, ValueError) as exc:
            raise DurableLifecycleError(
                "active manifest stream scope is invalid"
            ) from exc
        key = (adapter, role)
        if key in result:
            raise DurableLifecycleError("active manifest contains duplicate roles")
        result[key] = (stream_id, source_fence)
    _require_exact_role_inventory(tuple(result), label="active manifest")
    return result


def _validate_checkpoint_for_plan(
    checkpoint: CheckpointWrite,
    lease: RevisionLease,
    stream: BuildPlanStream,
) -> None:
    value = checkpoint.checkpoint
    if (
        value.organization_id != lease.organization_id
        or value.workspace_id != lease.workspace_id
        or value.catalog_epoch != lease.catalog_epoch
        or value.catalog_revision != lease.catalog_revision
        or value.build_token != lease.build_token
        or value.projection_version != lease.projection_version
        or value.source_adapter is not stream.source_adapter
        or value.producer_stream_id != stream.producer_stream_id
        or checkpoint.source_version_fence != stream.source_version_fence
        or value.source_version_fence != stream.source_version_fence
    ):
        raise DurableLifecycleError("checkpoint scope differs from its build plan")


def _require_fenced_resumes(
    lease: RevisionLease,
    resumes: Sequence[CheckpointWrite],
) -> None:
    expected = {value.key for value in lease.build_plan.streams}
    observed = {
        (
            value.checkpoint.source_adapter,
            value.checkpoint.producer_stream_id,
        )
        for value in resumes
    }
    if len(resumes) != 10 or observed != expected:
        raise DurableLifecycleError(
            "fenced revision lacks its exact ten persisted checkpoints"
        )
    for value in resumes:
        checkpoint = value.checkpoint
        if (
            checkpoint.status is not CheckpointStatus.COMPLETE
            or not checkpoint.terminal
            or checkpoint.gap_count
            or checkpoint.poison_count
            or checkpoint.conflict_count
        ):
            raise DurableLifecycleError(
                "fenced revision has incomplete or unsafe checkpoint evidence"
            )


def _validate_prior_marker(
    marker: int | None,
    active: PriorActiveEvidence | None,
) -> None:
    if marker is None:
        if active is not None:
            raise DurableLifecycleError(
                "open revision claims no prior active state but one exists"
            )
        return
    if active is None or active.catalog_revision != marker:
        raise DurableLifecycleError(
            "open revision prior-active marker differs from durable lineage"
        )


def _require_fresh_lineage_anchor(
    active: PriorActiveEvidence,
    *,
    observed_at: datetime,
) -> None:
    age = (observed_at - active.lineage_anchor.qualified_at).total_seconds()
    if age < 0 or age > MAX_LINEAGE_ANCHOR_AGE_SECONDS:
        raise DurableLifecycleError(
            "incremental revision has no recent daily full-repair lineage anchor"
        )
    if active.lineage_anchor.active_revisions_since >= (
        MAX_ACTIVE_REVISIONS_SINCE_ANCHOR
    ):
        raise DurableLifecycleError(
            "incremental revision would exceed the bounded full-repair lineage depth"
        )


def _resolve_requested_mode(
    *,
    requested: LifecycleRunMode,
    active: PriorActiveEvidence | None,
    observed_at: datetime,
) -> LifecycleRunMode:
    if requested is not LifecycleRunMode.AUTO:
        return requested
    if active is None:
        raise DurableLifecycleError(
            "auto lifecycle requires an active catalog; run initial backfill explicitly"
        )
    age = (observed_at - active.lineage_anchor.qualified_at).total_seconds()
    if age < 0:
        raise DurableLifecycleError("lineage anchor qualification is in the future")
    if (
        age >= FULL_REPAIR_INTERVAL_SECONDS
        or active.lineage_anchor.active_revisions_since
        >= MAX_ACTIVE_REVISIONS_SINCE_ANCHOR
    ):
        return LifecycleRunMode.FULL_REPAIR
    return LifecycleRunMode.INCREMENTAL


def _require_lease_scope(lease: RevisionLease, scope: WorkspaceCatalogScope) -> None:
    if (
        lease.organization_id != scope.organization_id
        or lease.workspace_id != scope.workspace_id
        or lease.catalog_epoch != scope.catalog_epoch
        or lease.projection_version != scope.projection_version
    ):
        raise DurableLifecycleError("persisted reservation changed workspace scope")


def _role_for_stream(
    plan: RevisionBuildPlan,
    key: tuple[SourceAdapter, str],
) -> ManifestStreamRole:
    values = tuple(value.role for value in plan.streams if value.key == key)
    if len(values) != 1:
        raise DurableLifecycleError("stream is absent or duplicated in build plan")
    return values[0]


def _require_exact_role_inventory(
    values: tuple[tuple[SourceAdapter, ManifestStreamRole], ...],
    *,
    label: str,
) -> None:
    if len(values) != 10 or frozenset(values) != _EXPECTED_ROLE_INVENTORY:
        raise DurableLifecycleError(f"{label} does not contain the exact ten roles")


def _lease_from_reservation(
    row: Mapping[str, Any],
    scope: WorkspaceCatalogScope,
) -> RevisionLease:
    if (
        _text(row.get("organization_id"), "organization_id") != scope.organization_id
        or _text(row.get("workspace_id"), "workspace_id") != scope.workspace_id
        or _uint(row.get("catalog_epoch"), "catalog_epoch") != scope.catalog_epoch
        or _uint(row.get("projection_version"), "projection_version")
        != scope.projection_version
        or _uint(row.get("envelope_version"), "envelope_version") != 0
        or _text(row.get("producer_stream_id"), "producer_stream_id")
        != _text(row.get("build_token"), "build_token")
    ):
        raise DurableLifecycleError("reservation row changed workspace identity")
    return RevisionLease(
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        catalog_epoch=scope.catalog_epoch,
        catalog_revision=_uint(row.get("catalog_revision"), "catalog_revision"),
        projection_version=scope.projection_version,
        build_token=_text(row.get("build_token"), "build_token"),
        build_plan_json=_text(row.get("build_plan_json"), "build_plan_json"),
        build_lease_sha256=_text(
            row.get("build_lease_sha256"),
            "build_lease_sha256",
        ),
        issued_at=_datetime(row.get("started_at"), "started_at"),
        expires_at=_datetime(row.get("drain_deadline"), "drain_deadline"),
    )


def _activation_state(
    row: Mapping[str, Any],
    *,
    scope: WorkspaceCatalogScope,
) -> _ActivationState:
    if (
        _text(row.get("organization_id"), "organization_id") != scope.organization_id
        or _text(row.get("workspace_id"), "workspace_id") != scope.workspace_id
        or _uint(row.get("catalog_epoch"), "catalog_epoch") != scope.catalog_epoch
        or _uint(row.get("projection_version"), "projection_version")
        != scope.projection_version
    ):
        raise DurableLifecycleError("activation state changed workspace scope")
    revision = _uint(row.get("catalog_revision"), "catalog_revision")
    sequence = _uint(row.get("activation_sequence"), "activation_sequence")
    anchor = _uint(row.get("lineage_anchor_revision"), "lineage_anchor_revision")
    if not revision or not sequence or not 1 <= anchor <= revision:
        raise DurableLifecycleError("activation revision/sequence/anchor is invalid")
    try:
        mode = CatalogLifecycleMode(_text(row.get("lifecycle_mode"), "lifecycle_mode"))
    except ValueError as exc:
        raise DurableLifecycleError("activation lifecycle mode is invalid") from exc
    if mode in {
        CatalogLifecycleMode.INITIAL_BACKFILL,
        CatalogLifecycleMode.FULL_REPAIR,
    }:
        if anchor != revision:
            raise DurableLifecycleError("snapshot activation does not anchor at itself")
    elif anchor >= revision:
        raise DurableLifecycleError("incremental activation has no earlier anchor")
    manifest = _text(row.get("source_manifest_json"), "source_manifest_json")
    manifest_sha = _text(
        row.get("source_manifest_sha256"),
        "source_manifest_sha256",
    )
    try:
        decoded = json.loads(manifest)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DurableLifecycleError("activation source manifest is invalid") from exc
    if (
        canonical_json(decoded) != manifest
        or canonical_json_sha256(manifest) != manifest_sha
        or decoded.get("lifecycle_mode") != mode.value
        or decoded.get("lineage_anchor_revision") != anchor
    ):
        raise DurableLifecycleError(
            "activation manifest does not bind its lifecycle lineage"
        )
    require_sha256(manifest_sha, field="source_manifest_sha256")
    activation_sha = _text(row.get("activation_sha256"), "activation_sha256")
    require_sha256(activation_sha, field="activation_sha256")
    status = _text(row.get("status"), "status")
    if status not in {"building", "active", "disabled"}:
        raise DurableLifecycleError("activation status is invalid")
    qualified = row.get("qualified_at")
    if status == "active" and qualified is None:
        raise DurableLifecycleError("active revision has no qualification timestamp")
    qualified_at = (
        _datetime(qualified, "qualified_at")
        if qualified is not None
        else datetime(1970, 1, 1, tzinfo=UTC)
    )
    return _ActivationState(
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        catalog_epoch=scope.catalog_epoch,
        catalog_revision=revision,
        build_token=canonical_uuid(
            _text(row.get("build_token"), "build_token"),
            field="build_token",
        ),
        projection_version=scope.projection_version,
        lifecycle_mode=mode,
        lineage_anchor_revision=anchor,
        activation_sequence=sequence,
        source_manifest_json=manifest,
        source_manifest_sha256=manifest_sha,
        activation_sha256=activation_sha,
        status=status,
        qualified_at=qualified_at,
    )


def _unique_latest(
    rows: Sequence[Mapping[str, Any]],
    *,
    label: str,
) -> Mapping[str, Any]:
    return _unique_latest_columns(
        rows,
        logical_columns=_RESERVATION_LOGICAL_COLUMNS,
        label=label,
    )


def _unique_latest_columns(
    rows: Sequence[Mapping[str, Any]],
    *,
    logical_columns: Sequence[str],
    label: str,
) -> Mapping[str, Any]:
    if not rows:
        raise DurableLifecycleError(f"{label} is missing")
    maximum = max(_uint(row.get("_version"), "_version") for row in rows)
    candidates = tuple(
        row for row in rows if _uint(row.get("_version"), "_version") == maximum
    )
    identities = {
        tuple(_stable_value(row.get(column)) for column in logical_columns)
        for row in candidates
    }
    if len(identities) != 1:
        raise DurableLifecycleError(f"{label} has conflicting latest rows")
    return candidates[0]


def _stable_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, (list, tuple)):
        return tuple(_stable_value(item) for item in value)
    return value


def _datetime_to_micros(value: datetime) -> int:
    _require_utc(value, "cutoff")
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    micros = (
        delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    )
    _positive_uint(micros, 64, "cutoff microseconds")
    return micros


def _micros_to_datetime(value: int) -> datetime:
    _positive_uint(value, 64, "cutoff microseconds")
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)
    except (OverflowError, OSError, ValueError) as exc:
        raise DurableLifecycleError("persisted cutoff is not a UTC timestamp") from exc


def _require_utc(value: datetime, field: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise ValueError(f"{field} must be UTC-aware")


def _positive_uint(value: Any, bits: int, field: str) -> None:
    if type(value) is not int or not 1 <= value < (1 << bits):
        raise ValueError(f"{field} must be a positive UInt{bits}")


def _uint(value: Any, field: str) -> int:
    if type(value) is int:
        parsed = value
    elif type(value) is str:
        if (
            not value
            or len(value) > 20
            or (len(value) > 1 and value[0] == "0")
            or any(character < "0" or character > "9" for character in value)
        ):
            raise DurableLifecycleError(f"{field} is not a UInt64")
        parsed = int(value)
    else:
        raise DurableLifecycleError(f"{field} is not a UInt64")
    if not 0 <= parsed < (1 << 64):
        raise DurableLifecycleError(f"{field} is not a UInt64")
    return parsed


def _text(value: Any, field: str) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DurableLifecycleError(f"{field} is not UTF-8 text") from exc
    if isinstance(value, str):
        return value
    if value is None:
        raise DurableLifecycleError(f"{field} is not text")
    return str(value)


def _datetime(value: Any, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DurableLifecycleError(f"{field} is not a datetime") from exc
    if not isinstance(value, datetime):
        raise DurableLifecycleError(f"{field} is not a datetime")
    _require_utc(value, field)
    return value


__all__ = [
    "ActiveStreamEvidence",
    "ClickHouseLifecycleStateReader",
    "ConfiguredSourceBounds",
    "DurableLifecycleError",
    "DurableWorkspaceCatalogLifecycle",
    "FreshSpanLifecycleCutoffFreezer",
    "FULL_REPAIR_INTERVAL_SECONDS",
    "FrozenLifecycleCutoffs",
    "LifecycleCompletionEvidence",
    "LifecycleCompletionReader",
    "LifecycleCutoffFreezer",
    "LifecycleExecutor",
    "LifecycleRunMode",
    "LifecycleRunResult",
    "LifecycleStateReader",
    "LineageAnchorEvidence",
    "MAX_ACTIVE_REVISIONS_SINCE_ANCHOR",
    "MAX_LINEAGE_ANCHOR_AGE_SECONDS",
    "PersistedCheckpointEvidence",
    "PersistedReservation",
    "PreparedLifecycleRevision",
    "PriorActiveEvidence",
    "ReservationStatus",
    "SourceWindow",
    "StreamStart",
    "WorkspaceCatalogScope",
]
