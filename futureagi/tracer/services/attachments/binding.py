"""Ingestion staging seam plus the bounded reconcile engine for occurrence binding.

Binding proof (``control_plane`` supplies the concrete store and reader):
- the occurrence's parent was fixed by an authenticated, eligible writer when
  the upload was initialized (B02 broker), never by OTLP attributes; and
- the latest live ClickHouse span at exactly that scope/trace/span persists a
  normalized ``fi.attachment.refs`` entry naming the same attachment and
  occurrence, with the same direction and modality; and
- the source's retention is known (the same rule the read path applies).

The capture-session carriers are stripped before persistence on both ingest
paths, so the nonce is not re-proven at bind time. ``stage_ingested_span`` is
an optional credential-bound observation hook; binding does not depend on it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from .references import (
    InvalidReference,
    canonical_span_id,
    canonical_trace_id,
    canonical_uuid,
    extract_projection,
)

MAX_BATCH = 256
MAX_PENDING_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class Scope:
    organization_id: str
    workspace_id: str
    project_id: str

    def __post_init__(self):
        for value in (self.organization_id, self.workspace_id, self.project_id):
            canonical_uuid(value)


@dataclass(frozen=True)
class Parent:
    trace_id: str
    span_id: str

    def __post_init__(self):
        object.__setattr__(self, "trace_id", canonical_trace_id(self.trace_id))
        canonical_span_id(self.span_id)


@dataclass(frozen=True)
class Writer:
    actor_id: str
    scope: Scope

    def __post_init__(self):
        canonical_uuid(self.actor_id)


@dataclass(frozen=True)
class Candidate:
    writer: Writer
    parent: Parent
    capture_session_id: str
    correlation_nonce: str
    # IDs only. Policy/direction/retention come from the server upload record.
    references: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PendingLink:
    """One unbound live occurrence. Its parent came from the authenticated upload."""

    internal_id: object
    scope_id: object
    scope: Scope
    parent: Parent
    attachment_id: str
    occurrence_id: str
    direction: str
    modality: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ParentState:
    """Latest collapsed ClickHouse facts for one exact parent.

    ``references`` maps occurrence_id to the persisted, validated reference.
    ``source`` is the backend ``SourceState`` that binding stores; it is None
    when retention is unknown, which never binds.
    """

    scope: Scope
    parent: Parent
    live: bool
    references: dict
    source: object | None
    expires_at: datetime | None


class StagingStore(Protocol):
    """Optional credential-bound ingest observation; nothing installs one yet.

    Both ingest paths lack the original credential id today (the Go actor and
    the Python ingestion task carry only org/workspace/user), so a staging
    store would have no writer to verify. Binding never waits for it.
    """

    def stage(self, candidate: Candidate, now: datetime) -> bool:
        """Atomically validate writer/session/nonce and exact registered IDs.

        Reject absent/expired/revoked sessions, scope mismatch, unknown occurrence,
        policy denial and tombstones. Idempotent by complete scope + association;
        a conflicting writer/parent is never a relink. Bounded reservation applies.
        Staging NEVER marks an occurrence bound or bytes readable.
        """
        ...


class BindingStore(Protocol):
    def claim_pending(self, now: datetime, limit: int) -> list[PendingLink]:
        """Unbound live occurrences, oldest first, with a hard SQL limit."""
        ...

    def settle(
        self, link: PendingLink, state: str, source: ParentState | None, now: datetime
    ) -> bool:
        """Apply one decision atomically; False on any concurrent change.

        ``bound`` goes through the backend's fenced CAS (scope enabled, policy
        and deletion generation unchanged, identical source on replay) and
        never extends the source expiry. ``expired``/``rejected`` tombstone the
        occurrence and enqueue the erase outbox when nothing else references
        the bytes. ``pending`` changes nothing.
        """
        ...


class ParentReader(Protocol):
    def latest(
        self, links: list[PendingLink]
    ) -> dict[tuple[Scope, Parent], ParentState]:
        """One bounded batch; collapse versions BEFORE excluding deleted rows.

        Resolve the full project/trace/span tuple and authoritative retention.
        Ambiguous/unknown sources are absent. OTLP timestamps are never used.
        """
        ...


def stage_ingested_span(
    *,
    attributes,
    trace_id,
    span_id,
    writer: Writer | None,
    store: StagingStore | None,
    now: datetime,
):
    """Server-only hook; safe when called before or after span persistence.

    Reconciliation independently demands successful authoritative ingestion.
    Passing user_id as actor_id is not a substitute for the broker credential ID.
    """
    if writer is None or store is None:
        return False
    try:
        projection = extract_projection(attributes)
        if projection is None or projection.capture_session_id is None:
            return False
        candidate = Candidate(
            writer=writer,
            parent=Parent(trace_id, span_id),
            capture_session_id=projection.capture_session_id,
            correlation_nonce=projection.correlation_nonce,
            references=tuple(
                (ref["attachment_id"], ref["occurrence_id"]) for ref in projection.refs
            ),
        )
    except InvalidReference:
        return False
    return store.stage(candidate, now)


@dataclass(frozen=True)
class IngestionBinding:
    """Explicit converter dependency, constructed by the authenticated broker."""

    writer: Writer
    store: StagingStore

    def stage(self, span, project, organization_id, now):
        scope = self.writer.scope
        if (
            str(organization_id),
            str(project.organization_id),
            str(project.workspace_id),
            str(project.id),
        ) != (
            scope.organization_id,
            scope.organization_id,
            scope.workspace_id,
            scope.project_id,
        ):
            return False
        return stage_ingested_span(
            attributes=span.get("attributes", {}),
            trace_id=span.get("trace_id"),
            span_id=span.get("span_id"),
            writer=self.writer,
            store=self.store,
            now=now,
        )


def _decision(link, parent, now):
    if now >= min(link.expires_at, link.created_at + MAX_PENDING_AGE):
        return "expired"
    if parent is None:
        return "pending"
    if parent.scope != link.scope or parent.parent != link.parent:
        return "rejected"
    if not parent.live:
        return "expired"
    reference = parent.references.get(link.occurrence_id)
    if reference is None:
        # The span may be re-exported with its references; expiry bounds this.
        return "pending"
    if (
        reference["attachment_id"],
        reference["direction"],
        reference["modality"],
    ) != (link.attachment_id, link.direction, link.modality):
        return "rejected"
    if parent.source is None or parent.expires_at is None:
        return "pending"
    if now >= parent.expires_at:
        return "expired"
    return "bound"


def reconcile_pending(
    store: BindingStore, reader: ParentReader, *, now: datetime, limit: int = MAX_BATCH
):
    """One finite worker tick. Durable state lives in the occurrence rows.

    No SDK retry, open span, media fetch, URL or payload is needed. Store failure
    propagates so the scheduler retries; it must never acknowledge a false bind.
    """
    if now.tzinfo is None or type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise ValueError("invalid_attachment_reconciliation_bound")
    links = store.claim_pending(now, limit)
    if len(links) > limit:
        raise ValueError("attachment_store_exceeded_bound")
    parents = reader.latest(links) if links else {}
    counts = dict.fromkeys(("bound", "pending", "rejected", "expired", "conflict"), 0)
    for link in links:
        parent = parents.get((link.scope, link.parent))
        state = _decision(link, parent, now)
        if store.settle(link, state, parent, now):
            counts[state] += 1
        else:
            counts["conflict"] += 1
    return counts
