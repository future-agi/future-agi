"""Exact control-query routing with real native agreement, fake transports only."""

from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import activation_control as control
from tracer.services.clickhouse.v2.property_catalog import (
    reader_activation_client as subject,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    DurableNativeCatalogWriter,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    _MAX_ROWS,
    STRICT_READ_AGREEMENT,
    NativeReadAgreement,
    NativeWriteProofError,
)
from tracer.services.clickhouse.v2.property_catalog.state_store import (
    activation_latest_rows_sql,
)
from tracer.tests.test_property_catalog_native_read_agreement import observed_proof
from tracer.tests.test_property_catalog_native_write_proof import DATABASE

SCOPE = control.ActivationControlScope(str(UUID(int=1)), str(UUID(int=2)))
KINDS = ("events", "qualified", "follow", "history")
INVENTORIES = KINDS[:3]


def query_case(kind, database=DATABASE, deployment="dev"):
    params = {
        "catalog_organization_id": SCOPE.organization_id,
        "catalog_workspace_id": SCOPE.workspace_id,
    }
    if kind == "events":
        limit = control.ACTIVATION_CONTROL_MAX_EVENTS + 1
        params["catalog_control_result_limit"] = limit
        return (
            control.activation_control_event_sql(database, deployment=deployment),
            params,
            NativeReadAgreement.cap_plus_one(
                limit_parameter="catalog_control_result_limit", limit=limit
            ),
        )
    if kind == "history":
        return (
            control.activation_history_sql(database, deployment=deployment),
            params,
            STRICT_READ_AGREEMENT,
        )
    assert kind in {"qualified", "follow"}
    follow = kind == "follow"
    if follow:
        params.update(catalog_follow_epoch=1, catalog_follow_anchor=1)
    return (
        control.qualified_activation_sql(
            database, deployment=deployment, follow=follow
        ),
        params,
        NativeReadAgreement.cap_plus_one(
            limit=4 if follow else control.ACTIVATION_CONTROL_MAX_QUALIFIED_BUILDS + 1
        ),
    )


class Driver:
    user = "catalog_control_writer"
    server_enforced_readonly = False
    host = "member-1.internal"
    port = 9000

    def __init__(self, database=DATABASE, deployment="dev"):
        self.database = database
        self.deployment = deployment
        self.execute_read = Mock(side_effect=AssertionError("no local fallback"))

    def execute(self, sql):
        if sql == subject._CLICKHOUSE_PROVENANCE_SQL:
            return [("catalog-0", self.database, self.user, 0, 0)]
        assert sql == subject._CLICKHOUSE_GRANTS_SQL, "only fake identity/grant reads"
        if self.deployment == "prod":
            return [
                (
                    f"GRANT SELECT ON {self.database}.property_catalog_activations TO {self.user}",
                ),
                (
                    f"GRANT SELECT, INSERT ON {self.database}.{control.ACTIVATION_CONTROL_TABLE} TO {self.user}",
                ),
            ]
        return [
            (f"GRANT SELECT, INSERT ON {self.database}.* TO {self.user}",),
            *(
                (f"GRANT SELECT ON system.{table} TO {self.user}",)
                for table in ("databases", "settings", "tables")
            ),
        ]


def adapter(driver, writer):
    return subject._ActivationControlClient(
        driver,
        database=driver.database,
        user=driver.user,
        expected_hostnames=("catalog-0",),
        deployment=driver.deployment,
        durable_writer=writer,
    )


def delegation_client(database=DATABASE, deployment="dev"):
    driver = Driver(database, deployment)
    writer = object.__new__(DurableNativeCatalogWriter)
    writer.driver, writer.database = driver, database
    writer.query = Mock(return_value=({"native": "unchanged"},))
    return adapter(driver, writer), driver, writer


def target(revision):
    return control.ActivationControlTarget(
        organization_id=SCOPE.organization_id,
        workspace_id=SCOPE.workspace_id,
        catalog_epoch=1,
        projection_version=1,
        catalog_revision=revision,
        build_token=str(UUID(int=100 + revision)),
        activation_sha256="a" * 64,
    )


def event(
    sequence,
    previous=control.ZERO_SHA256,
    *,
    action=control.ActivationControlAction.FOLLOW,
):
    return control.ActivationControlEvent.create(
        control_sequence=sequence,
        request_id=str(UUID(int=200 + sequence)),
        action=action,
        target=target(sequence),
        previous_control_sha256=previous,
        controlled_at=datetime(2026, 9, 6, microsecond=123456, tzinfo=UTC),
    )


def sample_rows(kind):
    if kind == "history":
        return [{"catalog_history_exists": 1}]
    if kind == "events":
        first = event(1)
        return [first.as_row(), event(2, first.control_sha256).as_row()]
    return [
        {
            "organization_id": SCOPE.organization_id,
            "workspace_id": SCOPE.workspace_id,
            "catalog_epoch": 1,
            "projection_version": 1,
            "catalog_revision": revision,
            "build_token": str(UUID(int=100 + revision)),
            "activation_sequence": revision,
            "activation_sha256": "a" * 64,
            "latest_variants": 1,
        }
        for revision in (1, 2)
    ]


def real_client(tmp_path, kind, left, right):
    names = tuple(sample_rows(kind)[0])
    # Column names are kept even for empty observations, as the native protocol
    # does. The real proof and writer run; only observations/attestation are fake.
    proof, calls = observed_proof(
        tmp_path,
        [tuple(row[name] for name in names) for row in left],
        right
        if isinstance(right, Exception)
        else [tuple(row[name] for name in names) for row in right],
        left_columns=names,
        right_columns=names,
    )
    driver = Driver()
    for index, connection in enumerate(proof.connections, start=1):
        connection.driver.host = f"member-{index}.internal"
        connection.driver.port = 9000
    proof.attest = Mock()
    writer = DurableNativeCatalogWriter(
        driver, directory=tmp_path, proof=proof, member_name=proof.connections[0].name
    )
    return adapter(driver, writer), proof, calls


def store_read(client, kind):
    store = control.ClickHouseActivationControlStore(
        client, database=DATABASE, deployment="dev", timeout_ms=1000
    )
    if kind == "events":
        return store.list_control_events(SCOPE)
    if kind == "qualified":
        return store.list_qualified_activations(SCOPE)
    if kind == "follow":
        return store.qualified_for_follow(SCOPE, catalog_epoch=1, anchor_revision=1)
    return store.has_lifecycle_history(SCOPE)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "database,deployment", [(DATABASE, "dev"), ("property_catalog", "prod")]
)
def test_all_four_exact_queries_forward_the_reviewed_contract(
    kind, database, deployment
):
    client, driver, writer = delegation_client(database, deployment)
    sql, params, agreement = query_case(kind, database, deployment)
    original_params = dict(params)
    assert client.query(sql, params, timeout_ms=1000) is writer.query.return_value
    writer.query.assert_called_once_with(
        sql, params, timeout_ms=1000, agreement=agreement
    )
    assert params == original_params
    assert client._allowed_reads == frozenset(
        [query_case(k, database, deployment)[0] for k in KINDS]
        + [activation_latest_rows_sql(database)]
    )
    driver.execute_read.assert_not_called()


@pytest.mark.parametrize(
    "database,deployment", [(DATABASE, "dev"), ("property_catalog", "prod")]
)
def test_terminal_follow_history_uses_only_exact_all_status_read(database, deployment):
    client, driver, writer = delegation_client(database, deployment)
    sql = activation_latest_rows_sql(database)
    params = {
        "organization_id": SCOPE.organization_id,
        "workspace_id": SCOPE.workspace_id,
        "catalog_epoch": 1,
    }
    assert client.query(sql, params, timeout_ms=1000) is writer.query.return_value
    writer.query.assert_called_once_with(
        sql, params, timeout_ms=1000, agreement=NativeReadAgreement.complete_result()
    )
    writer.query.reset_mock()
    for unreviewed in (
        sql + " ",
        activation_latest_rows_sql(database, through_revision=True),
    ):
        with pytest.raises(
            subject.ProductionActivationCommandError, match="non-reviewed read"
        ):
            client.query(unreviewed, params, timeout_ms=1000)
    writer.query.assert_not_called()
    driver.execute_read.assert_not_called()


@pytest.mark.parametrize(
    "mutation", ["whitespace", "comment", "cap", "foreign", "source", "other_catalog"]
)
def test_nearby_or_unreviewed_sql_never_inherits_a_contract(mutation):
    client, driver, writer = delegation_client()
    sql, params, _ = query_case("follow")
    sql = {
        "whitespace": sql + " ",
        "comment": sql + "-- reviewed-looking",
        "cap": sql.replace("LIMIT 4", "LIMIT 5"),
        "foreign": sql.replace(DATABASE, "property_catalog_dev_foreign"),
        "source": "SELECT * FROM `source`.`spans` LIMIT 4",
        "other_catalog": f"SELECT * FROM `{DATABASE}`.`property_catalog_checkpoints` LIMIT 4",
    }[mutation]
    with pytest.raises(
        subject.ProductionActivationCommandError, match="non-reviewed read"
    ):
        client.query(sql, params, timeout_ms=1000)
    writer.query.assert_not_called()
    driver.execute_read.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [("database", "foreign"), ("user", "foreign"), ("server_enforced_readonly", True)],
)
def test_identity_guard_still_precedes_agreement_delegation(field, value):
    client, driver, writer = delegation_client()
    setattr(driver, field, value)
    sql, params, _ = query_case("events")
    with pytest.raises(
        subject.ProductionActivationCommandError, match="identity changed"
    ):
        client.query(sql, params, timeout_ms=1000)
    writer.query.assert_not_called()
    driver.execute_read.assert_not_called()


@pytest.mark.parametrize("kind", INVENTORIES)
def test_real_proof_allows_benign_duplicates_and_order_returns_first_typed_rows(
    tmp_path, kind
):
    a, b = sample_rows(kind)
    left, right = [b, a, a], [a, b]
    client, proof, calls = real_client(tmp_path, kind, left, right)
    sql, params, _ = query_case(kind)
    result = client.query(sql, params, timeout_ms=1000)
    assert result == tuple(left)
    if kind == "events":
        assert result[0]["controlled_at"] is b["controlled_at"]
    assert [call[0] for call in calls] == ["replica1", "replica2"]
    assert all(call[1] == sql and call[2] == params for call in calls)
    assert proof.attest.call_count == 2  # Both writer barriers remain intact.


@pytest.mark.parametrize("kind", INVENTORIES)
@pytest.mark.parametrize("overflow_first", [False, True])
def test_raw_member_overflow_precedes_control_parsing_or_selection(
    tmp_path, monkeypatch, kind, overflow_first
):
    row = sample_rows(kind)[0]
    _, _, agreement = query_case(kind)
    assert agreement.limit is not None
    # Qualified lifetime history may have a larger SQL sentinel than the
    # proof's existing hard row bound. Keep that stricter bound; do not raise it
    # merely to reach the query sentinel in this fake native response.
    overflow_rows = min(agreement.limit, _MAX_ROWS + 1)
    left, right = (
        ([row] * overflow_rows, [row])
        if overflow_first
        else ([row], [row] * overflow_rows)
    )
    client, proof, calls = real_client(tmp_path, kind, left, right)
    parser = Mock(side_effect=AssertionError("must not select before all-member proof"))
    monkeypatch.setattr(
        control,
        "_event_from_row" if kind == "events" else "_qualified_from_row",
        parser,
    )
    member = "replica1" if overflow_first else "replica2"
    reason = (
        "catalog agreement result is incomplete"
        if agreement.limit > _MAX_ROWS
        else f"raw overflow on {member}"
    )
    with pytest.raises(NativeWriteProofError, match=reason):
        store_read(client, kind)
    parser.assert_not_called()
    assert len(calls) == (1 if overflow_first else 2)
    assert proof.attest.call_count == 1
    client._driver.execute_read.assert_not_called()


@pytest.mark.parametrize("bad", [None, True, False, 0, 1, 4096, 4098, "4097", 4097.0])
def test_control_parameter_is_required_typed_and_pinned_before_state_reads(
    tmp_path, bad
):
    row = sample_rows("events")[0]
    client, proof, calls = real_client(tmp_path, "events", [row], [row])
    sql, params, _ = query_case("events")
    params["catalog_control_result_limit"] = bad
    if bad is None:
        del params["catalog_control_result_limit"]
    with pytest.raises(NativeWriteProofError, match="limit"):
        client.query(sql, params, timeout_ms=1000)
    assert calls == []
    assert proof.attest.call_count == 1


@pytest.mark.parametrize("kind", INVENTORIES)
def test_same_members_agreement_does_not_erase_full_value_conflicts(tmp_path, kind):
    row = sample_rows(kind)[0]
    changed = {
        **row,
        "control_sha256" if kind == "events" else "latest_variants": "b" * 64
        if kind == "events"
        else 2,
    }
    client, _, _ = real_client(tmp_path, kind, [row], [changed])
    with pytest.raises(NativeWriteProofError, match="disagrees"):
        store_read(client, kind)


@pytest.mark.parametrize(
    "right", [[], [{"catalog_history_exists": 1}] * 2, [{"catalog_history_exists": 0}]]
)
def test_history_exists_stays_strict_not_duplicate_tolerant(tmp_path, right):
    client, _, _ = real_client(tmp_path, "history", sample_rows("history"), right)
    with pytest.raises(NativeWriteProofError, match="disagrees"):
        store_read(client, "history")


@pytest.mark.parametrize(
    "rows,expected", [([], False), ([{"catalog_history_exists": 1}], True)]
)
def test_history_exists_still_accepts_agreed_singleton_or_empty(
    tmp_path, rows, expected
):
    client, proof, calls = real_client(tmp_path, "history", rows, rows)
    assert store_read(client, "history") is expected
    assert len(calls) == proof.attest.call_count == 2


@pytest.mark.parametrize(
    "action",
    [control.ActivationControlAction.FOLLOW, control.ActivationControlAction.DISABLE],
)
def test_control_chain_and_disable_selection_still_run_after_agreement(
    tmp_path, action
):
    first = event(1)
    second = event(2, first.control_sha256, action=action)
    client, _, _ = real_client(
        tmp_path,
        "events",
        [second.as_row(), first.as_row(), first.as_row()],
        [first.as_row(), second.as_row()],
    )
    events = store_read(client, "events")
    assert events == (first, second)
    assert control.selected_control_target(events) == (
        None if action is control.ActivationControlAction.DISABLE else second.target
    )


def test_agreed_control_fork_still_fails_existing_chain_validation(tmp_path):
    rows = [
        event(1).as_row(),
        event(1, action=control.ActivationControlAction.DISABLE).as_row(),
    ]
    client, _, _ = real_client(tmp_path, "events", rows, list(reversed(rows)))
    with pytest.raises(
        control.ActivationControlRejected, match="control_sequence_conflict"
    ):
        store_read(client, "events")


@pytest.mark.parametrize("kind", ["qualified", "follow"])
def test_agreed_qualification_conflict_still_fails_existing_validator(tmp_path, kind):
    row = {**sample_rows(kind)[0], "latest_variants": 2}
    client, _, _ = real_client(tmp_path, kind, [row], [row])
    with pytest.raises(
        control.ActivationControlRejected, match="qualified_state_conflict"
    ):
        store_read(client, kind)


def test_unavailable_member_does_not_retry_or_fall_back_to_local_read(tmp_path):
    row = sample_rows("events")[0]
    client, proof, calls = real_client(
        tmp_path, "events", [row], TimeoutError("second member lost")
    )
    with pytest.raises(TimeoutError, match="second member lost"):
        store_read(client, "events")
    assert [call[0] for call in calls] == ["replica1", "replica2"]
    assert proof.attest.call_count == 1
    client._driver.execute_read.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
def test_legacy_nondurable_path_and_sql_are_unchanged(kind):
    driver = Driver()
    driver.execute_read = Mock(return_value=([], [("native_column", "String")], {}))
    client = adapter(driver, None)
    sql, params, _ = query_case(kind)
    assert client.query(sql, params, timeout_ms=1000) == ()
    driver.execute_read.assert_called_once_with(
        sql,
        params,
        timeout_ms=1000,
        settings={
            "max_result_rows": control.ACTIVATION_CONTROL_MAX_EVENTS + 1,
            "result_overflow_mode": "throw",
            "readonly": 2,
        },
    )
