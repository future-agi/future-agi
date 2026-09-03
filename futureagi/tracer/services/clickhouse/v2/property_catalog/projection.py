"""Pure definition projection, visibility, and version resolution."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .codec import (
    MAX_IDENTITY_COMPONENT_BYTES,
    ZERO_UUID,
    canonical_uuid,
    require_sha256,
    validate_text,
)
from .models import (
    PropertyBindingRow,
    PropertyDefinition,
    SourceAdapter,
    VisibilityBinding,
    VisibilityScope,
    canonicalize_definition,
    make_binding_id,
    make_state_sha256,
)
from .runtime_limits import RUNTIME_LIMITS


class DefinitionConflictError(ValueError):
    """Two rows claim the same immutable source state with different content."""


class VersionResolutionStatus(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    STALE = "stale"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class VersionResolution:
    status: VersionResolutionStatus
    current: PropertyBindingRow


@dataclass(frozen=True, slots=True)
class BindingHistoryResolution:
    current: PropertyBindingRow
    duplicate_count: int
    stale_count: int


@dataclass(frozen=True, slots=True)
class VisibilityContext:
    organization_id: str
    workspace_id: str
    project_ids: frozenset[str] = frozenset()
    agent_definition_ids: frozenset[str] = frozenset()
    dataset_ids: frozenset[str] = frozenset()
    include_workspace_defaults: bool = True

    def __post_init__(self) -> None:
        if type(self.include_workspace_defaults) is not bool:
            raise TypeError("include_workspace_defaults must be a bool")
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
        for field_name in ("project_ids", "agent_definition_ids", "dataset_ids"):
            values = getattr(self, field_name)
            object.__setattr__(
                self,
                field_name,
                frozenset(canonical_uuid(value, field=field_name) for value in values),
            )


def project_definition(
    *,
    organization_id: str,
    workspace_id: str,
    catalog_epoch: int,
    catalog_revision: int,
    build_token: str,
    projection_version: int,
    visibility: VisibilityBinding,
    definition: PropertyDefinition,
    source_adapter: SourceAdapter,
    source_entity_id: str,
    source_version: int,
    source_fingerprint: str,
    producer_stream_id: str,
    producer_sequence: int,
    emitted_at: datetime,
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
    is_deleted: bool = False,
    deleted_at: datetime | None = None,
) -> PropertyBindingRow:
    """Project an adapter definition into one immutable binding event."""

    organization_id = canonical_uuid(organization_id, field="organization_id")
    workspace_id = canonical_uuid(workspace_id, field="workspace_id")
    producer_stream_id = canonical_uuid(
        producer_stream_id,
        field="producer_stream_id",
    )
    build_token = canonical_uuid(build_token, field="build_token")
    canonical = canonicalize_definition(definition)
    binding_id = make_binding_id(
        organization_id=organization_id,
        workspace_id=workspace_id,
        visibility=visibility,
        property_id=canonical.property_id,
        source_adapter=source_adapter,
    )
    state_sha256 = make_state_sha256(
        binding_id=binding_id,
        definition_sha256=canonical.definition_sha256,
        source_entity_id=source_entity_id,
        source_version=source_version,
        source_fingerprint=source_fingerprint,
        is_deleted=is_deleted,
        deleted_at=deleted_at,
        first_seen=first_seen,
        last_seen=last_seen,
    )
    return PropertyBindingRow(
        organization_id=organization_id,
        workspace_id=workspace_id,
        catalog_epoch=catalog_epoch,
        catalog_revision=catalog_revision,
        build_token=build_token,
        projection_version=projection_version,
        binding_id=binding_id,
        visibility_scope=visibility.scope,
        visibility_id=visibility.visibility_id,
        definition=canonical,
        source_adapter=source_adapter,
        source_entity_id=source_entity_id,
        source_version=source_version,
        source_fingerprint=source_fingerprint,
        is_deleted=is_deleted,
        deleted_at=deleted_at,
        state_sha256=state_sha256,
        producer_stream_id=producer_stream_id,
        producer_sequence=producer_sequence,
        first_seen=first_seen,
        last_seen=last_seen,
        emitted_at=emitted_at,
    )


def resolve_source_update(
    current: PropertyBindingRow,
    incoming: PropertyBindingRow,
) -> VersionResolution:
    """Resolve a replay/update without allowing stale state to win."""

    if current.binding_id != incoming.binding_id:
        raise ValueError("cannot resolve updates for different binding IDs")
    if incoming.source_version < current.source_version:
        return VersionResolution(VersionResolutionStatus.STALE, current)
    if incoming.source_version == current.source_version:
        if incoming.state_sha256 != current.state_sha256:
            return VersionResolution(VersionResolutionStatus.CONFLICT, current)
        winner = max((current, incoming), key=_transport_order)
        return VersionResolution(VersionResolutionStatus.DUPLICATE, winner)
    return VersionResolution(VersionResolutionStatus.APPLIED, incoming)


def resolve_binding_history(
    events: tuple[PropertyBindingRow, ...] | list[PropertyBindingRow],
    *,
    at_revision: int | None = None,
) -> BindingHistoryResolution:
    """Resolve one binding history independently of delivery order."""

    eligible = [
        event
        for event in events
        if at_revision is None or event.catalog_revision <= at_revision
    ]
    if not eligible:
        raise ValueError("binding history has no event at the requested revision")
    binding_ids = {event.binding_id for event in eligible}
    if len(binding_ids) != 1:
        raise ValueError("binding history contains multiple binding IDs")

    max_version = max(event.source_version for event in eligible)
    candidates = [event for event in eligible if event.source_version == max_version]
    state_hashes = {event.state_sha256 for event in candidates}
    if len(state_hashes) != 1:
        raise DefinitionConflictError(
            f"binding {eligible[0].binding_id} has conflicting source version {max_version}"
        )
    current = max(candidates, key=_transport_order)
    return BindingHistoryResolution(
        current=current,
        duplicate_count=len(candidates) - 1,
        stale_count=len(eligible) - len(candidates),
    )


def is_visible(row: PropertyBindingRow, context: VisibilityContext) -> bool:
    """Apply already-authorized visibility; never infer tenant access."""

    if (
        row.organization_id != context.organization_id
        or row.workspace_id != context.workspace_id
    ):
        return False
    if row.visibility_scope is VisibilityScope.ALWAYS:
        return row.visibility_id == ZERO_UUID
    if row.visibility_scope is VisibilityScope.WORKSPACE_DEFAULT:
        return (
            context.include_workspace_defaults
            and row.visibility_id == context.workspace_id
        )
    if row.visibility_scope is VisibilityScope.PROJECT:
        return row.visibility_id in context.project_ids
    if row.visibility_scope is VisibilityScope.AGENT_DEFINITION:
        return row.visibility_id in context.agent_definition_ids
    if row.visibility_scope is VisibilityScope.DATASET:
        return row.visibility_id in context.dataset_ids
    return False


def resolve_visible_definitions(
    events: tuple[PropertyBindingRow, ...] | list[PropertyBindingRow],
    *,
    context: VisibilityContext,
    at_revision: int,
) -> tuple[PropertyBindingRow, ...]:
    """Resolve binding histories, visibility, tombstones, and property dedupe."""

    grouped: dict[str, list[PropertyBindingRow]] = {}
    for event in events:
        if event.catalog_revision <= at_revision:
            grouped.setdefault(event.binding_id, []).append(event)

    live: list[PropertyBindingRow] = []
    for history in grouped.values():
        current = resolve_binding_history(history, at_revision=at_revision).current
        if not current.is_deleted and is_visible(current, context):
            live.append(current)

    by_property: dict[str, list[PropertyBindingRow]] = {}
    for row in live:
        by_property.setdefault(row.property_id, []).append(row)

    deduped: list[PropertyBindingRow] = []
    for property_id, bindings in by_property.items():
        definition_hashes = {
            binding.definition.definition_sha256 for binding in bindings
        }
        if len(definition_hashes) != 1:
            raise DefinitionConflictError(
                f"property {property_id} has conflicting visible definitions"
            )
        deduped.append(min(bindings, key=_visibility_order))
    return tuple(sorted(deduped, key=lambda row: row.order_key))


def _transport_order(row: PropertyBindingRow) -> tuple[int, int, datetime, str]:
    return (
        row.catalog_revision,
        row.producer_sequence,
        row.emitted_at,
        row.state_sha256,
    )


def _visibility_order(row: PropertyBindingRow) -> tuple[int, str]:
    rank = {
        VisibilityScope.PROJECT: 0,
        VisibilityScope.AGENT_DEFINITION: 1,
        VisibilityScope.DATASET: 2,
        VisibilityScope.WORKSPACE_DEFAULT: 3,
        VisibilityScope.ALWAYS: 4,
    }
    return rank[row.visibility_scope], row.binding_id


@dataclass(frozen=True, slots=True)
class PostgresReadBudget:
    """Hard limits a relational source adapter must apply to every page."""

    statement_timeout_ms: int = RUNTIME_LIMITS.postgres_statement_timeout_ms
    wall_timeout_seconds: float = RUNTIME_LIMITS.source_adapter_wall_seconds
    max_rows_per_page: int = RUNTIME_LIMITS.postgres_page_rows
    max_total_rows: int = RUNTIME_LIMITS.postgres_max_total_rows
    initial_backfill: bool = False
    scheduled_reconcile: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.statement_timeout_ms) is not int
            or not 1
            <= self.statement_timeout_ms
            <= RUNTIME_LIMITS.postgres_statement_timeout_ms
        ):
            raise ValueError(
                "statement_timeout_ms must be between 1 and "
                f"{RUNTIME_LIMITS.postgres_statement_timeout_ms}"
            )
        if type(self.initial_backfill) is not bool:
            raise ValueError("initial_backfill must be a bool")
        if type(self.scheduled_reconcile) is not bool:
            raise ValueError("scheduled_reconcile must be a bool")
        if self.initial_backfill and self.scheduled_reconcile:
            raise ValueError(
                "initial_backfill and scheduled_reconcile are mutually exclusive"
            )
        if self.initial_backfill:
            wall_mode = "initial-backfill"
            maximum_wall_seconds = (
                RUNTIME_LIMITS.initial_backfill_source_adapter_wall_seconds
            )
        elif self.scheduled_reconcile:
            wall_mode = "scheduled-reconcile"
            maximum_wall_seconds = (
                RUNTIME_LIMITS.scheduled_reconcile_source_adapter_wall_seconds
            )
        else:
            wall_mode = "standard"
            maximum_wall_seconds = RUNTIME_LIMITS.source_adapter_wall_seconds
        if (
            type(self.wall_timeout_seconds) not in {int, float}
            or isinstance(self.wall_timeout_seconds, bool)
            or not 0 < self.wall_timeout_seconds <= maximum_wall_seconds
        ):
            raise ValueError(
                f"wall_timeout_seconds exceeds the bounded {wall_mode} wall"
            )
        if self.statement_timeout_ms >= self.wall_timeout_seconds * 1_000:
            raise ValueError("statement timeout must be below the adapter wall")
        if (
            type(self.max_rows_per_page) is not int
            or not 1 <= self.max_rows_per_page <= RUNTIME_LIMITS.postgres_page_rows
        ):
            raise ValueError(
                "max_rows_per_page must be between 1 and "
                f"{RUNTIME_LIMITS.postgres_page_rows}"
            )
        if (
            type(self.max_total_rows) is not int
            or not 1 <= self.max_total_rows <= RUNTIME_LIMITS.postgres_max_total_rows
        ):
            raise ValueError(
                "max_total_rows must be between 1 and "
                f"{RUNTIME_LIMITS.postgres_max_total_rows}"
            )


@dataclass(frozen=True, slots=True)
class PostgresSnapshotContext:
    organization_id: str
    workspace_id: str
    project_ids: tuple[str, ...]
    catalog_epoch: int
    catalog_revision: int
    projection_version: int
    snapshot_cutoff: datetime

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
        if not isinstance(self.project_ids, tuple):
            raise TypeError("project_ids must be a tuple")
        projects = tuple(
            sorted(
                canonical_uuid(project_id, field="project_id")
                for project_id in self.project_ids
            )
        )
        if not projects or len(projects) > 256 or len(set(projects)) != len(projects):
            raise ValueError("project_ids must contain 1..256 unique canonical UUIDs")
        object.__setattr__(self, "project_ids", projects)
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
        if self.snapshot_cutoff.tzinfo is None:
            raise ValueError("snapshot_cutoff must be timezone-aware")


@dataclass(frozen=True, slots=True)
class PostgresSourcePage:
    definitions: tuple[PropertyDefinition, ...]
    next_cursor: str | None
    terminal: bool
    source_count: int
    source_digest: str

    def __post_init__(self) -> None:
        if type(self.terminal) is not bool:
            raise TypeError("terminal must be a bool")
        if type(self.source_count) is not int or not 0 <= self.source_count < (1 << 64):
            raise ValueError("source_count must be a UInt64")
        require_sha256(self.source_digest, field="source_digest")
        if self.terminal != (self.next_cursor is None):
            raise ValueError("terminal pages must not expose a next cursor")


def validate_postgres_page(
    page: PostgresSourcePage,
    *,
    budget: PostgresReadBudget,
) -> None:
    """Require a source page to honor the caller's explicit row ceilings."""

    if len(page.definitions) > budget.max_rows_per_page:
        raise ValueError("PostgreSQL property page exceeds max_rows_per_page")
    if page.source_count > budget.max_total_rows:
        raise ValueError("PostgreSQL property page exceeds max_total_rows")
    if page.next_cursor is not None:
        validate_text(
            page.next_cursor,
            field="next_cursor",
            max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
        )


@runtime_checkable
class ReadOnlyPostgresPropertyAdapter(Protocol):
    """Interface only; implementations must never write to source tables."""

    source_adapter: SourceAdapter
    read_only: bool
    isolation_level: str

    @abstractmethod
    def read_page(
        self,
        *,
        context: PostgresSnapshotContext,
        cursor: str | None,
        budget: PostgresReadBudget,
    ) -> PostgresSourcePage:
        """Read one keyset page from a read-only repeatable-read snapshot."""


def validate_postgres_adapter(adapter: ReadOnlyPostgresPropertyAdapter) -> None:
    """Fail closed if an adapter does not declare the required read contract."""

    if not isinstance(adapter.source_adapter, SourceAdapter):
        raise TypeError("source_adapter must be a SourceAdapter")
    if adapter.source_adapter in {
        SourceAdapter.SYSTEM_MANIFEST,
        SourceAdapter.SPAN_ATTRIBUTE,
    }:
        raise ValueError("non-PostgreSQL sources cannot use this adapter interface")
    if adapter.read_only is not True:
        raise ValueError("PostgreSQL property adapters must be read-only")
    if not isinstance(adapter.isolation_level, str):
        raise TypeError("isolation_level must be a string")
    if adapter.isolation_level.casefold().replace("_", " ") != "repeatable read":
        raise ValueError("PostgreSQL property adapters must use repeatable read")
    validate_text(
        adapter.isolation_level,
        field="isolation_level",
        max_bytes=MAX_IDENTITY_COMPONENT_BYTES,
    )
