from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import reader_activation as subject
from tracer.services.clickhouse.v2.property_catalog import (
    reader_activation_client as native,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ACTIVATION_CONTROL_COLUMNS,
    ACTIVATION_CONTROL_TABLE,
    ActivationControlAction,
    ActivationControlEvent,
    ActivationControlRejected,
    ActivationControlRequest,
    ActivationControlScope,
    ActivationControlTarget,
    PropertyCatalogActivationControlPlane,
    activation_control_event_sql,
    activation_history_sql,
    qualified_activation_sql,
)

AT = datetime(2026, 9, 5, tzinfo=UTC)
SCOPE = ActivationControlScope(str(UUID(int=1)), str(UUID(int=2)))


def target(revision=1, *, epoch=7, projection=3):
    return ActivationControlTarget(
        organization_id=SCOPE.organization_id,
        workspace_id=SCOPE.workspace_id,
        catalog_epoch=epoch,
        projection_version=projection,
        catalog_revision=revision,
        build_token=str(UUID(int=revision + 10)),
        activation_sha256=hashlib.sha256(f"{epoch}:{revision}".encode()).hexdigest(),
    )


class Client:
    # This double models a settled transport. Native receipt/durability tests
    # exercise the real writer and journal separately.
    receipt_confirmation_available = True

    def __init__(self, *, database="property_catalog", deployment="prod"):
        self.catalog_database, self.deployment = database, deployment
        self.targets = [target()]
        self.history_rows = None
        self.events, self.attempts = [], []
        self.failure = None
        self.hidden = False
        self.attestations = 0
        self.confirmations = []
        self.confirmation_failure = None

    def attest(self):
        self.attestations += 1

    def confirm_receipt(
        self, table, rows, *, columns, timeout_ms, deduplication_token
    ):
        assert self.receipt_confirmation_available is True
        assert table == f"`{self.catalog_database}`.`{ACTIVATION_CONTROL_TABLE}`"
        assert columns == ACTIVATION_CONTROL_COLUMNS
        assert timeout_ms > 0 and len(rows) == 1
        row = dict(rows[0])
        assert row in self.events
        assert deduplication_token == (
            "property-catalog-activation-control-v1:"
            f"{row['request_id']}:{row['control_sha256']}"
        )
        if self.confirmation_failure is not None:
            raise self.confirmation_failure
        self.confirmations.append((row, deduplication_token))

    def query(self, sql, params, *, timeout_ms):
        assert params["catalog_organization_id"] == SCOPE.organization_id
        if sql == activation_control_event_sql(
            self.catalog_database, deployment=self.deployment
        ):
            return [] if self.hidden else list(self.events)
        if sql == activation_history_sql(
            self.catalog_database, deployment=self.deployment
        ):
            if self.history_rows is not None:
                return self.history_rows
            return [{"catalog_history_exists": 1}] if self.targets else []
        follow = "catalog_follow_epoch" in params
        assert sql == qualified_activation_sql(
            self.catalog_database, deployment=self.deployment, follow=follow
        )
        targets = self.targets
        if follow and len(targets) > 3:
            targets = [
                item
                for item in targets
                if item.catalog_revision == params["catalog_follow_anchor"]
                or item in targets[-2:]
            ]
        return [
            dict(
                asdict(item),
                activation_sequence=item.catalog_revision,
                latest_variants=1,
            )
            for item in targets
        ]

    def insert(self, table, rows, *, columns, timeout_ms, deduplication_token):
        assert table == f"`{self.catalog_database}`.`{ACTIVATION_CONTROL_TABLE}`"
        assert columns == ACTIVATION_CONTROL_COLUMNS
        row = dict(rows[0])
        self.attempts.append((row, deduplication_token))
        failure, self.failure = self.failure, None
        if failure == "before":
            raise TimeoutError("uncertain before insert")
        self.events.append(row)
        if failure == "after":
            raise TimeoutError("uncertain after insert")


def service(client, directory, **kwargs):
    return subject.AutomaticReaderActivation(
        client,
        database=client.catalog_database,
        deployment=client.deployment,
        state_directory=directory,
        catalog_epoch=7,
        projection_version=3,
        authorize_scope=kwargs.pop("authorize_scope", lambda _: True),
        now=kwargs.pop("now", lambda: AT),
        **kwargs,
    )


@pytest.mark.parametrize(
    "database,deployment",
    [("property_catalog", "prod"), ("property_catalog_dev_oss", "dev")],
)
def test_first_replay_restart_and_new_qualification(tmp_path, database, deployment):
    client = Client(database=database, deployment=deployment)
    first = service(client, tmp_path).reconcile(SCOPE)
    assert first.status == "activated" and first.selected_target == target()
    assert (
        service(client, tmp_path, now=lambda: AT + timedelta(days=1))
        .reconcile(SCOPE)
        .status
        == "already_selected"
    )
    client.targets.append(target(2))
    second = service(client, tmp_path).reconcile(SCOPE)
    assert second.event is None and second.selected_target == target(2)
    assert len(client.events) == len(client.attempts) == 1
    assert first.event.action is ActivationControlAction.FOLLOW


@pytest.mark.parametrize("action", ["disable", "rollback"])
def test_explicit_actions_are_sticky_until_explicit_reenable(tmp_path, action):
    client = Client()
    automatic = service(client, tmp_path)
    first = automatic.reconcile(SCOPE)
    client.targets.append(target(2))
    automatic.reconcile(SCOPE)
    plane = PropertyCatalogActivationControlPlane(automatic.store)
    chosen = target(2) if action == "disable" else target()
    manual = getattr(plane, action)(
        request=ActivationControlRequest(str(UUID(int=100)), chosen, first.event.head),
        now=AT,
    )
    client.targets.append(target(3))
    result = service(client, tmp_path).reconcile(SCOPE)
    assert result.status == action
    assert result.selected_target == (None if action == "disable" else target())
    assert len(client.events) == 2
    plane.activate(
        request=ActivationControlRequest(
            str(UUID(int=101)), target(3), manual.event.head
        ),
        now=AT,
    )
    assert automatic.reconcile(SCOPE).selected_target == target(3)


@pytest.mark.parametrize("failure", ["before", "after"])
def test_uncertain_write_restart_uses_exact_persisted_event(tmp_path, failure):
    client = Client()
    client.failure = failure
    with pytest.raises(TimeoutError):
        service(client, tmp_path).reconcile(SCOPE)
    original = client.attempts[0]
    client.targets.append(target(2))
    result = service(client, tmp_path, now=lambda: AT + timedelta(days=9)).reconcile(
        SCOPE
    )
    assert result.status == "recovered" and result.selected_target == target()
    assert result.event.controlled_at == AT
    assert all(attempt == original for attempt in client.attempts)
    assert len(client.events) == 1
    assert service(client, tmp_path).reconcile(SCOPE).selected_target == target(2)


def test_pending_different_request_cannot_allocate_same_sequence(tmp_path):
    client = Client()
    automatic = service(client, tmp_path)
    client.failure = "before"
    with pytest.raises(TimeoutError):
        automatic.reconcile(SCOPE)
    event = ActivationControlEvent.create(
        control_sequence=1,
        request_id=str(UUID(int=999)),
        action=ActivationControlAction.ACTIVATE,
        target=target(),
        previous_control_sha256="0" * 64,
        controlled_at=AT,
    )
    with pytest.raises(ActivationControlRejected, match="control_append_uncertain"):
        automatic.store.append_control_event(event, expected_head=None)
    assert len(client.attempts) == 1


def test_pending_manual_disable_is_not_replaced_by_automatic_activation(tmp_path):
    client = Client()
    automatic = service(client, tmp_path)
    first = automatic.reconcile(SCOPE)
    client.failure = "before"
    with pytest.raises(TimeoutError):
        PropertyCatalogActivationControlPlane(automatic.store).disable(
            request=ActivationControlRequest(
                str(UUID(int=100)), target(), first.event.head
            ),
            now=AT,
        )
    with pytest.raises(
        ActivationControlRejected, match="control_manual_append_uncertain"
    ):
        service(client, tmp_path).reconcile(SCOPE)
    assert len(client.attempts) == 2


def test_two_independent_coordinators_serialize_compared_head(tmp_path):
    client = Client()
    services = [service(client, tmp_path) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda item: item.reconcile(SCOPE), services))
    assert (
        sorted(item.status for item in results)
        == ["activated"] + ["already_selected"] * 5
    )
    assert len(client.events) == 1


def test_lagging_replica_cannot_fork_confirmed_head_after_restart(tmp_path):
    client = Client()
    service(client, tmp_path).reconcile(SCOPE)
    client.hidden = True
    with pytest.raises(
        ActivationControlRejected, match="control_durable_head_not_visible"
    ):
        service(client, tmp_path).reconcile(SCOPE)
    assert len(client.attempts) == 1


@pytest.mark.parametrize("authorization", [(False,), (True, False)])
def test_authorization_required_at_read_and_append_boundaries(tmp_path, authorization):
    client = Client()
    answers = iter(authorization)
    assert (
        service(client, tmp_path, authorize_scope=lambda _: next(answers))
        .reconcile(SCOPE)
        .status
        == "ineligible"
    )
    assert not client.attempts


def test_empty_qualification_waits_and_conflicting_identity_rejects(tmp_path):
    client = Client()
    client.targets = []
    assert (
        service(client, tmp_path).reconcile(SCOPE).status == "waiting_for_qualification"
    )
    client.targets = [target(epoch=8)]
    with pytest.raises(
        ActivationControlRejected, match="control_installation_identity_conflict"
    ):
        service(client, tmp_path).reconcile(SCOPE)
    assert not client.attempts


@pytest.mark.parametrize(
    "kind", ["fifo", "symlink", "permissions", "digest", "noncanonical"]
)
def test_intent_rejects_unsafe_or_corrupted_files_without_writing(tmp_path, kind):
    client = Client()
    automatic = service(client, tmp_path)
    first = automatic.reconcile(SCOPE)
    path = next(tmp_path.glob("reader-activation-*.json"))
    raw = path.read_bytes()
    path.unlink()
    if kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "symlink":
        path.symlink_to(tmp_path / "missing")
    else:
        path.write_bytes(
            raw.replace(first.event.control_sha256.encode(), b"f" * 64)
            if kind == "digest"
            else raw + b" "
            if kind == "noncanonical"
            else raw
        )
        path.chmod(0o644 if kind == "permissions" else 0o600)
    with pytest.raises((ActivationControlRejected, OSError, ValueError)):
        service(client, tmp_path).reconcile(SCOPE)
    assert len(client.attempts) == 1


def schema_rows(database, *, replicated=False):
    from tracer.services.clickhouse.v2 import catalog_prod_schema as schema

    rows = []
    for statement, spec in zip(
        (*schema._load_canonical_statements(), schema._activation_control_statement()),
        (*schema._TABLE_SPECS, schema._ACTIVATION_CONTROL_SPEC),
        strict=True,
    ):
        if statement.table not in {
            "property_catalog_activations",
            ACTIVATION_CONTROL_TABLE,
        }:
            continue
        sql = statement.sql
        if replicated:
            sql = schema._render_table(
                statement,
                spec,
                target_database=database,
                cluster="runtime_schema_verification",
                keeper_path_prefix="/clickhouse/tables",
            ).sql
        rows.append(
            (
                database,
                statement.table,
                spec.replicated_engine if replicated else statement.engine,
                sql,
            )
        )
    return rows


class Driver:
    def __init__(self, database, *, deployment="prod", replicated=False):
        self.database, self.user, self.server_enforced_readonly = (
            database,
            "control_writer",
            False,
        )
        self.provenance = [("catalog-0", database, self.user, 0, 0)]
        self.schemas = schema_rows(database, replicated=replicated)
        grants = [
            (database, "property_catalog_activations", "SELECT"),
            (database, ACTIVATION_CONTROL_TABLE, "SELECT, INSERT"),
        ]
        if deployment == "dev":
            grants = [
                (database, "*", "SELECT, INSERT"),
                *[
                    ("system", table, "SELECT")
                    for table in ("databases", "settings", "tables")
                ],
            ]
        self.grants = [
            (f"GRANT {access} ON {db}.{table} TO {self.user}",)
            for db, table, access in grants
        ]
        self.writes = []

    def execute(self, sql, *args, **kwargs):
        if sql == native._CLICKHOUSE_PROVENANCE_SQL:
            return self.provenance
        if sql == native._CLICKHOUSE_GRANTS_SQL:
            return self.grants
        self.writes.append((sql, args, kwargs))

    def execute_read(self, sql, params, **kwargs):
        assert sql == subject._SCHEMA_SQL
        assert params["database"] == self.database
        return self.schemas, (), None


@pytest.mark.parametrize(
    "deployment,replicated", [("dev", False), ("prod", False), ("prod", True)]
)
def test_client_attests_supported_schema_and_deployment_grants(deployment, replicated):
    db = "property_catalog_dev_oss" if deployment == "dev" else "property_catalog"
    driver = Driver(db, deployment=deployment, replicated=replicated)
    client = subject.ReaderActivationClient(
        driver,
        database=db,
        user=driver.user,
        expected_hostnames=("catalog-0",),
        deployment=deployment,
    )
    client.attest()
    assert not driver.writes
    with pytest.raises(
        native.ProductionActivationCommandError, match="non-reviewed read"
    ):
        client.query("SELECT * FROM default.spans", {}, timeout_ms=100)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "column", "engine", "provenance", "grant", "readonly"],
)
def test_client_rejects_schema_provenance_or_grant_drift_before_write(mutation):
    driver = Driver("property_catalog_dev_oss", deployment="dev")
    if mutation == "missing":
        driver.schemas.pop()
    elif mutation == "extra":
        driver.schemas.append(driver.schemas[0])
    elif mutation in {"column", "engine"}:
        db, name, engine, sql = driver.schemas[-1]
        driver.schemas[-1] = (
            db,
            name,
            "Memory" if mutation == "engine" else engine,
            sql.replace(
                "control_sequence         UInt64", "control_sequence         UInt32"
            ),
        )
    elif mutation == "provenance":
        driver.provenance = [("wrong-server", driver.database, driver.user, 0, 0)]
    elif mutation == "grant":
        driver.grants.append((f"GRANT INSERT ON default.spans TO {driver.user}",))
    else:
        driver.server_enforced_readonly = True
    with pytest.raises(
        (ActivationControlRejected, native.ProductionActivationCommandError)
    ):
        subject.ReaderActivationClient(
            driver,
            database=driver.database,
            user=driver.user,
            expected_hostnames=("catalog-0",),
            deployment="dev",
        )
    assert not driver.writes


def test_qualified_sql_uses_latest_nonnull_qualification_without_argmax_null_skip():
    sql = qualified_activation_sql("property_catalog_dev_oss", deployment="dev")
    assert (
        "argMax(tuple(versioned_rows.qualified_at), versioned_rows._version).1" in sql
    )
    assert "WHERE status = 'active' AND qualified_at IS NOT NULL" in sql


def test_initial_history_query_reuses_reviewed_two_table_read_grants():
    database = "property_catalog_dev_oss"
    driver = Driver(database, deployment="dev")
    original_read = driver.execute_read
    calls = []

    def read(sql, params, **kwargs):
        if sql == activation_history_sql(database, deployment="dev"):
            calls.append((params, kwargs))
            return [(1,)], [("catalog_history_exists", "UInt8")], None
        return original_read(sql, params, **kwargs)

    driver.execute_read = read
    client = subject.ReaderActivationClient(
        driver, database=database, user=driver.user,
        expected_hostnames=("catalog-0",), deployment="dev",
    )
    assert client.query(
        activation_history_sql(database, deployment="dev"),
        {"catalog_organization_id": SCOPE.organization_id, "catalog_workspace_id": SCOPE.workspace_id},
        timeout_ms=100,
    ) == ({"catalog_history_exists": 1},)
    assert calls[0][1]["settings"]["readonly"] == 2
    assert calls[0][1]["settings"]["result_overflow_mode"] == "throw"
    assert not driver.writes
