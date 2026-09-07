"""Automatic reader selection after durable lifecycle qualification.

Parent integration: call ``AutomaticReaderActivation.reconcile(scope)`` after
the runtime's verified active-state reread, and on already-active recovery.
Construct it from the persisted installation coordinates, a dedicated attested
control writer, and an authoritative workspace-eligibility callback. All manual
and automatic control writers must share the fence-volume state directory.
This service creates no lifecycle builds and never changes numeric identities.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import NAMESPACE_URL, uuid5

from .activation import ActivationRecord, CatalogLifecycleMode
from .activation_control import (
    ACTIVATION_CONTROL_TABLE,
    ActivationControlAction,
    ActivationControlEvent,
    ActivationControlRejected,
    ActivationControlScope,
    ActivationControlTarget,
    ClickHouseActivationControlStore,
    _event_from_row,
    canonical_control_events,
    canonical_qualified_activations,
    follow_qualified_target,
    initial_follow_request_id,
    is_initial_follow,
    is_initial_follow_event,
    selected_control_target,
)
from .codec import canonical_json, framed_sha256
from .mutation_lock import FileCatalogMutationSerializer
from .native_write_journal import NativeTerminalRepairReceipt
from .runtime_limits import RUNTIME_LIMITS
from .terminal_repair_intent import FrozenTerminalRepairIntent
from .wire import ZERO_SHA256

_T = TypeVar("_T")
_INTENT_FORMAT = "futureagi.property-catalog-reader-activation-intent"
_MAX_INTENT_BYTES = 16 * 1024
_ACTIVATION_TABLE = "property_catalog_activations"
_SCHEMA_SQL = """SELECT database, name, engine, create_table_query
FROM system.tables
WHERE database = %(database)s AND name IN %(tables)s
ORDER BY name
"""


class FileActivationControlCoordinator:
    """Serialize compared heads and preserve exact events across uncertainty.

    The lock key includes database and tenant, so unrelated workspaces can
    advance independently. Reentrancy is limited to this object and thread;
    separate processes still use the same filesystem lock. The dedicated
    directory must be shared by every writer of this database's control ledger.
    """

    def __init__(
        self, directory: str | Path, *, database: str, deployment: str = "prod"
    ) -> None:
        from tfc.settings.settings import validate_property_catalog_database

        self.database = validate_property_catalog_database(
            database, deployment=deployment
        )
        self.directory = Path(directory)
        if not self.directory.is_absolute() or self.directory.is_symlink():
            raise ValueError(
                "reader activation requires an absolute shared state directory"
            )
        self._serializer = FileCatalogMutationSerializer(str(self.directory))
        self._local = threading.local()

    def _key(self, scope: ActivationControlScope) -> str:
        return framed_sha256(
            "futureagi.property-catalog.activation-control-writer.v1",
            self.database,
            scope.organization_id,
            scope.workspace_id,
        )

    def serialize(
        self, scope: ActivationControlScope, operation: Callable[[], _T]
    ) -> _T:
        key = self._key(scope)
        if getattr(self._local, "key", None) == key:
            return operation()

        def run() -> _T:
            previous = getattr(self._local, "key", None)
            self._local.key = key
            try:
                return operation()
            finally:
                self._local.key = previous

        return self._serializer.serialize(key, run)

    def _path(self, scope: ActivationControlScope) -> Path:
        if getattr(self._local, "key", None) != self._key(scope):
            raise ActivationControlRejected("control_intent_requires_lock")
        return self.directory / f"reader-activation-{self._key(scope)}.json"

    def pending(self, scope: ActivationControlScope) -> ActivationControlEvent | None:
        record = self._read(scope)
        return record[1] if record is not None and record[0] == "pending" else None

    def verify_history(
        self, scope: ActivationControlScope, events: Sequence[ActivationControlEvent]
    ) -> None:
        record = self._read(scope)
        if record is None:
            return
        phase, known = record
        observed = next(
            (
                event
                for event in events
                if event.control_sequence == known.control_sequence
            ),
            None,
        )
        if observed is not None and observed != known:
            raise ActivationControlRejected("control_durable_head_conflict")
        if phase == "completed" and observed is None:
            raise ActivationControlRejected("control_durable_head_not_visible")

    def _read(
        self, scope: ActivationControlScope
    ) -> tuple[str, ActivationControlEvent] | None:
        path = self._path(scope)
        try:
            descriptor = os.open(
                path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            )
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 1 < info.st_size <= _MAX_INTENT_BYTES
            ):
                raise ActivationControlRejected("control_intent_unsafe")
            raw = source.read(_MAX_INTENT_BYTES + 1)
        try:
            document = json.loads(raw)
            if (
                set(document) != {"format", "version", "database", "phase", "event"}
                or document["format"] != _INTENT_FORMAT
                or type(document["version"]) is not int
                or document["version"] != 1
                or document["database"] != self.database
                or document["phase"] not in {"pending", "completed"}
            ):
                raise ValueError("intent format")
            event = _event_from_row(document["event"])
            if (
                event.target.scope != scope
                or self._encode(event, document["phase"]) != raw
            ):
                raise ValueError("intent scope or canonical bytes")
        except (KeyError, TypeError, ValueError) as exc:
            raise ActivationControlRejected("control_intent_invalid") from exc
        return document["phase"], event

    def _encode(self, event: ActivationControlEvent, phase: str) -> bytes:
        row = event.as_row()
        row["controlled_at"] = event.controlled_at.isoformat(timespec="microseconds")
        return (
            canonical_json(
                {
                    "format": _INTENT_FORMAT,
                    "version": 1,
                    "database": self.database,
                    "phase": phase,
                    "event": row,
                },
                max_bytes=_MAX_INTENT_BYTES - 1,
            )
            + "\n"
        ).encode()

    def prepare(self, event: ActivationControlEvent) -> None:
        pending = self.pending(event.target.scope)
        if pending is not None:
            if pending != event:
                raise ActivationControlRejected("control_append_uncertain")
            return
        self._write(event, "pending")

    def _write(self, event: ActivationControlEvent, phase: str) -> None:
        path = self._path(event.target.scope)
        raw = self._encode(event, phase)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".reader-activation-", dir=self.directory
        )
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(raw)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
            self._sync_directory()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def complete(self, event: ActivationControlEvent) -> None:
        if self.pending(event.target.scope) != event:
            raise ActivationControlRejected("control_intent_not_exact")
        # Retain a confirmed head even after clearing uncertainty. A later
        # writer must not allocate from a lagging replica's older ledger view.
        self._write(event, "completed")

    def _sync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class AutomaticReaderActivationResult:
    status: str
    selected_target: ActivationControlTarget | None
    event: ActivationControlEvent | None = None


class AutomaticReaderActivation:
    """Advance an eligible workspace, preserving operator control heads."""

    def __init__(
        self,
        client: Any,
        *,
        database: str,
        state_directory: str | Path,
        catalog_epoch: int,
        projection_version: int,
        authorize_scope: Callable[[ActivationControlScope], bool],
        deployment: str = "prod",
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if any(
            type(value) is not int or not 1 <= value <= 65535
            for value in (catalog_epoch, projection_version)
        ):
            raise ValueError(
                "reader activation requires resolved installation identity"
            )
        if not callable(authorize_scope) or not callable(
            getattr(client, "attest", None)
        ):
            raise TypeError(
                "reader activation requires scope authorization and writer attestation"
            )
        self.coordinator = FileActivationControlCoordinator(
            state_directory, database=database, deployment=deployment
        )
        self.store = ClickHouseActivationControlStore(
            client,
            database=database,
            append_coordinator=self.coordinator,
            deployment=deployment,
        )
        self._client = client
        self._deployment = deployment
        self._epoch, self._projection = catalog_epoch, projection_version
        self._authorize_scope, self._now = authorize_scope, now

    def reconcile(
        self,
        scope: ActivationControlScope,
        *,
        terminal_repairs: tuple[
            tuple[FrozenTerminalRepairIntent, NativeTerminalRepairReceipt], ...
        ] = (),
    ) -> AutomaticReaderActivationResult:
        """Reconcile using only terminal receipts just reproven by the runtime.

        With repairs, the caller retains the coordinator workspace lock across
        confirmation and this call; control coordination is the inner lock.
        These existing evidence objects add no reader journal or receipt format.
        """
        if type(terminal_repairs) is not tuple or any(
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not FrozenTerminalRepairIntent
            or type(pair[1]) is not NativeTerminalRepairReceipt
            for pair in terminal_repairs
        ):
            raise ActivationControlRejected("control_terminal_repair_evidence_invalid")
        return self.coordinator.serialize(
            scope, lambda: self._reconcile(scope, terminal_repairs=terminal_repairs)
        )

    def prepare_initial(
        self, target: ActivationControlTarget
    ) -> AutomaticReaderActivationResult:
        """Publish managed first FOLLOW before the caller appends INITIAL ACTIVE.

        The lifecycle caller must already have qualified and fenced this exact
        target. No data becomes readable until its immutable ACTIVE row exists.
        The existing coordinator retains exact bytes across pre/post-insert
        crashes; a resumed fenced INITIAL must retry this same target.
        """
        if (target.catalog_epoch, target.projection_version) != (
            self._epoch,
            self._projection,
        ):
            raise ActivationControlRejected("control_installation_identity_conflict")
        return self.coordinator.serialize(
            target.scope, lambda: self._prepare_initial(target)
        )

    def _prepare_initial(
        self, target: ActivationControlTarget
    ) -> AutomaticReaderActivationResult:
        scope = target.scope
        if self._authorize_scope(scope) is not True:
            raise ActivationControlRejected("control_initial_scope_ineligible")
        self._client.attest()
        events = self.store.list_control_events(scope)
        self.coordinator.verify_history(scope, events)
        pending = self.coordinator.pending(scope)
        confirmed = None
        if pending is not None:
            if not is_initial_follow_event(pending, database=self.coordinator.database):
                raise ActivationControlRejected("control_initial_append_uncertain")
            observed = next(
                (item for item in events if item.request_id == pending.request_id), None
            )
            if observed is not None:
                if observed != pending:
                    raise ActivationControlRejected("control_initial_append_uncertain")
                self.store.confirm_control_event(pending)
                if events[-1] != pending:
                    self.store.confirm_control_event(events[-1])
                confirmed = events[-1]
                self.coordinator.complete(pending)
            else:
                if self.store.has_lifecycle_history(scope):
                    raise ActivationControlRejected("control_initial_history_not_empty")
                # Resolve the old uncertain append with its original bytes before
                # allocating another sequence, even when the build was replaced.
                if self._authorize_scope(scope) is not True:
                    raise ActivationControlRejected("control_initial_scope_ineligible")
                self.store.append_control_event(
                    pending, expected_head=events[-1].head if events else None
                )
                confirmed = pending
                events = canonical_control_events((*events, pending), scope=scope)
        head = events[-1].head if events else None
        if events:
            if events[-1] != confirmed:
                self.store.confirm_control_event(events[-1])
            if events[-1].action in {
                ActivationControlAction.DISABLE,
                ActivationControlAction.ROLLBACK,
            }:
                return AutomaticReaderActivationResult(
                    events[-1].action.value, selected_control_target(events)
                )
            if not is_initial_follow(events, database=self.coordinator.database):
                raise ActivationControlRejected("control_initial_target_conflict")
            if head.target == target:
                return AutomaticReaderActivationResult("prepared", None, events[-1])
            if (head.target.catalog_epoch, head.target.projection_version) != (
                target.catalog_epoch,
                target.projection_version,
            ) or target.catalog_revision <= head.target.catalog_revision:
                raise ActivationControlRejected("control_initial_target_conflict")
        # Check ALL lifecycle history, not just qualified/current-installation
        # rows. Never repair a completed catalog's missing control by inventing
        # a new initial head, even if its qualified query is empty.
        if self.store.has_lifecycle_history(scope):
            raise ActivationControlRejected("control_initial_history_not_empty")
        sequence = 1 if head is None else head.control_sequence + 1
        previous = ZERO_SHA256 if head is None else head.control_sha256
        event = ActivationControlEvent.create(
            control_sequence=sequence,
            request_id=initial_follow_request_id(
                self.coordinator.database,
                target,
                control_sequence=sequence,
                previous_control_sha256=previous,
            ),
            action=ActivationControlAction.FOLLOW,
            target=target,
            previous_control_sha256=previous,
            controlled_at=self._now(),
        )
        if self._authorize_scope(scope) is not True:
            raise ActivationControlRejected("control_initial_scope_ineligible")
        appended = self.store.append_control_event(event, expected_head=head)
        return AutomaticReaderActivationResult("prepared", None, appended)

    def _reconcile(
        self, scope: ActivationControlScope, *, terminal_repairs=()
    ) -> AutomaticReaderActivationResult:
        if self._authorize_scope(scope) is not True:
            return AutomaticReaderActivationResult("ineligible", None)
        self._client.attest()
        events = canonical_control_events(
            self.store.list_control_events(scope), scope=scope
        )
        self.coordinator.verify_history(scope, events)
        head = events[-1].head if events else None
        selected = selected_control_target(events)
        pending = self.coordinator.pending(scope)
        confirmed_head = False
        if pending is not None:
            observed = next(
                (item for item in events if item.request_id == pending.request_id), None
            )
            if observed is not None:
                if observed != pending:
                    raise ActivationControlRejected("control_request_id_conflict")
                self.store.confirm_control_event(pending)
                if events[-1] != pending:
                    self.store.confirm_control_event(events[-1])
                confirmed_head = True
                self.coordinator.complete(pending)
                # An explicit action following a recovered append remains the head.
                if not is_initial_follow(events, database=self.coordinator.database):
                    return AutomaticReaderActivationResult(
                        "recovered", selected, observed
                    )
                pending = None
        if events and not confirmed_head:
            self.store.confirm_control_event(events[-1])
        if head is not None and head.action in {
            ActivationControlAction.DISABLE,
            ActivationControlAction.ROLLBACK,
        }:
            return AutomaticReaderActivationResult(head.action.value, selected)
        if pending is not None and pending.action is not ActivationControlAction.FOLLOW:
            raise ActivationControlRejected("control_manual_append_uncertain")
        repaired_anchor = self._terminal_follow_anchor(scope, head, terminal_repairs)
        try:
            qualified = canonical_qualified_activations(
                self.store.qualified_for_follow(
                    scope,
                    catalog_epoch=self._epoch,
                    anchor_revision=(
                        pending.target.catalog_revision
                        if pending
                        else head.target.catalog_revision
                        if head
                        else 0
                    ),
                ),
                scope=scope,
            )
        except ActivationControlRejected as exc:
            if (
                exc.reason == "qualified_activation_missing"
                and repaired_anchor is not None
                and pending is None
            ):
                # Check all-status conflicts even when the small qualified
                # projection is empty. A visible replacement contradicting that
                # projection is not permission to suppress its failure.
                if (
                    self._repair_follow_replacement(scope, repaired_anchor, None)
                    is None
                ):
                    return AutomaticReaderActivationResult(
                        "waiting_for_qualification", None
                    )
                raise
            if (
                exc.reason == "qualified_activation_missing"
                and self._deployment == "dev"
                and is_initial_follow(events, database=self.coordinator.database)
                and pending is None
                and not self.store.has_lifecycle_history(scope)
            ):
                return AutomaticReaderActivationResult(
                    "waiting_for_qualification", None
                )
            if (
                exc.reason == "qualified_activation_missing"
                and head is None
                and pending is None
            ):
                return AutomaticReaderActivationResult(
                    "waiting_for_qualification", None
                )
            raise
        by_target = {item.target: item for item in qualified}
        if repaired_anchor is not None and head.target in by_target:
            raise ActivationControlRejected("control_terminal_anchor_still_qualified")
        repairing = (
            head is not None
            and head.target not in by_target
            and repaired_anchor is not None
        )
        if repairing:
            replacement = self._repair_follow_replacement(
                scope, repaired_anchor, pending.target if pending is not None else None
            )
            if replacement is None:
                if pending is not None:
                    raise ActivationControlRejected("control_append_uncertain")
                return AutomaticReaderActivationResult(
                    "waiting_for_qualification", None
                )
            target = self._record_target(replacement)
            qualified = self.store.qualified_for_follow(
                scope,
                catalog_epoch=self._epoch,
                anchor_revision=target.catalog_revision,
            )
            by_target = {item.target: item for item in qualified}
            if (
                target not in by_target
                or by_target[target].lifecycle_activation_sequence
                != replacement.activation_sequence
            ):
                raise ActivationControlRejected("target_not_qualified")
            # Preserve the normal installation/order checks for later qualified
            # progress even though the terminal old anchor is no longer ACTIVE.
            follow_qualified_target(qualified, anchor=target)
        else:
            target = qualified[-1].target if pending is None else pending.target
        if (
            target.catalog_epoch != self._epoch
            or target.projection_version != self._projection
        ):
            raise ActivationControlRejected("control_installation_identity_conflict")
        if target not in by_target:
            raise ActivationControlRejected("target_not_qualified")
        if head is not None and head.target not in by_target and not repairing:
            raise ActivationControlRejected("control_head_target_not_qualified")
        if (
            pending is None
            and head is not None
            and head.action is ActivationControlAction.FOLLOW
            and not repairing
        ):
            selected = follow_qualified_target(qualified, anchor=head.target)
            return AutomaticReaderActivationResult("already_selected", selected)
        if pending is not None:
            if pending.action is not ActivationControlAction.FOLLOW:
                # Manual pending DISABLE/ROLLBACK can only be retried explicitly.
                raise ActivationControlRejected("control_manual_append_uncertain")
            event = pending
        else:
            if (
                head is not None
                and not repairing
                and by_target[target].lifecycle_position
                < by_target[head.target].lifecycle_position
            ):
                raise ActivationControlRejected("activate_target_not_newer")
            previous = ZERO_SHA256 if head is None else head.control_sha256
            request_id = str(
                uuid5(
                    NAMESPACE_URL,
                    framed_sha256(
                        "futureagi.property-catalog.automatic-reader-follow.v1",
                        self.coordinator.database,
                        scope.organization_id,
                        scope.workspace_id,
                        previous,
                        target.catalog_epoch,
                        target.projection_version,
                        target.catalog_revision,
                        target.build_token,
                        target.activation_sha256,
                    ),
                )
            )
            event = ActivationControlEvent.create(
                control_sequence=1 if head is None else head.control_sequence + 1,
                request_id=request_id,
                action=ActivationControlAction.FOLLOW,
                target=target,
                previous_control_sha256=previous,
                controlled_at=self._now(),
            )
        # Recheck authorization immediately before the durable append boundary.
        if self._authorize_scope(scope) is not True:
            return AutomaticReaderActivationResult("ineligible", selected)
        appended = self.store.append_control_event(event, expected_head=head)
        return AutomaticReaderActivationResult(
            "recovered" if pending else "activated", appended.target, appended
        )

    @staticmethod
    def _record_target(record: ActivationRecord) -> ActivationControlTarget:
        return ActivationControlTarget(
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            catalog_epoch=record.catalog_epoch,
            projection_version=record.projection_version,
            catalog_revision=record.catalog_revision,
            build_token=record.build_token,
            activation_sha256=record.activation_sha256,
        )

    def _terminal_follow_anchor(self, scope, head, terminal_repairs):
        from .publication_journal import _record

        if head is None or head.action is not ActivationControlAction.FOLLOW:
            return None
        matched = None
        for intent, receipt in terminal_repairs:
            intent = FrozenTerminalRepairIntent.decode(intent.encode())
            if (
                intent.binding != receipt.binding
                or intent.database != self.coordinator.database
                or intent.lease.organization_id != scope.organization_id
                or intent.lease.workspace_id != scope.workspace_id
                or intent.lease.catalog_epoch != self._epoch
                or intent.lease.projection_version != self._projection
            ):
                raise ActivationControlRejected(
                    "control_terminal_repair_evidence_conflict"
                )
            document = intent.publication_document
            if document is None or document["publication"] is None:
                continue  # A source-only terminal repair cannot invalidate FOLLOW.
            original = _record(document["publication"]["record"])
            if self._record_target(original) != head.target:
                continue
            if matched is not None and matched != (intent, receipt, original):
                raise ActivationControlRejected(
                    "control_terminal_repair_evidence_conflict"
                )
            matched = (intent, receipt, original)
        return None if matched is None else matched[2]

    def _repair_follow_replacement(self, scope, original, pending_target):
        from .state_store import ClickHouseCatalogStateStore

        history = ClickHouseCatalogStateStore(
            self._client,
            database=self.coordinator.database,
            serializer=self.coordinator._serializer,
        ).load_activation_history(
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            catalog_epoch=self._epoch,
        )
        candidates = []
        for record in history.active_records:
            if record.projection_version != self._projection:
                raise ActivationControlRejected(
                    "control_installation_identity_conflict"
                )
            if self._record_target(record) == self._record_target(original):
                raise ActivationControlRejected(
                    "control_terminal_anchor_still_qualified"
                )
            if (
                record.lifecycle_mode
                in {
                    CatalogLifecycleMode.INITIAL_BACKFILL,
                    CatalogLifecycleMode.FULL_REPAIR,
                }
                and record.lineage_anchor_revision == record.catalog_revision
                and record.catalog_revision > original.catalog_revision
                and record.activation_sequence > original.activation_sequence
                and record.build_token != original.build_token
                and (
                    pending_target is None
                    or self._record_target(record) == pending_target
                )
            ):
                candidates.append(record)
        return max(
            candidates, key=lambda record: record.activation_sequence, default=None
        )


def verify_reader_activation_schema(driver: Any, *, database: str) -> None:
    """Read-only exact schema proof for the control writer's two visible tables."""
    from tracer.services.clickhouse.v2 import catalog_prod_schema as schema

    statements = (
        *schema._load_canonical_statements(),
        schema._activation_control_statement(),
    )
    specs = (*schema._TABLE_SPECS, schema._ACTIVATION_CONTROL_SPEC)
    pinned = {
        statement.table: (statement, spec)
        for statement, spec in zip(statements, specs, strict=True)
        if statement.table in {_ACTIVATION_TABLE, ACTIVATION_CONTROL_TABLE}
    }
    rows, _, _ = driver.execute_read(
        _SCHEMA_SQL,
        {"database": database, "tables": tuple(sorted(pinned))},
        timeout_ms=RUNTIME_LIMITS.state_store_timeout_ms,
        settings={
            **RUNTIME_LIMITS.clickhouse_read_settings,
            "readonly": 2,
            "max_result_rows": 3,
        },
    )
    if len(rows) != len(pinned):
        raise ActivationControlRejected("control_schema_incomplete")
    seen = set()
    engines = set()
    for actual_database, name, engine, create_query in rows:
        if actual_database != database or name not in pinned or name in seen:
            raise ActivationControlRejected("control_schema_scope_conflict")
        seen.add(name)
        statement, spec = pinned[name]
        engines.add(engine.startswith("Replicated"))
        expected = statement.sql
        if engine == spec.replicated_engine:
            expected = schema._render_table(
                statement,
                spec,
                target_database=database,
                cluster="runtime_schema_verification",
                keeper_path_prefix="/clickhouse/tables",
            ).sql
        elif engine != statement.engine:
            raise ActivationControlRejected("control_schema_engine_conflict")
        options = {
            "target_database": database,
            "expected_table": name,
            "cluster": "runtime_schema_verification",
        }
        if schema._canonical_create_tokens(
            create_query, **options
        ) != schema._canonical_create_tokens(expected, **options):
            raise ActivationControlRejected("control_schema_definition_conflict")
    if len(engines) != 1:
        raise ActivationControlRejected("control_schema_engine_family_conflict")


class ReaderActivationClient:
    """Reuse the existing exact grants/provenance guard and add schema admission."""

    def __init__(
        self,
        driver: Any,
        *,
        database: str,
        user: str,
        expected_hostnames: Sequence[str],
        deployment: str = "prod",
        durable_writer: Any = None,
        source_database: str | None = None,
    ) -> None:
        from .reader_activation_client import _ActivationControlClient

        self._guarded = _ActivationControlClient(
            driver,
            database=database,
            user=user,
            expected_hostnames=expected_hostnames,
            deployment=deployment,
            durable_writer=durable_writer,
            source_database=source_database,
        )
        self._driver = driver
        self.catalog_database = self._guarded.catalog_database
        self.attest()

    def attest(self) -> None:
        self._guarded._validate_identity()
        self._guarded._attest_server_identity_and_grants()
        verify_reader_activation_schema(self._driver, database=self.catalog_database)

    def query(
        self, sql: str, params: Mapping[str, Any], *, timeout_ms: int
    ) -> Sequence[Mapping[str, Any]]:
        return self._guarded.query(sql, params, timeout_ms=timeout_ms)

    @property
    def receipt_confirmation_available(self) -> bool:
        return self._guarded.receipt_confirmation_available

    def confirm_receipt(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None:
        self.attest()
        self._guarded.confirm_receipt(
            table,
            rows,
            columns=columns,
            timeout_ms=timeout_ms,
            deduplication_token=deduplication_token,
        )

    def insert(
        self,
        table: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str],
        timeout_ms: int,
        deduplication_token: str,
    ) -> None:
        self.attest()
        self._guarded.insert(
            table,
            rows,
            columns=columns,
            timeout_ms=timeout_ms,
            deduplication_token=deduplication_token,
        )

    def close(self) -> None:
        self._guarded.close()
