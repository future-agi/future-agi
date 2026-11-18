"""A01: bounded durable source intents, not a Sessions authority provider.

Lock order: owned PG transaction -> existing project lifecycle advisory lock ->
source scope -> intent -> delivery. No refresh/lease/attempt locks are acquired.
All mutating calls own a durable outer transaction: nested calls (including
source transactions/Collector/lease callers) are deliberately UNAVAILABLE until
those owners integrate a reviewed order. Direct CH dispatch is allowed only
AFTER begin AND claim_dispatch have returned/committed. Never wrap them and the
network call in an outer atomic block. All reads/writes explicitly use primary
'default'; a non-Postgres or non-READ-COMMITTED connection is unavailable.

There is no abandon/reset/expire-pending API. A claimed send is uncertain even
if the process dies before it opens the socket. Retrying cannot erase that
uncertainty; callers need an exact terminal receipt from the original send.
Acknowledgements are trusted internal transport evidence, NOT client input or
proof that all producers are enrolled. No PG response revision is certified.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from uuid import UUID

from django.db import DEFAULT_DB_ALIAS, connections, transaction

from tracer.models.metric_source_intent import (
    MetricSourceDelivery as Delivery,
)
from tracer.models.metric_source_intent import (
    MetricSourceIntent as Intent,
)
from tracer.models.metric_source_intent import (
    MetricSourcePage as Page,
)
from tracer.models.metric_source_intent import (
    MetricSourceScope as Scope,
)

MAX_PAGES = 32
MAX_ROWS = 500
MAX_BYTES = 16 * 1024 * 1024
MAX_TARGETS = 16
MAX_CLOSE = 500
MAX_REVISION = (1 << 63) - 1
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")


class SourceIntentError(RuntimeError):
    """Sanitized protocol failure; never includes private identities/payloads."""


class SourceConflict(SourceIntentError):
    pass


class SourceUnavailable(SourceIntentError):
    pass


class Store(str, Enum):
    SPANS = "spans"
    TRACES = "traces"
    SESSIONS = "trace_sessions"
    USERS = "end_users"
    SESSION_REMAP = "trace_session_id_remap"
    USER_REMAP = "end_user_id_remap"


class MemberState(str, Enum):
    LIVE = "live"
    TOMBSTONE = "tombstone"


class ReceiptKind(str, Enum):
    LOCAL_SYNCHRONOUS_INSERT = "local-synchronous-insert-v1"


class ProgressState(str, Enum):
    PENDING = "pending"
    SETTLED = "settled-not-qualified"


@dataclass(frozen=True)
class SourceScope:
    organization_id: UUID
    project_id: UUID


@dataclass(frozen=True)
class PhysicalIdentity:
    store: Store
    # spans: project,type,service,start_hour,trace,span; traces: project,
    # created_month,id (RMT replacement is partition-local). Other stores:
    # project,immutable id. Provenance of remap ownership is a producer obligation.
    key: tuple[str, ...]


@dataclass(frozen=True)
class CanonicalMembership:
    session_id: UUID | None
    user_id: UUID | None


@dataclass(frozen=True)
class MemberImage:
    physical: PhysicalIdentity
    canonical: CanonicalMembership
    state: MemberState = MemberState.LIVE


@dataclass(frozen=True)
class MembershipChange:
    old: MemberImage | None
    new: MemberImage | None


@dataclass(frozen=True)
class MembershipPage:
    changes: tuple[MembershipChange, ...]


@dataclass(frozen=True)
class SelectedStore:
    server_id: UUID
    database: str
    store: Store
    schema_sha256: str
    payload_sha256: str


@dataclass(frozen=True)
class SourceIntentToken:
    scope: SourceScope
    epoch: UUID
    intent_id: UUID
    operation_key: UUID
    revision: int
    manifest_sha256: str


@dataclass(frozen=True)
class DispatchTicket:
    token: SourceIntentToken
    receipt_id: UUID
    target: SelectedStore


@dataclass(frozen=True)
class SelectedServerReceipt:
    ticket: DispatchTicket
    kind: ReceiptKind
    # Exact terminal server query identity, not a timestamp or MAX(version).
    query_id: str


@dataclass(frozen=True)
class SourceProgress:
    scope: SourceScope
    epoch: UUID
    revision: int
    closed_revision: int
    state: ProgressState


def canonical_bytes(value) -> bytes:
    """Canonical metadata encoding; no truncation or arbitrary object coercion."""

    def encode(item):
        if isinstance(item, UUID):
            return str(item)
        raise TypeError("unsupported source metadata type")

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=encode,
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _uuid(value):
    if type(value) is not UUID or value.int == 0:
        raise SourceConflict("nonzero UUID required")


def _scope(scope):
    if type(scope) is not SourceScope:
        raise SourceConflict("source scope required")
    _uuid(scope.organization_id)
    _uuid(scope.project_id)


def _target(target):
    if type(target) is not SelectedStore or type(target.store) is not Store:
        raise SourceConflict("selected store required")
    _uuid(target.server_id)
    if not isinstance(target.database, str) or not _NAME.fullmatch(target.database):
        raise SourceConflict("invalid selected database")
    for digest in (target.schema_sha256, target.payload_sha256):
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise SourceConflict("invalid digest")


def _freeze(scope, pages, targets):
    _scope(scope)
    if type(pages) is not tuple or not 1 <= len(pages) <= MAX_PAGES:
        raise SourceConflict("bounded page tuple required")
    if type(targets) is not tuple or not 1 <= len(targets) <= MAX_TARGETS:
        raise SourceConflict("bounded selected-store tuple required")
    for target in targets:
        _target(target)
    ids = [(t.server_id, t.database, t.store) for t in targets]
    if len(set(ids)) != len(ids):
        raise SourceConflict("duplicate selected store")
    stores = {t.store for t in targets}
    page_bytes, count, size = [], 0, 0
    for page in pages:
        if (
            type(page) is not MembershipPage
            or type(page.changes) is not tuple
            or not page.changes
        ):
            raise SourceConflict("nonempty immutable membership page required")
        count += len(page.changes)
        if count > MAX_ROWS:
            raise SourceConflict("source operation row bound exceeded")
        for change in page.changes:
            if type(change) is not MembershipChange or (
                change.old is None and change.new is None
            ):
                raise SourceConflict("old or new membership required")
            for image in (change.old, change.new):
                if image is None:
                    continue
                if (
                    type(image) is not MemberImage
                    or type(image.physical) is not PhysicalIdentity
                ):
                    raise SourceConflict("physical member image required")
                key, store = image.physical.key, image.physical.store
                if (
                    type(store) is not Store
                    or store not in stores
                    or type(key) is not tuple
                ):
                    raise SourceConflict("member store not selected")
                if len(key) != (
                    6 if store is Store.SPANS else 3 if store is Store.TRACES else 2
                ):
                    raise SourceConflict("complete physical key required")
                if any(
                    type(v) is not str or len(v.encode("utf-8")) > 4096 for v in key
                ):
                    raise SourceConflict("invalid physical key")
                if key[0] != str(scope.project_id):
                    raise SourceConflict("cross-scope physical key")
                if (
                    type(image.canonical) is not CanonicalMembership
                    or type(image.state) is not MemberState
                ):
                    raise SourceConflict(
                        "canonical membership and explicit member state required"
                    )
                for value in (image.canonical.session_id, image.canonical.user_id):
                    if value is not None:
                        _uuid(value)
        data = canonical_bytes(asdict(page))
        size += len(data)
        if size > MAX_BYTES:
            raise SourceConflict("source operation byte bound exceeded")
        page_bytes.append(data)
    # Target order is not part of identity. Page order IS part of the manifest.
    targets = tuple(
        sorted(targets, key=lambda t: (str(t.server_id), t.database, t.store.value))
    )
    manifest = sha256(
        canonical_bytes(
            {
                "format": 1,
                "scope": asdict(scope),
                "pages": [sha256(p) for p in page_bytes],
                "targets": [asdict(t) for t in targets],
            }
        )
    )
    return page_bytes, targets, manifest


@contextmanager
def _locked(scope):
    _scope(scope)
    db = connections[DEFAULT_DB_ALIAS]
    if db.vendor != "postgresql" or db.in_atomic_block or not db.get_autocommit():
        raise SourceUnavailable("source adapter requires an owned primary transaction")
    # Reuse the real lock, including its key; no competing deletion/publication protocol.
    from tracer.services.derived_metrics.deletion_protocol import project_lock

    with transaction.atomic(using=DEFAULT_DB_ALIAS, durable=True):
        with db.cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            if cursor.fetchone()[0] != "read committed":
                raise SourceUnavailable("source adapter requires READ COMMITTED")
        project_lock(scope.project_id, using=DEFAULT_DB_ALIAS)
        yield


def _rows(Model):
    return Model.objects.using(DEFAULT_DB_ALIAS)


def _state(scope):
    return (
        _rows(Scope)
        .select_for_update()
        .filter(organization_id=scope.organization_id, project_id=scope.project_id)
        .first()
    )


def _token(state, intent):
    return SourceIntentToken(
        SourceScope(state.organization_id, state.project_id),
        state.epoch,
        intent.id,
        intent.operation_key,
        intent.revision,
        intent.manifest_sha256,
    )


def _intent(token):
    if type(token) is not SourceIntentToken:
        raise SourceConflict("source token required")
    _scope(token.scope)
    for value in (token.epoch, token.intent_id, token.operation_key):
        _uuid(value)
    if (
        type(token.revision) is not int
        or not 1 <= token.revision <= MAX_REVISION
        or type(token.manifest_sha256) is not str
        or not _HASH.fullmatch(token.manifest_sha256)
    ):
        raise SourceConflict("invalid source token revision or digest")
    state = _state(token.scope)
    if state is None or state.epoch != token.epoch:
        raise SourceConflict("stale source epoch")
    intent = (
        _rows(Intent)
        .select_for_update()
        .filter(scope=state, id=token.intent_id)
        .first()
    )
    if intent is None or _token(state, intent) != token:
        raise SourceConflict("source intent identity mismatch")
    return state, intent


def begin_source_intent(
    scope: SourceScope,
    operation_key: UUID,
    bounded_old_new_pages: tuple[MembershipPage, ...],
    *,
    expected_stores: tuple[SelectedStore, ...],
) -> SourceIntentToken:
    """Commit invalidation before dispatch; identical operation retries reuse identity.

    Only complete producer-supplied old/new images are accepted. Validating their
    structure cannot prove completeness/provenance; enrollment owns that proof.
    Bound each physical operation before registration. There is intentionally no
    partial append/seal protocol or positive empty-bootstrap receipt here.
    """
    _uuid(operation_key)
    pages, targets, manifest = _freeze(scope, bounded_old_new_pages, expected_stores)
    with _locked(scope):
        state = _state(scope)
        if state is None:
            state = _rows(Scope).create(
                organization_id=scope.organization_id, project_id=scope.project_id
            )
        prior = _rows(Intent).filter(scope=state, operation_key=operation_key).first()
        if prior is not None:
            if prior.manifest_sha256 != manifest:
                raise SourceConflict("operation identity reused with different content")
            return _token(state, prior)
        if state.revision >= MAX_REVISION:
            raise SourceUnavailable("source revision exhausted")
        state.revision += 1
        state.save(using=DEFAULT_DB_ALIAS, update_fields=("revision",))
        intent = _rows(Intent).create(
            scope=state,
            operation_key=operation_key,
            revision=state.revision,
            manifest_sha256=manifest,
            page_count=len(pages),
            target_count=len(targets),
        )
        _rows(Page).bulk_create(
            [
                Page(intent=intent, ordinal=i, sha256=sha256(p), payload=p)
                for i, p in enumerate(pages)
            ]
        )
        _rows(Delivery).bulk_create(
            [
                Delivery(
                    intent=intent,
                    server_id=t.server_id,
                    database=t.database,
                    store=t.store.value,
                    schema_sha256=t.schema_sha256,
                    payload_sha256=t.payload_sha256,
                )
                for t in targets
            ]
        )
        return _token(state, intent)


def _delivery_target(row):
    return SelectedStore(
        row.server_id,
        row.database,
        Store(row.store),
        row.schema_sha256,
        row.payload_sha256,
    )


def claim_dispatch(token: SourceIntentToken, target: SelectedStore) -> DispatchTicket:
    """Commit UNCERTAIN before touching transport. A claim is single-use.

    Even a crash-before-socket claim cannot be blindly retried. A late original
    send could otherwise become visible after a newer retry closes the intent.
    """
    _target(target)
    with _locked(token.scope):
        _, intent = _intent(token)
        row = (
            _rows(Delivery)
            .select_for_update()
            .filter(
                intent=intent,
                server_id=target.server_id,
                database=target.database,
                store=target.store.value,
            )
            .first()
        )
        if row is None or _delivery_target(row) != target:
            raise SourceConflict("unregistered delivery target")
        if row.state != Delivery.State.PREPARED or intent.complete:
            raise SourceUnavailable(
                "delivery already claimed; exact terminal acknowledgement required"
            )
        row.state = Delivery.State.UNCERTAIN
        row.save(using=DEFAULT_DB_ALIAS, update_fields=("state",))
        return DispatchTicket(token, row.receipt_id, target)


def acknowledge_selected_server(
    token: SourceIntentToken, receipts: tuple[SelectedServerReceipt, ...]
) -> SourceProgress:
    """Record exact terminal local receipts, then close only a bounded contiguous prefix.

    The caller is a trusted internal delivery adapter. A receipt is never a
    public API authorization token. Replica/helper producers must independently
    supply the selected target's evidence; a local insert cannot ack a replica.
    """
    if type(receipts) is not tuple or not 1 <= len(receipts) <= MAX_TARGETS:
        raise SourceConflict("bounded receipt tuple required")
    with _locked(token.scope):
        state, intent = _intent(token)
        for receipt in receipts:
            if (
                type(receipt) is not SelectedServerReceipt
                or type(receipt.ticket) is not DispatchTicket
                or receipt.ticket.token != token
                or receipt.kind is not ReceiptKind.LOCAL_SYNCHRONOUS_INSERT
                or type(receipt.query_id) is not str
                or receipt.query_id != str(receipt.ticket.receipt_id)
            ):
                raise SourceConflict("malformed or unrelated terminal receipt")
            row = (
                _rows(Delivery)
                .select_for_update()
                .filter(intent=intent, receipt_id=receipt.ticket.receipt_id)
                .first()
            )
            if row is None or _delivery_target(row) != receipt.ticket.target:
                raise SourceConflict("receipt does not match selected store")
            digest = sha256(canonical_bytes(asdict(receipt)))
            if row.state == Delivery.State.PREPARED:
                raise SourceConflict("acknowledgement before dispatch")
            if (
                row.state == Delivery.State.ACKNOWLEDGED
                and row.acknowledgement_sha256 != digest
            ):
                raise SourceConflict("conflicting terminal receipt")
            row.state, row.acknowledgement_sha256 = Delivery.State.ACKNOWLEDGED, digest
            row.save(
                using=DEFAULT_DB_ALIAS,
                update_fields=("state", "acknowledgement_sha256"),
            )
        if (
            _rows(Delivery)
            .filter(intent=intent, state=Delivery.State.ACKNOWLEDGED)
            .count()
            == intent.target_count
        ):
            intent.complete = True
            intent.save(using=DEFAULT_DB_ALIAS, update_fields=("complete",))
        _advance(state)
        return _progress(state)


def _advance(state):
    # Never MAX(revision). Missing/uncertain operations stop the prefix, including
    # a reserved sequence whose creating transaction has not committed.
    rows = (
        _rows(Intent)
        .filter(scope=state, revision__gt=state.closed_revision)
        .order_by("revision")[:MAX_CLOSE]
    )
    for row in rows:
        if row.revision != state.closed_revision + 1 or not row.complete:
            break
        state.closed_revision = row.revision
    state.save(using=DEFAULT_DB_ALIAS, update_fields=("closed_revision",))


def _progress(state):
    return SourceProgress(
        SourceScope(state.organization_id, state.project_id),
        state.epoch,
        state.revision,
        state.closed_revision,
        ProgressState.SETTLED
        if state.revision == state.closed_revision
        else ProgressState.PENDING,
    )


def source_progress(scope: SourceScope) -> SourceProgress | None:
    """Fresh primary progress only. None/SETTLED never qualifies a Sessions read."""
    with _locked(scope):
        state = _state(scope)
        return None if state is None else _progress(state)


def close_contiguous_source_intents(scope: SourceScope) -> SourceProgress | None:
    """Bounded continuation when an acknowledgement unlocked >500 operations."""
    with _locked(scope):
        state = _state(scope)
        if state is None:
            return None
        _advance(state)
        return _progress(state)


def read_source_page(token: SourceIntentToken, ordinal: int) -> bytes:
    """PERF2 seam: one immutable bounded nomination/preimage page, not a writer."""
    if type(ordinal) is not int or not 0 <= ordinal < MAX_PAGES:
        raise SourceConflict("invalid page ordinal")
    with _locked(token.scope):
        _, intent = _intent(token)
        page = _rows(Page).filter(intent=intent, ordinal=ordinal).first()
        if page is None or sha256(bytes(page.payload)) != page.sha256:
            raise SourceConflict("missing or damaged source page")
        return bytes(page.payload)
