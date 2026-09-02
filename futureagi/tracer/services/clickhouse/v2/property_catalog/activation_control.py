"""Production-only activation control over immutable qualified catalog builds.

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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from tfc.settings.settings import validate_property_catalog_database

from .codec import canonical_uuid, framed_sha256, require_sha256
from .runtime_limits import RUNTIME_LIMITS
from .wire import ZERO_SHA256

ACTIVATION_CONTROL_TABLE = "property_catalog_activation_control_events"
ACTIVATION_CONTROL_MAX_EVENTS = 4096
ACTIVATION_CONTROL_MAX_QUALIFIED_BUILDS = RUNTIME_LIMITS.max_lineage_revisions * 8

_CONTROL_COLUMNS = (
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


class ActivationControlAction(StrEnum):
    ACTIVATE = "activate"
    DISABLE = "disable"
    ROLLBACK = "rollback"


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


class ActivationControlCatalogClient(Protocol):
    """Minimal dedicated production write client used by the concrete store."""

    catalog_database: str

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
        scope = request.target.scope
        events = canonical_control_events(
            self._store.list_control_events(scope),
            scope=scope,
        )
        replay = _find_exact_replay(
            events,
            action=action,
            request=request,
        )
        if replay is not None:
            return ActivationControlResult(
                event=replay,
                selected_target=selected_control_target(events),
                idempotent=True,
            )

        actual_head = events[-1].head if events else None
        if actual_head != request.expected_head:
            raise ActivationControlRejected("control_stale")

        qualified = canonical_qualified_activations(
            self._store.list_qualified_activations(scope),
            scope=scope,
        )
        by_target = {item.target: item for item in qualified}
        requested = by_target.get(request.target)
        if requested is None:
            raise ActivationControlRejected("target_not_qualified")

        current_target = selected_control_target(events)
        baseline = _head_qualified_activation(
            actual_head,
            by_target=by_target,
        )
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
        post_events = canonical_control_events((*events, appended), scope=scope)
        return ActivationControlResult(
            event=appended,
            selected_target=selected_control_target(post_events),
            idempotent=False,
        )


class ClickHouseActivationControlStore:
    """Concrete append-only ClickHouse persistence with post-write fork proof.

    ClickHouse does not provide a row-level compare-and-swap primitive.  The
    exact predecessor check is therefore repeated before and after the insert.
    If two writers race, both rows remain immutable and the duplicate sequence
    creates a permanent, fail-closed fork; neither writer nor any production
    reader can select a build from that ledger.
    """

    def __init__(
        self,
        client: ActivationControlCatalogClient,
        *,
        database: str,
        timeout_ms: int | None = None,
    ) -> None:
        self._database = validate_property_catalog_database(
            database,
            deployment="prod",
        )
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

    def list_qualified_activations(
        self,
        scope: ActivationControlScope,
    ) -> tuple[QualifiedActivation, ...]:
        rows = self._client.query(
            qualified_activation_sql(self._database),
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
            activation_control_event_sql(self._database),
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

    def append_control_event(
        self,
        event: ActivationControlEvent,
        *,
        expected_head: ActivationControlHead | None,
    ) -> ActivationControlEvent:
        before = self.list_control_events(event.target.scope)
        exact_replay = tuple(
            item for item in before if item.request_id == event.request_id
        )
        if exact_replay:
            if len(exact_replay) == 1 and exact_replay[0] == event:
                return event
            raise ActivationControlRejected("control_request_id_conflict")
        actual_head = before[-1].head if before else None
        if actual_head != expected_head:
            raise ActivationControlRejected("control_concurrent")

        row = event.as_row()
        self._client.insert(
            f"`{self._database}`.`{ACTIVATION_CONTROL_TABLE}`",
            (row,),
            columns=_CONTROL_COLUMNS,
            timeout_ms=self._timeout_ms,
            deduplication_token=(
                "property-catalog-activation-control-v1:"
                f"{event.request_id}:{event.control_sha256}"
            ),
        )
        after = self.list_control_events(event.target.scope)
        persisted = tuple(item for item in after if item.request_id == event.request_id)
        if len(persisted) != 1 or persisted[0] != event:
            raise ActivationControlRejected("control_append_not_exact")
        if after[-1] != event:
            raise ActivationControlRejected("control_concurrent")
        return event


class ClickHouseActivationControlSelector:
    """Read-only production selector backed exclusively by the control ledger."""

    def __init__(
        self,
        executor: ActivationControlQueryExecutor,
        *,
        database: str,
    ) -> None:
        self._executor = executor
        self._database = validate_property_catalog_database(
            database,
            deployment="prod",
        )
        self._sql = activation_control_event_sql(self._database)

    def select_target(
        self,
        *,
        scope: Mapping[str, Any],
        timeout_ms: int,
    ) -> ActivationControlTarget:
        try:
            checked_scope = ActivationControlScope(
                organization_id=str(scope["organization_id"]),
                workspace_id=str(scope["workspace_id"]),
            )
            result = self._executor.execute(
                self._sql,
                {
                    **_scope_params(checked_scope),
                    "catalog_control_result_limit": (ACTIVATION_CONTROL_MAX_EVENTS + 1),
                },
                timeout_ms=timeout_ms,
                settings={
                    **_CONTROL_READ_SETTINGS,
                    "max_result_rows": ACTIVATION_CONTROL_MAX_EVENTS + 1,
                    "max_execution_time": timeout_ms / 1_000,
                },
            )
            rows = getattr(result, "data", None)
            if not isinstance(rows, list) or not all(
                isinstance(row, dict) for row in rows
            ):
                raise ActivationControlRejected("control_result_invalid")
            if len(rows) > ACTIVATION_CONTROL_MAX_EVENTS:
                raise ActivationControlRejected("control_history_limit")
            events = canonical_control_events(
                tuple(_event_from_row(row) for row in rows),
                scope=checked_scope,
            )
            target = selected_control_target(events)
            if target is None:
                reason = "control_missing" if not events else "control_disabled"
                raise ActivationControlUnavailable(reason)
            return target
        except ActivationControlUnavailable:
            raise
        except Exception as exc:
            raise ActivationControlUnavailable("control_invalid") from exc


def activation_control_selector_for_deployment(
    executor: ActivationControlQueryExecutor,
    *,
    database: str,
    deployment: str | None,
) -> ActivationControlSelector | None:
    """Wire control selection only for explicitly admitted production reads."""

    if deployment in {None, "dev"}:
        return None
    if deployment != "prod":
        raise ValueError("unsupported property catalog read deployment")
    return ClickHouseActivationControlSelector(executor, database=database)


def activation_control_event_sql(database: str) -> str:
    checked = validate_property_catalog_database(database, deployment="prod")
    columns = ", ".join(_CONTROL_COLUMNS)
    return f"""\
SELECT {columns}
FROM `{checked}`.`{ACTIVATION_CONTROL_TABLE}`
PREWHERE organization_id = %(catalog_organization_id)s
  AND workspace_id = %(catalog_workspace_id)s
ORDER BY control_sequence ASC, request_id ASC, control_sha256 ASC
LIMIT %(catalog_control_result_limit)s
"""


def qualified_activation_sql(database: str) -> str:
    checked = validate_property_catalog_database(database, deployment="prod")
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
        uniqExactIf(
            tuple(
                versioned_rows.projection_version,
                versioned_rows.activation_sequence,
                versioned_rows.activation_sha256,
                versioned_rows.status
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
WHERE status = 'active'
ORDER BY activation_sequence ASC, catalog_revision ASC, build_token ASC
LIMIT {ACTIVATION_CONTROL_MAX_QUALIFIED_BUILDS + 1}
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
