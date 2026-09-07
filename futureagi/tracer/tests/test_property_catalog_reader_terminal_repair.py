"""Reader proof-consumer tests with real publication records and control journal.

The typed receipts model the executor's just-confirmed output; native all-member
proof production is covered by the terminal executor suite. SQLite executes the
actual latest-state helper, including status/version resolution before filtering.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import reader_activation as subject
from tracer.services.clickhouse.v2.property_catalog.activation import (
    CatalogLifecycleMode,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlAction,
    ActivationControlRejected,
    ActivationControlRequest,
    ActivationControlScope,
    PropertyCatalogActivationControlPlane,
    activation_control_event_sql,
    activation_history_sql,
    qualified_activation_sql,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeTerminalRepairReceipt,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    NativeReadAgreement,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import (
    _ACTIVATION_COLUMNS,
    PropertyCatalogStateConflict,
    _latest_activations,
    activation_latest_rows_sql,
)
from tracer.services.clickhouse.v2.property_catalog.terminal_repair_intent import (
    FrozenTerminalRepairIntent,
)
from tracer.tests.test_property_catalog_activation_history import _set_mode
from tracer.tests.test_property_catalog_reader_activation import Client, Driver
from tracer.tests.test_property_catalog_terminal_repair_intent import (
    _no_active,
    publication_evidence,
)


class _Client(Client):
    def __init__(self, harness):
        super().__init__(database=harness.client.catalog_database, deployment="dev")
        self.harness = harness
        self.reads = []
        self.qualified_override = None

    def latest(self, params):
        sql = activation_latest_rows_sql(self.catalog_database)
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                f"ATTACH DATABASE ':memory:' AS `{self.catalog_database}`"
            )
            table = f"`{self.catalog_database}`.`property_catalog_activations`"
            connection.execute(
                f"CREATE TABLE {table} ({', '.join(_ACTIVATION_COLUMNS)})"
            )
            # Fixed-width decimal text preserves UInt64 ordering, including the
            # terminal UINT64_MAX that cannot fit SQLite's signed integer cell.
            connection.executemany(
                f"INSERT INTO {table} VALUES ({', '.join('?' for _ in _ACTIVATION_COLUMNS)})",
                [
                    tuple(
                        f"{row[column]:020d}"
                        if column == "_version"
                        else row[column].isoformat()
                        if isinstance(row[column], datetime)
                        else row[column]
                        for column in _ACTIVATION_COLUMNS
                    )
                    for row in self.harness.rows
                ],
            )
            return tuple(
                {
                    key: int(value)
                    if key == "_version"
                    else datetime.fromisoformat(value)
                    if key in {"qualified_at", "updated_at"} and value is not None
                    else value
                    for key, value in dict(row).items()
                }
                for row in connection.execute(
                    re.sub(r"%\(([a-z_]+)\)s", r":\1", sql), params
                )
            )

    def query(self, sql, params, *, timeout_ms):
        assert timeout_ms > 0
        self.reads.append(sql)
        if sql == activation_latest_rows_sql(self.catalog_database):
            return self.latest(params)
        assert params["catalog_organization_id"] == self.harness.lease.organization_id
        assert params["catalog_workspace_id"] == self.harness.lease.workspace_id
        if sql == activation_control_event_sql(self.catalog_database, deployment="dev"):
            return [] if self.hidden else list(self.events)
        if sql == activation_history_sql(self.catalog_database, deployment="dev"):
            return [{"catalog_history_exists": 1}] if self.harness.rows else []
        follow = "catalog_follow_epoch" in params
        assert sql == qualified_activation_sql(
            self.catalog_database, deployment="dev", follow=follow
        )
        if self.qualified_override is not None:
            return self.qualified_override
        latest = _latest_activations(
            self.latest(
                {
                    "organization_id": self.harness.lease.organization_id,
                    "workspace_id": self.harness.lease.workspace_id,
                    "catalog_epoch": self.harness.lease.catalog_epoch,
                }
            ),
            logical_columns=tuple(c for c in _ACTIVATION_COLUMNS if c != "_version"),
        )
        rows = sorted(
            (
                row
                for row in latest.values()
                if row["status"] == "active" and row["qualified_at"] is not None
            ),
            key=lambda row: row["activation_sequence"],
        )
        if follow:
            rows = [
                row
                for row in rows
                if row["catalog_revision"] == params["catalog_follow_anchor"]
                or row in rows[-2:]
            ]
        fields = (
            "organization_id",
            "workspace_id",
            "catalog_epoch",
            "projection_version",
            "catalog_revision",
            "build_token",
            "activation_sha256",
            "activation_sequence",
        )
        return [
            {**{field: row[field] for field in fields}, "latest_variants": 1}
            for row in rows
        ]


class _Case:
    def __init__(self, directory, monkeypatch):
        self.h, self.evidence = publication_evidence(directory)
        self.intent = FrozenTerminalRepairIntent.from_evidence(**self.evidence)
        self.receipt = NativeTerminalRepairReceipt(
            self.intent.binding, ("a" * 64, "b" * 64)
        )
        self.proofs = ((self.intent, self.receipt),)
        self.client = _Client(self.h)
        self.scope = ActivationControlScope(
            self.h.lease.organization_id, self.h.lease.workspace_id
        )
        self.directory = directory
        self.original = self.h.store_record(self.h.rows[0])
        monkeypatch.setattr(
            self.h.base.coordinator, "_confirm_terminal_marker", lambda *_: self.receipt
        )

    def service(self, **kwargs):
        return subject.AutomaticReaderActivation(
            self.client,
            database=self.client.catalog_database,
            deployment="dev",
            state_directory=self.directory,
            catalog_epoch=self.original.catalog_epoch,
            projection_version=self.original.projection_version,
            authorize_scope=kwargs.pop("authorize_scope", lambda _: True),
            now=kwargs.pop("now", self.h.base.clock),
            **kwargs,
        )

    def reconcile(self, **kwargs):
        # Runtime keeps this outer lock across fresh confirmation and selection.
        coordinator = self.h.base.coordinator
        return coordinator._serializer.serialize(
            coordinator._revision_key_for_lease(self.intent.lease),
            lambda: self.service().reconcile(
                self.scope, terminal_repairs=self.proofs, **kwargs
            ),
        )

    def invalidate(self):
        self.h.rows.append(dict(self.intent.rows[0]))

    def publish(self, mode=CatalogLifecycleMode.INITIAL_BACKFILL):
        self.h.advance()
        _set_mode(self.h, mode, self.h.lease.catalog_revision)
        return self.h.activate().record


@pytest.fixture
def case(tmp_path, monkeypatch):
    return _Case(tmp_path, monkeypatch)


def test_proven_missing_anchor_waits_then_real_initial_publication_reanchors(case):
    first = case.service().reconcile(case.scope)
    case.invalidate()
    waiting = case.reconcile()
    assert (waiting.status, waiting.selected_target, waiting.event) == (
        "waiting_for_qualification",
        None,
        None,
    )
    assert len(case.client.events) == len(case.client.attempts) == 1
    assert activation_latest_rows_sql(case.client.catalog_database) in case.client.reads
    replacement = case.publish()
    assert replacement.activation_sequence > case.original.activation_sequence
    repaired = case.reconcile()
    assert repaired.status == "activated"
    assert repaired.selected_target == subject.AutomaticReaderActivation._record_target(
        replacement
    )
    assert repaired.event.action is ActivationControlAction.FOLLOW
    assert repaired.event.control_sequence == first.event.control_sequence + 1
    assert repaired.event.previous_control_sha256 == first.event.control_sha256
    assert case.reconcile().status == "already_selected"
    assert len(case.client.events) == len(case.client.attempts) == 2


def test_later_real_full_repair_self_anchor_uses_same_follow_journal(case):
    first = case.service().reconcile(case.scope)
    # The already-published FULL is a real qualified record, not a synthetic
    # target digest. Terminal proof production timing is outside this consumer.
    replacement = case.publish(CatalogLifecycleMode.FULL_REPAIR)
    case.invalidate()
    result = case.reconcile()
    assert result.event.previous_control_sha256 == first.event.control_sha256
    assert result.selected_target.activation_sha256 == replacement.activation_sha256
    assert replacement.lineage_anchor_revision == replacement.catalog_revision
    assert result.event.action is ActivationControlAction.FOLLOW


def test_head_none_qualified_new_initial_keeps_normal_follow_path(case):
    case.invalidate()
    replacement = case.publish()
    result = case.reconcile()
    assert result.status == "activated" and result.event.control_sequence == 1
    assert result.selected_target.activation_sha256 == replacement.activation_sha256
    assert result.event.previous_control_sha256 == "0" * 64


@pytest.mark.parametrize("proof_kind", ["absent", "source_only", "different_original"])
def test_missing_anchor_without_exact_publication_proof_is_not_repaired(
    case, proof_kind
):
    first = case.service().reconcile(case.scope)
    case.invalidate()
    case.publish()
    if proof_kind == "absent":
        proofs = ()
    elif proof_kind == "source_only":
        intent = FrozenTerminalRepairIntent.from_evidence(**_no_active(case.evidence))
        proofs = ((intent, NativeTerminalRepairReceipt(intent.binding, ("a" * 64,))),)
    else:
        # Same tenant/revision/token but a different exact activation target.
        from tracer.services.clickhouse.v2.property_catalog.activation_control import (
            ActivationControlEvent,
        )

        original = first.event
        other = ActivationControlEvent.create(
            control_sequence=original.control_sequence,
            request_id=original.request_id,
            action=original.action,
            target=replace(original.target, activation_sha256="e" * 64),
            previous_control_sha256=original.previous_control_sha256,
            controlled_at=original.controlled_at,
        )
        # A separate journal models a valid pre-existing FOLLOW for that digest.
        case.client.events[0] = other.as_row()
        other_directory = case.directory / "other-reader"
        other_directory.mkdir()
        case.directory = other_directory
        proofs = case.proofs
    with pytest.raises(
        ActivationControlRejected, match="control_head_target_not_qualified"
    ):
        case.service().reconcile(case.scope, terminal_repairs=proofs)
    assert len(case.client.attempts) == 1


@pytest.mark.parametrize(
    "proofs", [True, [(True, True)], ((True, True),), ((None, None),)]
)
def test_untyped_proof_is_rejected_without_control_write(case, proofs):
    with pytest.raises(
        ActivationControlRejected, match="control_terminal_repair_evidence_invalid"
    ):
        case.service().reconcile(case.scope, terminal_repairs=proofs)
    assert not case.client.attempts


@pytest.mark.parametrize("field", ["build_lease_sha256", "publication_sha256"])
def test_receipt_must_bind_exact_frozen_intent(case, field):
    case.service().reconcile(case.scope)
    case.invalidate()
    receipt = replace(
        case.receipt, binding=replace(case.receipt.binding, **{field: "e" * 64})
    )
    with pytest.raises(
        ActivationControlRejected, match="control_terminal_repair_evidence_conflict"
    ):
        case.service().reconcile(case.scope, terminal_repairs=((case.intent, receipt),))
    assert len(case.client.attempts) == 1


def test_proof_cannot_select_still_active_original(case):
    case.service().reconcile(case.scope)
    with pytest.raises(
        ActivationControlRejected, match="control_terminal_anchor_still_qualified"
    ):
        case.reconcile()
    assert len(case.client.attempts) == 1


@pytest.mark.parametrize("action", ["disable", "rollback"])
@pytest.mark.parametrize("failure", [None, "before", "after"])
def test_manual_head_and_pending_manual_are_preserved(case, action, failure):
    automatic = case.service()
    first = automatic.reconcile(case.scope)
    if action == "rollback":
        case.publish(CatalogLifecycleMode.FULL_REPAIR)
    case.client.failure = failure
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    request = ActivationControlRequest(
        str(UUID(int=801)), first.selected_target, first.event.head
    )
    if failure:
        with pytest.raises(TimeoutError):
            getattr(plane, action)(request=request, now=case.h.base.clock())
    else:
        getattr(plane, action)(request=request, now=case.h.base.clock())
    frozen = case.client.attempts[-1]
    case.invalidate()
    if action == "disable":
        case.publish()
    if failure == "before":
        with pytest.raises(
            ActivationControlRejected, match="control_manual_append_uncertain"
        ):
            case.reconcile()
        pending = automatic.coordinator.serialize(
            case.scope, lambda: automatic.coordinator.pending(case.scope)
        )
        assert pending.request_id == request.request_id
    else:
        result = case.reconcile()
        assert result.status == ("recovered" if failure else action)
        assert result.selected_target == (
            None if action == "disable" else first.selected_target
        )
    assert case.client.attempts[-1] == frozen and len(case.client.attempts) == 2


@pytest.mark.parametrize("failure", ["before", "after"])
def test_uncertain_reanchor_replays_frozen_follow_on_restart(case, failure):
    case.service().reconcile(case.scope)
    case.invalidate()
    replacement = case.publish()
    case.client.failure = failure
    with pytest.raises(TimeoutError):
        case.reconcile()
    frozen = case.client.attempts[-1]
    result = case.service(
        now=lambda: case.h.base.clock() + timedelta(days=9)
    ).reconcile(case.scope, terminal_repairs=case.proofs)
    assert result.status == "recovered"
    assert result.selected_target.activation_sha256 == replacement.activation_sha256
    assert all(attempt == frozen for attempt in case.client.attempts[1:])
    assert result.event.controlled_at == frozen[0]["controlled_at"]
    assert len(case.client.events) == 2


@pytest.mark.parametrize(
    "mutation",
    ["conflicting_disabled", "duplicate_sequence", "unknown_status", "bad_manifest"],
)
def test_repair_keeps_all_status_and_malformed_record_failures(case, mutation):
    case.service().reconcile(case.scope)
    case.invalidate()
    if mutation == "conflicting_disabled":
        case.h.rows.append({**case.h.rows[-1], "value_rows": 123})
    elif mutation == "duplicate_sequence":
        case.h.rows.append(
            {
                **case.h.rows[-1],
                "catalog_revision": case.original.catalog_revision + 1,
                "build_token": str(UUID(int=901)),
            }
        )
    elif mutation == "unknown_status":
        case.h.rows[-1]["status"] = "unexpected"
    else:
        case.publish()
        case.h.rows[-1]["source_manifest_sha256"] = "e" * 64
    with pytest.raises((PropertyCatalogStateConflict, ValueError)):
        case.reconcile()
    assert len(case.client.attempts) == 1


def test_empty_projection_does_not_suppress_visible_replacement(case):
    case.service().reconcile(case.scope)
    case.invalidate()
    case.publish()
    case.client.qualified_override = []
    with pytest.raises(ActivationControlRejected, match="qualified_activation_missing"):
        case.reconcile()
    assert len(case.client.attempts) == 1


def test_nonmissing_qualification_error_is_not_suppressed(case):
    case.service().reconcile(case.scope)
    case.invalidate()
    case.client.qualified_override = [
        {
            **asdict(subject.AutomaticReaderActivation._record_target(case.original)),
            "activation_sequence": 0,
            "latest_variants": 1,
        }
    ]
    with pytest.raises((ActivationControlRejected, ValueError)):
        case.reconcile()
    assert len(case.client.attempts) == 1


@pytest.mark.parametrize("native", [False, True])
def test_reader_client_admits_only_exact_complete_history_query(native):
    from tracer.services.clickhouse.v2.property_catalog.reader_activation_client import (
        ProductionActivationCommandError,
    )

    database = "property_catalog_dev_oss"
    driver = Driver(database, deployment="dev")
    original_read = driver.execute_read
    calls = []
    sql = activation_latest_rows_sql(database)

    def read(statement, params, **kwargs):
        if statement == sql:
            calls.append((params, kwargs))
            return (), (), None
        return original_read(statement, params, **kwargs)

    def query(statement, params, **kwargs):
        assert statement == sql
        calls.append((params, kwargs))
        return ()

    from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
        DurableNativeCatalogWriter,
    )

    # Routing-only probe. Native proof/transport execution has its own suite.
    writer = object.__new__(DurableNativeCatalogWriter)
    writer.driver, writer.database, writer.query = driver, database, query

    driver.execute_read = read
    client = subject.ReaderActivationClient(
        driver,
        database=database,
        user=driver.user,
        expected_hostnames=("catalog-0",),
        deployment="dev",
        durable_writer=writer if native else None,
    )
    params = {
        "organization_id": str(UUID(int=1)),
        "workspace_id": str(UUID(int=2)),
        "catalog_epoch": 1,
    }
    assert client.query(sql, params, timeout_ms=100) == ()
    assert calls[0][0] == params
    if native:
        assert calls[0][1]["agreement"] == NativeReadAgreement.complete_result()
    else:
        assert calls[0][1]["settings"]["readonly"] == 2
        assert calls[0][1]["settings"]["result_overflow_mode"] == "throw"
    with pytest.raises(ProductionActivationCommandError, match="non-reviewed read"):
        client.query(sql + " LIMIT 1", params, timeout_ms=100)
    assert len(calls) == 1 and not driver.writes
