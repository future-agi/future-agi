"""Deployment-bound activation control over immutable qualified catalog builds.

``property_catalog_activations`` remains the immutable lifecycle/qualification
ledger.  This module never writes it and never reuses its activation sequence.
Operational selection, disable, and rollback actions are instead recorded as
one-row append-only events in ``property_catalog_activation_control_events``.

The control ledger is deliberately fail-closed.  Every event has a contiguous
control sequence and SHA-256 predecessor chain.  Exact duplicate physical rows
are tolerated, but a fork, gap, stale predecessor, request-id reuse, or digest
mismatch makes both writers and production readers reject the ledger.  A
DISABLE head selects no lifecycle activation, so readers cannot fall back to an
older qualified build.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import Any, Protocol, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid5

from tfc.settings.settings import validate_property_catalog_database

from .codec import canonical_uuid, framed_sha256, require_sha256
from .runtime_limits import RUNTIME_LIMITS
from .wire import ZERO_SHA256

ACTIVATION_CONTROL_TABLE = "property_catalog_activation_control_events"
ACTIVATION_CONTROL_MAX_EVENTS = 4096
ACTIVATION_CONTROL_MAX_QUALIFIED_BUILDS = RUNTIME_LIMITS.max_lineage_revisions * 8

ACTIVATION_CONTROL_COLUMNS = (
    "organization_id",
    "workspace_id",
    "catalog_epoch",
    "projection_version",
    "control_sequence",
    "request_id",
    "action",
    "target_catalog_revision",
    "target_build_token",
    "target_activation_sha256",
    "previous_control_sha256",
    "control_sha256",
    "controlled_at",
)

_CONTROL_READ_SETTINGS = RUNTIME_LIMITS.clickhouse_read_settings
_T = TypeVar("_T")


class ActivationControlError(RuntimeError):
    """Base class for deterministic activation-control failures."""


class ActivationControlRejected(ActivationControlError):
    """An explicit control request is stale, conflicting, or unauthorized."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"property catalog activation control rejected: {reason}")


class ActivationControlUnavailable(ActivationControlError):
    """Sanitized fail-closed signal used by production readers."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("The property catalog activation control is unavailable.")


class ActivationControlBootstrapPending(ActivationControlUnavailable):
    """Automatic onboarding has no completed reader selection yet.

    A first, prepared INITIAL FOLLOW may precede lifecycle publication. This
    first-page-only signal grants no data access and never ignores a DISABLE.
    """


class ActivationControlAction(StrEnum):
    ACTIVATE = "activate"
    DISABLE = "disable"
    ROLLBACK = "rollback"
    FOLLOW = "follow"


@dataclass(frozen=True, slots=True)
class ActivationControlScope:
    organization_id: str
    workspace_id: str

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


@dataclass(frozen=True, slots=True)
class ActivationControlTarget:
    """Exact immutable lifecycle activation selected by a control event."""

    organization_id: str
    workspace_id: str
    catalog_epoch: int
    projection_version: int
    catalog_revision: int
    build_token: str
    activation_sha256: str

    def __post_init__(self) -> None:
        scope = ActivationControlScope(
            self.organization_id,
            self.workspace_id,
        )
        object.__setattr__(self, "organization_id", scope.organization_id)
        object.__setattr__(self, "workspace_id", scope.workspace_id)
        _strict_positive_uint(
            self.catalog_epoch,
            field="catalog_epoch",
            bits=16,
        )
        _strict_positive_uint(
            self.projection_version,
            field="projection_version",
            bits=16,
        )
        _strict_positive_uint(
            self.catalog_revision,
            field="catalog_revision",
            bits=64,
        )
        object.__setattr__(
            self,
            "build_token",
            canonical_uuid(self.build_token, field="build_token"),
        )
        require_sha256(self.activation_sha256, field="activation_sha256")

    @property
    def scope(self) -> ActivationControlScope:
        return ActivationControlScope(
            self.organization_id,
            self.workspace_id,
        )


@dataclass(frozen=True, slots=True)
class QualifiedActivation:
    """Minimal immutable evidence read from property_catalog_activations."""

    target: ActivationControlTarget
    lifecycle_activation_sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.target, ActivationControlTarget):
            raise TypeError("target must be an ActivationControlTarget")
        _strict_positive_uint(
            self.lifecycle_activation_sequence,
            field="lifecycle_activation_sequence",
            bits=64,
        )

    @property
    def lifecycle_position(self) -> tuple[int, int]:
        """Order lifecycle generations without consuming control sequences."""

        return self.target.catalog_epoch, self.lifecycle_activation_sequence


@dataclass(frozen=True, slots=True)
class ActivationControlHead:
    """Exact optimistic-concurrency token for the independent control ledger."""

    control_sequence: int
    request_id: str
    action: ActivationControlAction
    target: ActivationControlTarget
    control_sha256: str

    def __post_init__(self) -> None:
        _strict_positive_uint(
            self.control_sequence,
            field="control_sequence",
            bits=64,
        )
        object.__setattr__(
            self,
            "request_id",
            canonical_uuid(self.request_id, field="request_id"),
        )
        if not isinstance(self.action, ActivationControlAction):
            raise TypeError("action must be an ActivationControlAction")
        if not isinstance(self.target, ActivationControlTarget):
            raise TypeError("target must be an ActivationControlTarget")
        require_sha256(self.control_sha256, field="control_sha256")


@dataclass(frozen=True, slots=True)
class ActivationControlEvent:
    """One immutable physical row in the append-only control ledger."""

    control_sequence: int
    request_id: str
    action: ActivationControlAction
    target: ActivationControlTarget
    previous_control_sha256: str
    controlled_at: datetime
    control_sha256: str

    def __post_init__(self) -> None:
        _strict_positive_uint(
            self.control_sequence,
            field="control_sequence",
            bits=64,
        )
        object.__setattr__(
            self,
            "request_id",
            canonical_uuid(self.request_id, field="request_id"),
        )
        if not isinstance(self.action, ActivationControlAction):
            raise TypeError("action must be an ActivationControlAction")
        if not isinstance(self.target, ActivationControlTarget):
            raise TypeError("target must be an ActivationControlTarget")
        require_sha256(
            self.previous_control_sha256,
            field="previous_control_sha256",
        )
        _require_utc(self.controlled_at, field="controlled_at")
        require_sha256(self.control_sha256, field="control_sha256")
        if self.control_sha256 != self.expected_sha256:
            raise ValueError("control_sha256 does not match the event fields")

    @classmethod
    def create(
        cls,
        *,
        control_sequence: int,
        request_id: str,
        action: ActivationControlAction,
        target: ActivationControlTarget,
        previous_control_sha256: str,
        controlled_at: datetime,
    ) -> ActivationControlEvent:
        digest = _control_sha256(
            control_sequence=control_sequence,
            request_id=request_id,
            action=action,
            target=target,
            previous_control_sha256=previous_control_sha256,
            controlled_at=controlled_at,
        )
        return cls(
            control_sequence=control_sequence,
            request_id=request_id,
            action=action,
            target=target,
            previous_control_sha256=previous_control_sha256,
            controlled_at=controlled_at,
            control_sha256=digest,
        )

    @property
    def expected_sha256(self) -> str:
        return _control_sha256(
            control_sequence=self.control_sequence,
            request_id=self.request_id,
            action=self.action,
            target=self.target,
            previous_control_sha256=self.previous_control_sha256,
            controlled_at=self.controlled_at,
        )

    @property
    def head(self) -> ActivationControlHead:
        return ActivationControlHead(
            control_sequence=self.control_sequence,
            request_id=self.request_id,
            action=self.action,
            target=self.target,
            control_sha256=self.control_sha256,
        )

    def as_row(self) -> dict[str, Any]:
        return {
            "organization_id": self.target.organization_id,
            "workspace_id": self.target.workspace_id,
            "catalog_epoch": self.target.catalog_epoch,
            "projection_version": self.target.projection_version,
            "control_sequence": self.control_sequence,
            "request_id": self.request_id,
            "action": self.action.value,
            "target_catalog_revision": self.target.catalog_revision,
            "target_build_token": self.target.build_token,
            "target_activation_sha256": self.target.activation_sha256,
            "previous_control_sha256": self.previous_control_sha256,
            "control_sha256": self.control_sha256,
            "controlled_at": self.controlled_at,
        }


@dataclass(frozen=True, slots=True)
class ActivationControlRequest:
    request_id: str
    target: ActivationControlTarget
    expected_head: ActivationControlHead | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "request_id",
            canonical_uuid(self.request_id, field="request_id"),
        )
        if not isinstance(self.target, ActivationControlTarget):
            raise TypeError("target must be an ActivationControlTarget")
        if self.expected_head is not None and not isinstance(
            self.expected_head,
            ActivationControlHead,
        ):
            raise TypeError("expected_head must be an ActivationControlHead or None")


@dataclass(frozen=True, slots=True)
class ActivationControlResult:
    event: ActivationControlEvent
    selected_target: ActivationControlTarget | None
    idempotent: bool


class ActivationControlStore(Protocol):
    """Persistence required by the production activation control plane."""

    def list_qualified_activations(
        self,
        scope: ActivationControlScope,
    ) -> Sequence[QualifiedActivation]: ...

    def list_control_events(
        self,
        scope: ActivationControlScope,
    ) -> Sequence[ActivationControlEvent]: ...

    def append_control_event(
        self,
        event: ActivationControlEvent,
        *,
        expected_head: ActivationControlHead | None,
    ) -> ActivationControlEvent: ...

    def confirm_control_event(self, event: ActivationControlEvent) -> None: ...


class ActivationControlCatalogClient(Protocol):
    """Minimal dedicated production write client used by the concrete store."""

    catalog_database: str

    @property
    def receipt_confirmation_available(self) -> bool: ...

    def query(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int,
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

    def confirm_receipt(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None: ...


class ActivationControlAppendCoordinator(Protocol):
    """Shared writer lock and crash-persistent exact append intent."""

    def serialize(
        self, scope: ActivationControlScope, operation: Callable[[], _T]
    ) -> _T: ...

    def pending(
        self, scope: ActivationControlScope
    ) -> ActivationControlEvent | None: ...

    def verify_history(
        self, scope: ActivationControlScope, events: Sequence[ActivationControlEvent]
    ) -> None: ...

    def prepare(self, event: ActivationControlEvent) -> None: ...

    def complete(self, event: ActivationControlEvent) -> None: ...


class ActivationControlQueryExecutor(Protocol):
    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> Any: ...


class ActivationControlSelector(Protocol):
    def select_target(
        self,
        *,
        scope: Mapping[str, Any],
        timeout_ms: int,
    ) -> ActivationControlTarget: ...


@dataclass(frozen=True, slots=True)
class ReaderActivationSelection:
    """Target and advancement intent from the same validated control read.

    A pinned ACTIVATE or ROLLBACK must not promise automatic scope catch-up.
    This is backend-only selection metadata, not part of a public cursor.
    """

    target: ActivationControlTarget
    follows_latest: bool = False
    follow_anchor: ActivationControlTarget | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, ActivationControlTarget):
            raise TypeError("reader selection requires an activation target")
        if type(self.follows_latest) is not bool:
            raise TypeError("reader selection advancement must be boolean")
        if self.follow_anchor is not None:
            anchor = self.follow_anchor
            if not isinstance(anchor, ActivationControlTarget):
                raise TypeError("reader selection anchor requires an activation target")
            if (
                not self.follows_latest
                or anchor.scope != self.target.scope
                or anchor.catalog_epoch != self.target.catalog_epoch
                or anchor.projection_version != self.target.projection_version
                or anchor.catalog_revision > self.target.catalog_revision
                or (
                    anchor.catalog_revision == self.target.catalog_revision
                    and anchor != self.target
                )
            ):
                raise ValueError("reader selection anchor must bound the same FOLLOW")

    def allows_previous_revision(self, *, epoch: int, revision: int) -> bool:
        """Bound a verified cursor to the currently authorized FOLLOW history.

        This only admits an exact historical lookup. The reader must still
        validate that build's qualification, lineage, scope and signed cursor
        fingerprint. A legacy target-only selector cannot authorize history.
        """
        return (
            self.follow_anchor is not None
            and type(epoch) is int
            and type(revision) is int
            and epoch == self.target.catalog_epoch
            and self.follow_anchor.catalog_revision
            <= revision
            < self.target.catalog_revision
        )


def select_reader_activation(
    selector: ActivationControlSelector,
    *,
    scope: Mapping[str, Any],
    timeout_ms: int,
) -> ReaderActivationSelection:
    select_for_read = getattr(selector, "select_for_read", None)
    if callable(select_for_read):
        selection = select_for_read(scope=scope, timeout_ms=timeout_ms)
        if not isinstance(selection, ReaderActivationSelection):
            raise ActivationControlUnavailable("control_selection_invalid")
        return selection
    # Older/custom selectors supply only a pinned target. Never infer FOLLOW
    # from the mere presence of a selector or an environment setting.
    return ReaderActivationSelection(
        selector.select_target(scope=scope, timeout_ms=timeout_ms)
    )


class PropertyCatalogActivationControlPlane:
    """Append activate/disable/rollback events without mutating lifecycle state."""

    def __init__(self, store: ActivationControlStore) -> None:
        self._store = store

    def activate(
        self,
        *,
        request: ActivationControlRequest,
        now: datetime,
    ) -> ActivationControlResult:
        """Select the newest exact qualified lifecycle activation."""

        return self._apply(
            action=ActivationControlAction.ACTIVATE,
            request=request,
            now=now,
        )

    def disable(
        self,
        *,
        request: ActivationControlRequest,
        now: datetime,
    ) -> ActivationControlResult:
        """Append DISABLE; the resulting state deliberately has no fallback."""

        return self._apply(
            action=ActivationControlAction.DISABLE,
            request=request,
            now=now,
        )

    def rollback(
        self,
        *,
        request: ActivationControlRequest,
        now: datetime,
    ) -> ActivationControlResult:
        """Select an exact prior target that still has qualified lifecycle proof."""

        return self._apply(
            action=ActivationControlAction.ROLLBACK,
            request=request,
            now=now,
        )

    def _apply(
        self,
        *,
        action: ActivationControlAction,
        request: ActivationControlRequest,
        now: datetime,
    ) -> ActivationControlResult:
        _require_utc(now, field="now")
        coordinator = (
            self._store._append_coordinator
            if isinstance(self._store, ClickHouseActivationControlStore)
            else None
        )
        if coordinator is not None:
            return coordinator.serialize(
                request.target.scope,
                lambda: self._apply_serialized(
                    action=action, request=request, now=now, coordinator=coordinator
                ),
            )
        return self._apply_serialized(action=action, request=request, now=now)

    def _apply_serialized(
        self,
        *,
        action: ActivationControlAction,
        request: ActivationControlRequest,
        now: datetime,
        coordinator: ActivationControlAppendCoordinator | None = None,
    ) -> ActivationControlResult:
        scope = request.target.scope
        events = canonical_control_events(
            self._store.list_control_events(scope),
            scope=scope,
        )
        if coordinator is not None:
            # An old idempotent request must not report a stale selection just
            # because its own receipt is settled. Keep the durable head check,
            # exact-event proofs, and result under the same reader-control lock.
            coordinator.verify_history(scope, events)
        replay = _find_exact_replay(
            events,
            action=action,
            request=request,
        )
        if replay is not None:
            pending = coordinator.pending(scope) if coordinator is not None else None
            confirmations = [replay]
            if pending is not None:
                observed = next(
                    (item for item in events if item.request_id == pending.request_id),
                    None,
                )
                if observed is None:
                    raise ActivationControlRejected("control_append_uncertain")
                if observed != pending:
                    raise ActivationControlRejected("control_request_id_conflict")
                if pending not in confirmations:
                    confirmations.append(pending)
            if events[-1] not in confirmations:
                confirmations.append(events[-1])
            for event in confirmations:
                self._store.confirm_control_event(event)
            if pending is not None:
                coordinator.complete(pending)
            return ActivationControlResult(
                event=replay,
                selected_target=selected_control_target(events),
                idempotent=True,
            )

        actual_head = events[-1].head if events else None
        if actual_head != request.expected_head:
            raise ActivationControlRejected("control_stale")

        if (
            action is ActivationControlAction.DISABLE
            and isinstance(self._store, ClickHouseActivationControlStore)
            and is_initial_follow(events, database=self._store.database)
            and selected_control_target(events) == request.target
            and not self._store.has_lifecycle_history(scope)
        ):
            # A prepared INITIAL can be stopped before ACTIVE exists. This
            # exception can only revoke selection, never activate or roll back.
            return self._append(action=action, request=request, now=now, events=events)

        qualified = canonical_qualified_activations(
            self._store.list_qualified_activations(scope),
            scope=scope,
        )
        by_target = {item.target: item for item in qualified}
        requested = by_target.get(request.target)
        if requested is None:
            raise ActivationControlRejected("target_not_qualified")

        current_target = selected_control_target(events)
        disabled_initial = (
            action is ActivationControlAction.ACTIVATE
            and actual_head is not None
            and actual_head.action is ActivationControlAction.DISABLE
            and isinstance(self._store, ClickHouseActivationControlStore)
            and is_initial_follow(events[:-1], database=self._store.database)
            and actual_head.target == events[-2].target
        )
        # Explicit re-enable may select a qualified replacement of an INITIAL
        # disabled before it ever became ACTIVE. The failed target is not proof.
        baseline = (
            None
            if disabled_initial
            else _head_qualified_activation(actual_head, by_target=by_target)
        )
        if (
            actual_head is not None
            and actual_head.action is ActivationControlAction.FOLLOW
        ):
            current_target = follow_qualified_target(
                qualified, anchor=actual_head.target
            )
            baseline = by_target[current_target]
        if action is ActivationControlAction.ACTIVATE:
            if request.target != qualified[-1].target:
                raise ActivationControlRejected("activate_target_not_latest")
            if current_target == request.target:
                raise ActivationControlRejected("activate_target_already_selected")
            if (
                actual_head is not None
                and actual_head.action is not ActivationControlAction.DISABLE
                and baseline is not None
                and requested.lifecycle_position <= baseline.lifecycle_position
            ):
                raise ActivationControlRejected("activate_target_not_newer")
        elif action is ActivationControlAction.DISABLE:
            if actual_head is None or current_target != request.target:
                raise ActivationControlRejected("disable_target_not_selected")
        else:
            if actual_head is None or baseline is None:
                raise ActivationControlRejected("rollback_requires_control_head")
            if requested.lifecycle_position >= baseline.lifecycle_position:
                raise ActivationControlRejected("rollback_target_not_prior")

        return self._append(action=action, request=request, now=now, events=events)

    def _append(
        self,
        *,
        action: ActivationControlAction,
        request: ActivationControlRequest,
        now: datetime,
        events: tuple[ActivationControlEvent, ...],
    ) -> ActivationControlResult:
        actual_head = events[-1].head if events else None
        previous_sha256 = (
            ZERO_SHA256 if actual_head is None else actual_head.control_sha256
        )
        event = ActivationControlEvent.create(
            control_sequence=1
            if actual_head is None
            else actual_head.control_sequence + 1,
            request_id=request.request_id,
            action=action,
            target=request.target,
            previous_control_sha256=previous_sha256,
            controlled_at=now,
        )
        appended = self._store.append_control_event(
            event,
            expected_head=request.expected_head,
        )
        if appended != event:
            raise ActivationControlRejected("control_append_not_exact")
        post_events = canonical_control_events(
            (*events, appended), scope=request.target.scope
        )
        return ActivationControlResult(
            event=appended,
            selected_target=selected_control_target(post_events),
            idempotent=False,
        )


class ClickHouseActivationControlStore:
    """Concrete append-only ClickHouse persistence with post-write fork proof.

    ClickHouse does not provide a row-level compare-and-swap primitive.  The
    exact predecessor check is therefore repeated before and after the insert.
    Managed writers supply the same append coordinator to serialize that check
    and persist exact uncertain-write retries. Uncoordinated legacy writers
    remain detectable as fail-closed forks; they must not run alongside managed
    writers without adopting the shared coordinator.
    """

    def __init__(
        self,
        client: ActivationControlCatalogClient,
        *,
        database: str,
        timeout_ms: int | None = None,
        append_coordinator: ActivationControlAppendCoordinator | None = None,
        deployment: str = "prod",
    ) -> None:
        self._database = validate_property_catalog_database(
            database,
            deployment=deployment,
        )
        self._deployment = deployment
        if getattr(client, "catalog_database", None) != self._database:
            raise ValueError("activation-control client/database binding mismatch")
        if timeout_ms is None:
            timeout_ms = RUNTIME_LIMITS.state_store_timeout_ms
        if type(timeout_ms) is not int or not (
            1 <= timeout_ms <= RUNTIME_LIMITS.state_store_timeout_ms
        ):
            raise ValueError("activation-control timeout is outside its safe bound")
        self._client = client
        self._timeout_ms = timeout_ms
        self._append_coordinator = append_coordinator

    @property
    def database(self) -> str:
        return self._database

    def qualified_for_follow(
        self,
        scope: ActivationControlScope,
        *,
        catalog_epoch: int,
        anchor_revision: int = 0,
    ) -> tuple[QualifiedActivation, ...]:
        """Read the exact anchor and two newest builds, not lifetime history."""
        rows = self._client.query(
            qualified_activation_sql(
                self._database,
                deployment=self._deployment,
                follow=True,
            ),
            {
                **_scope_params(scope),
                "catalog_follow_epoch": catalog_epoch,
                "catalog_follow_anchor": anchor_revision,
            },
            timeout_ms=self._timeout_ms,
        )
        if len(rows) > 3:
            raise ActivationControlRejected("qualified_follow_result_limit")
        return canonical_qualified_activations(
            tuple(_qualified_from_row(row) for row in rows),
            scope=scope,
        )

    def list_qualified_activations(
        self,
        scope: ActivationControlScope,
    ) -> tuple[QualifiedActivation, ...]:
        rows = self._client.query(
            qualified_activation_sql(self._database, deployment=self._deployment),
            _scope_params(scope),
            timeout_ms=self._timeout_ms,
        )
        if len(rows) > ACTIVATION_CONTROL_MAX_QUALIFIED_BUILDS:
            raise ActivationControlRejected("qualified_history_limit")
        return canonical_qualified_activations(
            tuple(_qualified_from_row(row) for row in rows),
            scope=scope,
        )

    def list_control_events(
        self,
        scope: ActivationControlScope,
    ) -> tuple[ActivationControlEvent, ...]:
        rows = self._client.query(
            activation_control_event_sql(self._database, deployment=self._deployment),
            {
                **_scope_params(scope),
                "catalog_control_result_limit": ACTIVATION_CONTROL_MAX_EVENTS + 1,
            },
            timeout_ms=self._timeout_ms,
        )
        if len(rows) > ACTIVATION_CONTROL_MAX_EVENTS:
            raise ActivationControlRejected("control_history_limit")
        return canonical_control_events(
            tuple(_event_from_row(row) for row in rows),
            scope=scope,
        )

    def has_lifecycle_history(self, scope: ActivationControlScope) -> bool:
        return _lifecycle_history_exists(
            self._client.query(
                activation_history_sql(self._database, deployment=self._deployment),
                _scope_params(scope),
                timeout_ms=self._timeout_ms,
            )
        )

    def append_control_event(
        self,
        event: ActivationControlEvent,
        *,
        expected_head: ActivationControlHead | None,
    ) -> ActivationControlEvent:
        if self._append_coordinator is not None:
            return self._append_coordinator.serialize(
                event.target.scope,
                lambda: self._append_control_event(event, expected_head=expected_head),
            )
        return self._append_control_event(event, expected_head=expected_head)

    def _require_receipt_confirmation(self) -> None:
        if getattr(
            self._client, "receipt_confirmation_available", False
        ) is not True or not callable(getattr(self._client, "confirm_receipt", None)):
            raise ActivationControlRejected("control_native_receipt_required")

    def confirm_control_event(self, event: ActivationControlEvent) -> None:
        """Prove this exact event, never publish or recover a different build.

        Reader-control coordination precedes the native event-scope lock. In
        particular, INITIAL FOLLOW confirmation must not drain a Prepared ACTIVE
        from that build: the lifecycle publisher still owns that authorization.
        """
        if not isinstance(event, ActivationControlEvent):
            raise TypeError("control confirmation requires an exact event")

        def confirm() -> None:
            self._require_receipt_confirmation()
            self._client.confirm_receipt(
                f"`{self._database}`.`{ACTIVATION_CONTROL_TABLE}`",
                (event.as_row(),),
                columns=ACTIVATION_CONTROL_COLUMNS,
                timeout_ms=self._timeout_ms,
                deduplication_token=_control_event_token(event),
            )

        if self._append_coordinator is not None:
            self._append_coordinator.serialize(event.target.scope, confirm)
        else:
            confirm()

    def _append_control_event(
        self,
        event: ActivationControlEvent,
        *,
        expected_head: ActivationControlHead | None,
    ) -> ActivationControlEvent:
        # Reject a missing proof transport before persisting an intent or
        # dispatching an INSERT. Read-only status/selection needs no journal.
        self._require_receipt_confirmation()
        before = self.list_control_events(event.target.scope)
        coordinator = self._append_coordinator
        if coordinator is not None:
            coordinator.verify_history(event.target.scope, before)
        pending = coordinator.pending(event.target.scope) if coordinator else None
        confirmed: list[ActivationControlEvent] = []
        if pending is not None:
            observed = next(
                (item for item in before if item.request_id == pending.request_id), None
            )
            if observed is not None:
                if observed != pending:
                    raise ActivationControlRejected("control_request_id_conflict")
                self.confirm_control_event(pending)
                confirmed.append(pending)
                if before[-1] != pending:
                    self.confirm_control_event(before[-1])
                    confirmed.append(before[-1])
                coordinator.complete(pending)
            elif pending != event:
                raise ActivationControlRejected("control_append_uncertain")
        exact_replay = tuple(
            item for item in before if item.request_id == event.request_id
        )
        if exact_replay:
            if len(exact_replay) == 1 and exact_replay[0] == event:
                if event not in confirmed:
                    self.confirm_control_event(event)
                return event
            raise ActivationControlRejected("control_request_id_conflict")
        actual_head = before[-1].head if before else None
        if actual_head != expected_head:
            raise ActivationControlRejected("control_concurrent")
        if event.control_sequence != (
            1 if actual_head is None else actual_head.control_sequence + 1
        ) or event.previous_control_sha256 != (
            ZERO_SHA256 if actual_head is None else actual_head.control_sha256
        ):
            raise ActivationControlRejected("control_stale")
        if len(before) >= ACTIVATION_CONTROL_MAX_EVENTS:
            raise ActivationControlRejected("control_history_limit")

        if before and before[-1] not in confirmed:
            self.confirm_control_event(before[-1])

        row = event.as_row()
        if coordinator is not None:
            coordinator.prepare(event)
        self._client.insert(
            f"`{self._database}`.`{ACTIVATION_CONTROL_TABLE}`",
            (row,),
            columns=ACTIVATION_CONTROL_COLUMNS,
            timeout_ms=self._timeout_ms,
            deduplication_token=_control_event_token(event),
        )
        after = self.list_control_events(event.target.scope)
        persisted = tuple(item for item in after if item.request_id == event.request_id)
        if len(persisted) != 1 or persisted[0] != event:
            raise ActivationControlRejected("control_append_not_exact")
        if after[-1] != event:
            raise ActivationControlRejected("control_concurrent")
        self.confirm_control_event(event)
        if coordinator is not None:
            coordinator.complete(event)
        return event


def _control_event_token(event: ActivationControlEvent) -> str:
    return (
        "property-catalog-activation-control-v1:"
        f"{event.request_id}:{event.control_sha256}"
    )


class ClickHouseActivationControlSelector:
    """Read-only production selector backed exclusively by the control ledger."""

    def __init__(
        self,
        executor: ActivationControlQueryExecutor,
        *,
        database: str,
        deployment: str = "prod",
        managed: bool = False,
    ) -> None:
        self._executor = executor
        self._database = validate_property_catalog_database(
            database,
            deployment=deployment,
        )
        self._deployment = deployment
        self._sql = activation_control_event_sql(self._database, deployment=deployment)
        self._follow_sql = qualified_activation_sql(
            self._database,
            deployment=deployment,
            follow=True,
        )
        if managed and deployment != "dev":
            raise ValueError("managed bootstrap selection is OSS-only")
        self._managed = managed

    def select_target(
        self,
        *,
        scope: Mapping[str, Any],
        timeout_ms: int,
    ) -> ActivationControlTarget:
        return self.select_for_read(scope=scope, timeout_ms=timeout_ms).target

    def select_for_read(
        self,
        *,
        scope: Mapping[str, Any],
        timeout_ms: int,
    ) -> ReaderActivationSelection:
        deadline = monotonic() + timeout_ms / 1_000
        try:
            checked_scope = ActivationControlScope(
                organization_id=str(scope["organization_id"]),
                workspace_id=str(scope["workspace_id"]),
            )
            events = self._read_events(checked_scope, deadline=deadline)
            if not events and self._managed:
                empty = not self._has_history(checked_scope, deadline=deadline)
                # FOLLOW may have been published after the first control read.
                # Always validate the new head, including DISABLE and corruption.
                events = self._read_events(checked_scope, deadline=deadline)
                if not events and empty:
                    raise ActivationControlBootstrapPending("control_bootstrap_pending")
            return self._select_events(events, checked_scope, deadline=deadline)
        except ActivationControlUnavailable:
            raise
        except Exception as exc:
            raise ActivationControlUnavailable("control_invalid") from exc

    def _read_events(
        self, scope: ActivationControlScope, *, deadline: float
    ) -> tuple[ActivationControlEvent, ...]:
        timeout_ms = _control_remaining_ms(deadline)
        result = self._executor.execute(
            self._sql,
            {
                **_scope_params(scope),
                "catalog_control_result_limit": ACTIVATION_CONTROL_MAX_EVENTS + 1,
            },
            timeout_ms=timeout_ms,
            settings={
                **_CONTROL_READ_SETTINGS,
                "max_result_rows": ACTIVATION_CONTROL_MAX_EVENTS + 1,
                "max_execution_time": timeout_ms / 1_000,
            },
        )
        rows = getattr(result, "data", None)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ActivationControlRejected("control_result_invalid")
        if len(rows) > ACTIVATION_CONTROL_MAX_EVENTS:
            raise ActivationControlRejected("control_history_limit")
        return canonical_control_events(
            tuple(_event_from_row(row) for row in rows), scope=scope
        )

    def _select_events(
        self,
        events: tuple[ActivationControlEvent, ...],
        scope: ActivationControlScope,
        *,
        deadline: float,
        allow_pending: bool = True,
    ) -> ReaderActivationSelection:
        target = selected_control_target(events)
        if target is None:
            raise ActivationControlUnavailable(
                "control_missing" if not events else "control_disabled"
            )
        if events[-1].action is not ActivationControlAction.FOLLOW:
            return ReaderActivationSelection(target)
        rows = self._follow_rows(target, deadline=deadline)
        if (
            not rows
            and allow_pending
            and is_initial_follow(events, database=self._database)
        ):
            # No qualified rows alone is NOT proof of onboarding: disabled,
            # malformed, other-epoch and unqualified lifecycle rows all veto it.
            empty = not self._has_history(scope, deadline=deadline)
            latest = self._read_events(scope, deadline=deadline)
            if latest != events:
                if empty and is_initial_follow(latest, database=self._database):
                    raise ActivationControlBootstrapPending("control_bootstrap_pending")
                return self._select_events(
                    latest, scope, deadline=deadline, allow_pending=False
                )
            if empty:
                raise ActivationControlBootstrapPending("control_bootstrap_pending")
            # ACTIVE may have appeared between the qualified read and history
            # probe. Retry once; existing history can never take the pending exit.
            rows = self._follow_rows(target, deadline=deadline)
        qualified = canonical_qualified_activations(
            tuple(_qualified_from_row(row) for row in rows), scope=scope
        )
        return ReaderActivationSelection(
            follow_qualified_target(qualified, anchor=target),
            follows_latest=True,
            follow_anchor=target,
        )

    def _follow_rows(
        self, target: ActivationControlTarget, *, deadline: float
    ) -> list[dict[str, Any]]:
        timeout_ms = _control_remaining_ms(deadline)
        result = self._executor.execute(
            self._follow_sql,
            {
                **_scope_params(target.scope),
                "catalog_follow_epoch": target.catalog_epoch,
                "catalog_follow_anchor": target.catalog_revision,
            },
            timeout_ms=timeout_ms,
            settings={
                **_CONTROL_READ_SETTINGS,
                "max_result_rows": 4,
                "max_execution_time": timeout_ms / 1_000,
            },
        )
        rows = getattr(result, "data", None)
        if (
            not isinstance(rows, list)
            or len(rows) > 3
            or not all(isinstance(row, dict) for row in rows)
        ):
            raise ActivationControlRejected("qualified_follow_result_invalid")
        return rows

    def _has_history(self, scope: ActivationControlScope, *, deadline: float) -> bool:
        timeout_ms = _control_remaining_ms(deadline)
        result = self._executor.execute(
            activation_history_sql(self._database, deployment=self._deployment),
            _scope_params(scope),
            timeout_ms=timeout_ms,
            settings={
                **_CONTROL_READ_SETTINGS,
                "max_result_rows": 1,
                "max_execution_time": timeout_ms / 1_000,
            },
        )
        return _lifecycle_history_exists(getattr(result, "data", None))


def _control_remaining_ms(deadline: float) -> int:
    remaining = int((deadline - monotonic()) * 1_000)
    if remaining < 1:
        raise ActivationControlUnavailable("control_deadline")
    return remaining


def activation_history_sql(database: str, *, deployment: str = "prod") -> str:
    checked = validate_property_catalog_database(database, deployment=deployment)
    return (
        f"SELECT 1 AS catalog_history_exists FROM `{checked}`.`property_catalog_activations` "
        "PREWHERE organization_id = %(catalog_organization_id)s "
        "AND workspace_id = %(catalog_workspace_id)s LIMIT 1"
    )


def _lifecycle_history_exists(rows: Any) -> bool:
    if isinstance(rows, (list, tuple)) and not rows:
        return False
    if (
        not isinstance(rows, (list, tuple))
        or len(rows) != 1
        or not isinstance(rows[0], dict)
        or set(rows[0]) != {"catalog_history_exists"}
        or type(rows[0]["catalog_history_exists"]) is not int
        or rows[0]["catalog_history_exists"] != 1
    ):
        raise ActivationControlRejected("control_bootstrap_proof_invalid")
    return True


def initial_follow_request_id(
    database: str,
    target: ActivationControlTarget,
    *,
    control_sequence: int = 1,
    previous_control_sha256: str = ZERO_SHA256,
) -> str:
    """Identify the first prepublication FOLLOW without another table or action.

    This domain distinguishes a writer-authorized INITIAL from ordinary FOLLOW.
    It is not qualification evidence: the reader still requires the exact ACTIVE
    target before any data access. Only the dedicated, attested writer appends it.
    """
    return str(
        uuid5(
            NAMESPACE_URL,
            framed_sha256(
                "futureagi.property-catalog.initial-reader-follow.v1",
                database,
                target.organization_id,
                target.workspace_id,
                control_sequence,
                previous_control_sha256,
                target.catalog_epoch,
                target.projection_version,
                target.catalog_revision,
                target.build_token,
                target.activation_sha256,
            ),
        )
    )


def is_initial_follow(
    events: Sequence[ActivationControlEvent], *, database: str
) -> bool:
    """An initial preparation chain, possibly replacing never-active attempts."""
    return (
        bool(events)
        and events[0].control_sequence == 1
        and events[0].previous_control_sha256 == ZERO_SHA256
        and all(is_initial_follow_event(event, database=database) for event in events)
        and all(
            earlier.target.catalog_epoch == later.target.catalog_epoch
            and earlier.target.projection_version == later.target.projection_version
            and earlier.target.catalog_revision < later.target.catalog_revision
            for earlier, later in zip(events, events[1:], strict=False)
        )
    )


def is_initial_follow_event(event: ActivationControlEvent, *, database: str) -> bool:
    return (
        event.action is ActivationControlAction.FOLLOW
        and event.request_id
        == initial_follow_request_id(
            database,
            event.target,
            control_sequence=event.control_sequence,
            previous_control_sha256=event.previous_control_sha256,
        )
    )


def activation_control_selector_for_deployment(
    executor: ActivationControlQueryExecutor,
    *,
    database: str,
    deployment: str | None,
    managed: bool = False,
) -> ActivationControlSelector | None:
    """Wire control selection only for explicitly admitted production reads."""

    if deployment is None or (deployment == "dev" and not managed):
        return None
    if deployment not in {"dev", "prod"}:
        raise ValueError("unsupported property catalog read deployment")
    return ClickHouseActivationControlSelector(
        executor,
        database=database,
        deployment=deployment,
        managed=managed and deployment == "dev",
    )


def activation_control_event_sql(database: str, *, deployment: str = "prod") -> str:
    checked = validate_property_catalog_database(database, deployment=deployment)
    columns = ", ".join(ACTIVATION_CONTROL_COLUMNS)
    return f"""\
SELECT {columns}
FROM `{checked}`.`{ACTIVATION_CONTROL_TABLE}`
PREWHERE organization_id = %(catalog_organization_id)s
  AND workspace_id = %(catalog_workspace_id)s
ORDER BY control_sequence ASC, request_id ASC, control_sha256 ASC
LIMIT %(catalog_control_result_limit)s
"""


def qualified_activation_sql(
    database: str,
    *,
    deployment: str = "prod",
    follow: bool = False,
) -> str:
    checked = validate_property_catalog_database(database, deployment=deployment)
    # A FOLLOW control event pins the installation and a qualified lower bound,
    # not every incremental revision. Keep that anchor and the newest two builds
    # (including ordering conflicts), independent of lifetime revision count.
    epoch_predicate = "AND catalog_epoch = %(catalog_follow_epoch)s" if follow else ""
    selection = (
        """
  AND (catalog_revision = %(catalog_follow_anchor)s OR catalog_revision IN
       (SELECT catalog_revision FROM latest
        WHERE status = 'active' AND qualified_at IS NOT NULL
        ORDER BY activation_sequence DESC, catalog_revision DESC, build_token DESC
        LIMIT 2))
"""
        if follow
        else ""
    )
    result_limit = 4 if follow else ACTIVATION_CONTROL_MAX_QUALIFIED_BUILDS + 1
    return f"""\
WITH versioned AS
(
    SELECT
        *,
        max(_version) OVER (
            PARTITION BY organization_id, workspace_id, catalog_epoch,
                         catalog_revision, build_token
        ) AS latest_version
    FROM `{checked}`.`property_catalog_activations`
    PREWHERE organization_id = %(catalog_organization_id)s
      AND workspace_id = %(catalog_workspace_id)s
      {epoch_predicate}
), latest AS
(
    SELECT
        versioned_rows.organization_id,
        versioned_rows.workspace_id,
        versioned_rows.catalog_epoch,
        versioned_rows.catalog_revision,
        versioned_rows.build_token,
        argMax(versioned_rows.projection_version, versioned_rows._version)
            AS projection_version,
        argMax(versioned_rows.activation_sequence, versioned_rows._version)
            AS activation_sequence,
        argMax(versioned_rows.activation_sha256, versioned_rows._version)
            AS activation_sha256,
        argMax(versioned_rows.status, versioned_rows._version) AS status,
        argMax(tuple(versioned_rows.qualified_at), versioned_rows._version).1
            AS qualified_at,
        uniqExactIf(
            tuple(
                versioned_rows.projection_version,
                versioned_rows.activation_sequence,
                versioned_rows.activation_sha256,
                versioned_rows.status,
                versioned_rows.qualified_at
            ),
            versioned_rows._version = versioned_rows.latest_version
        ) AS latest_variants
    FROM versioned AS versioned_rows
    GROUP BY
        versioned_rows.organization_id,
        versioned_rows.workspace_id,
        versioned_rows.catalog_epoch,
        versioned_rows.catalog_revision,
        versioned_rows.build_token
)
SELECT
    organization_id,
    workspace_id,
    catalog_epoch,
    projection_version,
    catalog_revision,
    build_token,
    activation_sequence,
    activation_sha256,
    latest_variants
FROM latest
WHERE status = 'active' AND qualified_at IS NOT NULL
{selection}
ORDER BY activation_sequence ASC, catalog_revision ASC, build_token ASC
LIMIT {result_limit}
"""


def canonical_control_events(
    events: Sequence[ActivationControlEvent],
    *,
    scope: ActivationControlScope | None = None,
) -> tuple[ActivationControlEvent, ...]:
    """Validate complete physical history without merge/state reduction."""

    by_sequence: dict[int, ActivationControlEvent] = {}
    by_request: dict[str, ActivationControlEvent] = {}
    for event in events:
        if not isinstance(event, ActivationControlEvent):
            raise TypeError("control history must contain ActivationControlEvent")
        if scope is None:
            scope = event.target.scope
        if event.target.scope != scope:
            raise ActivationControlRejected("control_scope_conflict")
        prior_sequence = by_sequence.get(event.control_sequence)
        if prior_sequence is not None:
            if prior_sequence != event:
                raise ActivationControlRejected("control_sequence_conflict")
            continue
        prior_request = by_request.get(event.request_id)
        if prior_request is not None and prior_request != event:
            raise ActivationControlRejected("control_request_id_conflict")
        by_sequence[event.control_sequence] = event
        by_request[event.request_id] = event

    ordered = tuple(by_sequence[index] for index in sorted(by_sequence))
    previous = ZERO_SHA256
    for expected_sequence, event in enumerate(ordered, start=1):
        if event.control_sequence != expected_sequence:
            raise ActivationControlRejected("control_sequence_gap")
        if event.previous_control_sha256 != previous:
            raise ActivationControlRejected("control_digest_chain_broken")
        previous = event.control_sha256
    return ordered


def canonical_qualified_activations(
    activations: Sequence[QualifiedActivation],
    *,
    scope: ActivationControlScope,
) -> tuple[QualifiedActivation, ...]:
    by_sequence: dict[tuple[int, int], QualifiedActivation] = {}
    by_revision: dict[tuple[int, int], QualifiedActivation] = {}
    by_target: dict[ActivationControlTarget, QualifiedActivation] = {}
    for activation in activations:
        if not isinstance(activation, QualifiedActivation):
            raise TypeError("qualified history must contain QualifiedActivation")
        if activation.target.scope != scope:
            raise ActivationControlRejected("qualified_scope_conflict")
        for key, index, reason in (
            (
                (
                    activation.target.catalog_epoch,
                    activation.lifecycle_activation_sequence,
                ),
                by_sequence,
                "qualified_sequence_conflict",
            ),
            (
                (
                    activation.target.catalog_epoch,
                    activation.target.catalog_revision,
                ),
                by_revision,
                "qualified_revision_conflict",
            ),
        ):
            prior = index.get(key)
            if prior is not None and prior != activation:
                raise ActivationControlRejected(reason)
            index[key] = activation
        prior_target = by_target.get(activation.target)
        if prior_target is not None and prior_target != activation:
            raise ActivationControlRejected("qualified_target_conflict")
        by_target[activation.target] = activation
    ordered = tuple(
        sorted(
            by_target.values(),
            key=lambda item: (
                item.target.catalog_epoch,
                item.lifecycle_activation_sequence,
                item.target.catalog_revision,
                item.target.build_token,
            ),
        )
    )
    if not ordered:
        raise ActivationControlRejected("qualified_activation_missing")
    if any(
        later.lifecycle_position <= earlier.lifecycle_position
        or (
            later.target.catalog_epoch == earlier.target.catalog_epoch
            and later.target.catalog_revision <= earlier.target.catalog_revision
        )
        for earlier, later in zip(ordered, ordered[1:], strict=False)
    ):
        raise ActivationControlRejected("qualified_order_invalid")
    return ordered


def selected_control_target(
    events: Sequence[ActivationControlEvent],
) -> ActivationControlTarget | None:
    ordered = canonical_control_events(events)
    if not ordered or ordered[-1].action is ActivationControlAction.DISABLE:
        return None
    return ordered[-1].target


def follow_qualified_target(
    qualified: Sequence[QualifiedActivation],
    *,
    anchor: ActivationControlTarget,
) -> ActivationControlTarget:
    """Follow compatible qualified progress; never cross an installation."""
    ordered = canonical_qualified_activations(qualified, scope=anchor.scope)
    by_target = {item.target: item for item in ordered}
    if anchor not in by_target:
        raise ActivationControlRejected("control_head_target_not_qualified")
    latest = ordered[-1]
    if (latest.target.catalog_epoch, latest.target.projection_version) != (
        anchor.catalog_epoch,
        anchor.projection_version,
    ):
        raise ActivationControlRejected("control_installation_identity_conflict")
    if latest.lifecycle_position < by_target[anchor].lifecycle_position:
        raise ActivationControlRejected("activate_target_not_newer")
    return latest.target


def _find_exact_replay(
    events: tuple[ActivationControlEvent, ...],
    *,
    action: ActivationControlAction,
    request: ActivationControlRequest,
) -> ActivationControlEvent | None:
    matches = tuple(event for event in events if event.request_id == request.request_id)
    if not matches:
        return None
    if len(matches) != 1:
        raise ActivationControlRejected("control_request_id_conflict")
    event = matches[0]
    expected_previous = (
        ZERO_SHA256
        if request.expected_head is None
        else request.expected_head.control_sha256
    )
    expected_sequence = (
        1
        if request.expected_head is None
        else request.expected_head.control_sequence + 1
    )
    if (
        event.action is not action
        or event.target != request.target
        or event.previous_control_sha256 != expected_previous
        or event.control_sequence != expected_sequence
    ):
        raise ActivationControlRejected("control_request_id_conflict")
    return event


def _head_qualified_activation(
    head: ActivationControlHead | None,
    *,
    by_target: Mapping[ActivationControlTarget, QualifiedActivation],
) -> QualifiedActivation | None:
    if head is None:
        return None
    qualified = by_target.get(head.target)
    if qualified is None:
        raise ActivationControlRejected("control_head_target_not_qualified")
    return qualified


def _event_from_row(row: Mapping[str, Any]) -> ActivationControlEvent:
    target = ActivationControlTarget(
        organization_id=_text(row.get("organization_id"), field="organization_id"),
        workspace_id=_text(row.get("workspace_id"), field="workspace_id"),
        catalog_epoch=_positive_uint(
            row.get("catalog_epoch"),
            field="catalog_epoch",
            bits=16,
        ),
        projection_version=_positive_uint(
            row.get("projection_version"),
            field="projection_version",
            bits=16,
        ),
        catalog_revision=_positive_uint(
            row.get("target_catalog_revision"),
            field="target_catalog_revision",
            bits=64,
        ),
        build_token=_text(row.get("target_build_token"), field="target_build_token"),
        activation_sha256=_text(
            row.get("target_activation_sha256"),
            field="target_activation_sha256",
        ),
    )
    try:
        action = ActivationControlAction(_text(row.get("action"), field="action"))
    except ValueError as exc:
        raise ActivationControlRejected("control_action_invalid") from exc
    return ActivationControlEvent(
        control_sequence=_positive_uint(
            row.get("control_sequence"),
            field="control_sequence",
            bits=64,
        ),
        request_id=_text(row.get("request_id"), field="request_id"),
        action=action,
        target=target,
        previous_control_sha256=_text(
            row.get("previous_control_sha256"),
            field="previous_control_sha256",
        ),
        controlled_at=_utc_datetime(row.get("controlled_at"), field="controlled_at"),
        control_sha256=_text(row.get("control_sha256"), field="control_sha256"),
    )


def _qualified_from_row(row: Mapping[str, Any]) -> QualifiedActivation:
    if (
        _positive_uint(row.get("latest_variants"), field="latest_variants", bits=64)
        != 1
    ):
        raise ActivationControlRejected("qualified_state_conflict")
    target = ActivationControlTarget(
        organization_id=_text(row.get("organization_id"), field="organization_id"),
        workspace_id=_text(row.get("workspace_id"), field="workspace_id"),
        catalog_epoch=_positive_uint(
            row.get("catalog_epoch"),
            field="catalog_epoch",
            bits=16,
        ),
        projection_version=_positive_uint(
            row.get("projection_version"),
            field="projection_version",
            bits=16,
        ),
        catalog_revision=_positive_uint(
            row.get("catalog_revision"),
            field="catalog_revision",
            bits=64,
        ),
        build_token=_text(row.get("build_token"), field="build_token"),
        activation_sha256=_text(
            row.get("activation_sha256"),
            field="activation_sha256",
        ),
    )
    return QualifiedActivation(
        target=target,
        lifecycle_activation_sequence=_positive_uint(
            row.get("activation_sequence"),
            field="activation_sequence",
            bits=64,
        ),
    )


def _scope_params(scope: ActivationControlScope) -> dict[str, Any]:
    return {
        "catalog_organization_id": scope.organization_id,
        "catalog_workspace_id": scope.workspace_id,
    }


def _control_sha256(
    *,
    control_sequence: int,
    request_id: str,
    action: ActivationControlAction,
    target: ActivationControlTarget,
    previous_control_sha256: str,
    controlled_at: datetime,
) -> str:
    _require_utc(controlled_at, field="controlled_at")
    return framed_sha256(
        "futureagi.property-catalog.activation-control.v1",
        target.organization_id,
        target.workspace_id,
        target.catalog_epoch,
        target.projection_version,
        control_sequence,
        request_id,
        action.value,
        target.catalog_revision,
        target.build_token,
        target.activation_sha256,
        previous_control_sha256,
        controlled_at.isoformat(timespec="microseconds"),
    )


def _positive_uint(value: Any, *, field: str, bits: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive UInt{bits}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive UInt{bits}") from exc
    if not 1 <= parsed < (1 << bits):
        raise ValueError(f"{field} must be a positive UInt{bits}")
    return parsed


def _strict_positive_uint(value: Any, *, field: str, bits: int) -> int:
    if type(value) is not int or not 1 <= value < (1 << bits):
        raise ValueError(f"{field} must be a positive UInt{bits}")
    return value


def _text(value: Any, *, field: str) -> str:
    if isinstance(value, UUID):
        value = str(value)
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{field} must be UTF-8 text") from exc
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty text")
    return value


def _utc_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    _require_utc(value, field=field)
    return value


def _require_utc(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")


__all__ = [
    "ACTIVATION_CONTROL_COLUMNS",
    "ACTIVATION_CONTROL_MAX_EVENTS",
    "ACTIVATION_CONTROL_TABLE",
    "ActivationControlAction",
    "ActivationControlEvent",
    "ActivationControlHead",
    "ActivationControlRejected",
    "ActivationControlRequest",
    "ActivationControlResult",
    "ActivationControlScope",
    "ActivationControlSelector",
    "ActivationControlStore",
    "ActivationControlTarget",
    "ActivationControlUnavailable",
    "ClickHouseActivationControlSelector",
    "ClickHouseActivationControlStore",
    "PropertyCatalogActivationControlPlane",
    "QualifiedActivation",
    "activation_control_event_sql",
    "activation_control_selector_for_deployment",
    "canonical_control_events",
    "canonical_qualified_activations",
    "qualified_activation_sql",
    "selected_control_target",
]
