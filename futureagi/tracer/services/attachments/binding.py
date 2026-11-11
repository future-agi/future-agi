"""Bounded ingestion/reconciliation boundary for the attachment control store.

The B02 broker supplies Writer from authentication, never OTLP/resource attrs.
B01/B09 implement BindingStore transactions; B05/B10 implement ParentReader
against collapsed latest live CH state and authoritative retention provenance.
No default/in-memory production store is installed: absent authority stays dark.
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
MAX_ATTEMPTS = 12
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
    internal_id: str
    scope: Scope
    parent: Parent
    actor_id: str
    capture_session_id: str
    correlation_nonce: str
    deletion_generation: int
    policy_version: str
    created_at: datetime
    expires_at: datetime
    attempts: int
    revision: int


@dataclass(frozen=True)
class ParentState:
    scope: Scope
    parent: Parent
    actor_id: str
    capture_session_id: str
    correlation_nonce: str
    deletion_generation: int
    policy_version: str
    # A live child without its authoritative root is not a readable parent.
    root_observed: bool
    live: bool
    policy_allowed: bool
    retention_known: bool
    expires_at: datetime | None
    source_revision: str


class BindingStore(Protocol):
    def stage(self, candidate: Candidate, now: datetime) -> bool:
        """Atomically validate writer/session/nonce and exact registered IDs.

        Reject absent/expired/revoked sessions, scope mismatch, unknown occurrence,
        policy denial and tombstones. Idempotent by complete scope + association;
        a conflicting writer/parent is never a relink. Bounded reservation applies.
        Staging NEVER marks an occurrence bound or bytes readable.
        """
        ...

    def claim_pending(self, now: datetime, limit: int) -> list[PendingLink]:
        """Lease due pending links across scopes, with a hard SQL limit."""
        ...

    def settle(
        self, link: PendingLink, state: str, source: ParentState | None, now: datetime
    ) -> bool:
        """CAS link revision and revalidate policy/session/deletion fences atomically.

        For bound, require unchanged current source_revision and generations and
        derive expiry from source provenance (never extend it). For expired,
        tombstone and enqueue idempotent cleanup/release. For pending, increment
        attempts and schedule bounded backoff. Return false on any concurrent
        change. Physical cleanup is B09's outbox, not a PG cascade.
        """
        ...


class ParentReader(Protocol):
    def latest(
        self, links: list[PendingLink]
    ) -> dict[tuple[Scope, Parent], ParentState]:
        """One bounded batch; collapse versions BEFORE excluding deleted rows.

        Resolve full project/trace/span tuple, root eligibility, writer nonce and
        authoritative retention provenance. Ambiguous/unknown sources are absent.
        Do not use OTLP timestamps as retention provenance.
        """
        ...


def stage_ingested_span(
    *,
    attributes,
    trace_id,
    span_id,
    writer: Writer | None,
    store: BindingStore | None,
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
    store: BindingStore

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


def _decision(link, source, now):
    if (
        now >= min(link.expires_at, link.created_at + MAX_PENDING_AGE)
        or link.attempts >= MAX_ATTEMPTS
    ):
        return "expired"
    if source is None:
        return "pending"
    if source.scope != link.scope or source.parent != link.parent:
        return "rejected"
    if not source.live or source.deletion_generation != link.deletion_generation:
        return "expired"
    if (source.actor_id, source.capture_session_id, source.correlation_nonce) != (
        link.actor_id,
        link.capture_session_id,
        link.correlation_nonce,
    ):
        return "rejected"
    if not source.policy_allowed or source.policy_version != link.policy_version:
        return "rejected"
    if source.expires_at is not None and now >= source.expires_at:
        return "expired"
    if (
        not source.root_observed
        or not source.retention_known
        or not source.source_revision
    ):
        return "pending"
    return "bound"


def reconcile_pending(
    store: BindingStore, reader: ParentReader, *, now: datetime, limit: int = MAX_BATCH
):
    """One finite worker tick. Durable scheduling/leases belong to the store.

    No SDK retry, open span, media fetch, URL or payload is needed. Store failure
    propagates so the scheduler retries; it must never acknowledge a false bind.
    """
    if now.tzinfo is None or type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise ValueError("invalid_attachment_reconciliation_bound")
    links = store.claim_pending(now, limit)
    if len(links) > limit:
        raise ValueError("attachment_store_exceeded_bound")
    sources = reader.latest(links) if links else {}
    counts = dict.fromkeys(("bound", "pending", "rejected", "expired", "conflict"), 0)
    for link in links:
        source = sources.get((link.scope, link.parent))
        state = _decision(link, source, now)
        if store.settle(link, state, source, now):
            counts[state] += 1
        else:
            counts["conflict"] += 1
    return counts
